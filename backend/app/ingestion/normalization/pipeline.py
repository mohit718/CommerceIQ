"""
Orchestrates: raw file -> import_raw_rows -> validate -> normalize ->
product mapping -> dedup/upsert -> orders/order_lines/returns or
inventory_snapshots -> import_batch status.

process_batch() also returns the set of dates it touched (sales order_dates
and returns return_dates) so the caller can trigger analytics recomputation
for exactly those dates — see app/jobs/ingestion_jobs.py and
app/jobs/analytics_jobs.py. Inventory/product imports don't affect daily
metrics, so they always return an empty set.

This module is intentionally decoupled from *how* it gets invoked — see
app/jobs/ingestion_jobs.py for the BackgroundTasks entrypoint that opens
its own DB session. process_batch() itself just takes a Session, so it's
directly unit-testable without going through HTTP or BackgroundTasks.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ingestion.csv.channel_configs import PRODUCT_IMPORT_CONFIG, resolve_channel_config
from app.ingestion.csv.parser import map_row, parse_csv_bytes
from app.ingestion.normalization.normalizers import normalize_currency, normalize_date, normalize_sku
from app.ingestion.normalization.product_mapper import resolve_product
from app.ingestion.storage import get_storage
from app.ingestion.storage.base import FileStorage
from app.models import (
    Channel,
    ChannelProduct,
    ImportBatch,
    ImportRawRow,
    InventorySnapshot,
    Order,
    OrderLine,
    Product,
    Return,
)
from app.shared.exceptions import DuplicateRowSkipped, IngestionRowError
from app.shared.logging import get_logger

logger = get_logger(__name__)


def process_batch(db: Session, batch: ImportBatch, storage: FileStorage | None = None) -> set[date]:
    """Processes every row of an import batch. Never raises for row-level
    problems — those are captured per-row on ImportRawRow. Only raises for
    batch-level failures (file unreadable, totally unparseable CSV), which
    the caller (ingestion_jobs.run_import_job) marks as batch.status =
    'failed'.

    Returns the set of dates whose daily_product_metrics/daily_channel_metrics
    need recomputing as a result of this batch (empty for inventory/products
    imports)."""
    storage = storage or get_storage()

    channel = db.query(Channel).filter(Channel.id == batch.channel_id).first()
    if not channel:
        raise IngestionRowError(f"channel_id={batch.channel_id} not found")

    raw_bytes = storage.read(batch.storage_path)
    rows = parse_csv_bytes(raw_bytes)

    if batch.import_type == "products":
        config = PRODUCT_IMPORT_CONFIG
    else:
        config = resolve_channel_config(channel.name, batch.import_type)

    column_map = config["column_map"]
    date_format = config.get("date_format")

    batch.row_count = len(rows)
    error_count = 0
    touched_dates: set[date] = set()

    for raw in rows:
        raw_row = ImportRawRow(import_batch_id=batch.id, raw_data=raw)
        db.add(raw_row)
        db.flush()

        mapped = map_row(raw, column_map)

        try:
            if batch.import_type == "sales":
                touched = _process_sales_row(db, batch, mapped, config)
                if touched:
                    touched_dates.add(touched)
            elif batch.import_type == "inventory":
                _process_inventory_row(db, batch, mapped, date_format)
            elif batch.import_type == "products":
                _process_product_row(db, batch, mapped)
            elif batch.import_type == "returns":
                touched = _process_returns_row(db, batch, mapped, date_format)
                if touched:
                    touched_dates.add(touched)
            else:
                raise IngestionRowError(f"unknown import_type: {batch.import_type}")

            raw_row.processed = True

        except DuplicateRowSkipped as e:
            # Expected/intentional — not an error, just not re-applied.
            raw_row.processed = True
            raw_row.error_message = str(e)

        except IngestionRowError as e:
            raw_row.processed = False
            raw_row.error_message = str(e)
            error_count += 1

        except Exception as e:  # noqa: BLE001 — last line of defense per row
            logger.exception("Unexpected error processing row in batch %s", batch.id)
            raw_row.processed = False
            raw_row.error_message = f"unexpected error: {e}"
            error_count += 1

    batch.error_count = error_count
    batch.status = "completed" if error_count < len(rows) or len(rows) == 0 else "failed"
    db.commit()

    return touched_dates


# --- per-row-type processors -------------------------------------------------


def _process_sales_row(db: Session, batch: ImportBatch, mapped: dict, config: dict) -> date | None:
    external_sku = mapped.get("external_sku")
    if not external_sku:
        raise IngestionRowError("missing SKU")

    product, channel_product = resolve_product(db, batch.business_id, batch.channel_id, external_sku)
    if not product:
        raise IngestionRowError(f"UNMAPPED_SKU:{normalize_sku(external_sku)}")

    external_order_id = mapped.get("external_order_id")
    if not external_order_id:
        raise IngestionRowError("missing order id")

    order_date_raw = mapped.get("order_date")
    if not order_date_raw:
        raise IngestionRowError("missing order date")
    order_date = normalize_date(order_date_raw, config.get("date_format"))

    # get-or-create order header (dedup at the order level:
    # (business_id, channel_id, external_order_id) is unique)
    order = (
        db.query(Order)
        .filter(
            Order.business_id == batch.business_id,
            Order.channel_id == batch.channel_id,
            Order.external_order_id == external_order_id,
        )
        .first()
    )
    if not order:
        order = Order(
            business_id=batch.business_id,
            channel_id=batch.channel_id,
            external_order_id=external_order_id,
            order_date=order_date,
        )
        db.add(order)
        db.flush()

    # dedup at the line level: same order + same resolved channel_product
    # means this exact row was already imported (e.g. re-uploaded file).
    existing_line = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id, OrderLine.channel_product_id == channel_product.id)
        .first()
    )
    if existing_line:
        raise DuplicateRowSkipped(
            f"duplicate row: order {external_order_id} / sku {external_sku} already imported"
        )

    quantity = int(mapped.get("quantity") or 1)
    gross = normalize_currency(mapped.get("gross_amount"))
    discount = normalize_currency(mapped.get("discount_amount"))
    shipping = normalize_currency(mapped.get("shipping_amount"))
    tax = normalize_currency(mapped.get("tax_amount"))

    if mapped.get("fee_amount") is not None:
        fee = normalize_currency(mapped.get("fee_amount"))
    elif "fee_pct_of_gross" in config:
        fee = (gross * Decimal(str(config["fee_pct_of_gross"]))).quantize(Decimal("0.01"))
    else:
        fee = Decimal("0")

    line = OrderLine(
        business_id=batch.business_id,
        order_id=order.id,
        product_id=product.id,
        channel_product_id=channel_product.id,
        quantity=quantity,
        gross_amount=gross,
        discount_amount=discount,
        fee_amount=fee,
        shipping_amount=shipping,
        tax_amount=tax,
    )
    db.add(line)

    return order_date


def _process_inventory_row(db: Session, batch: ImportBatch, mapped: dict, date_format: str | None) -> None:
    external_sku = mapped.get("external_sku")
    if not external_sku:
        raise IngestionRowError("missing SKU")

    product, _ = resolve_product(db, batch.business_id, batch.channel_id, external_sku)
    if not product:
        raise IngestionRowError(f"UNMAPPED_SKU:{normalize_sku(external_sku)}")

    snapshot_date_raw = mapped.get("snapshot_date")
    if not snapshot_date_raw:
        raise IngestionRowError("missing snapshot date")
    snapshot_date = normalize_date(snapshot_date_raw, date_format)

    quantity_raw = mapped.get("quantity_on_hand")
    if quantity_raw is None:
        raise IngestionRowError("missing quantity_on_hand")
    quantity = int(float(quantity_raw))  # tolerate '120.0'-style values

    # Upsert semantics: re-uploading corrected inventory for the same date
    # should overwrite, not duplicate.
    existing = (
        db.query(InventorySnapshot)
        .filter(
            InventorySnapshot.business_id == batch.business_id,
            InventorySnapshot.product_id == product.id,
            InventorySnapshot.channel_id == batch.channel_id,
            InventorySnapshot.snapshot_date == snapshot_date,
        )
        .first()
    )
    if existing:
        existing.quantity_on_hand = quantity
    else:
        db.add(
            InventorySnapshot(
                business_id=batch.business_id,
                product_id=product.id,
                channel_id=batch.channel_id,
                snapshot_date=snapshot_date,
                quantity_on_hand=quantity,
            )
        )


def _process_product_row(db: Session, batch: ImportBatch, mapped: dict) -> None:
    sku = normalize_sku(mapped.get("sku"))
    if not sku:
        raise IngestionRowError("missing sku")

    product = db.query(Product).filter(Product.business_id == batch.business_id, Product.sku == sku).first()

    if not product:
        product = Product(
            business_id=batch.business_id,
            sku=sku,
            name=mapped.get("name") or sku,
            brand=mapped.get("brand"),
            category=mapped.get("category"),
            cost_price=normalize_currency(mapped.get("cost_price")) or None,
            selling_price=normalize_currency(mapped.get("selling_price")) or None,
        )
        db.add(product)
        db.flush()
    else:
        # Update mutable fields if the re-uploaded row provides them —
        # doesn't touch fields left blank in this row.
        if mapped.get("name"):
            product.name = mapped["name"]
        if mapped.get("brand"):
            product.brand = mapped["brand"]
        if mapped.get("category"):
            product.category = mapped["category"]
        if mapped.get("cost_price"):
            product.cost_price = normalize_currency(mapped["cost_price"])
        if mapped.get("selling_price"):
            product.selling_price = normalize_currency(mapped["selling_price"])

    external_sku = mapped.get("external_sku") or sku

    existing_mapping = (
        db.query(ChannelProduct)
        .filter(
            ChannelProduct.business_id == batch.business_id,
            ChannelProduct.channel_id == batch.channel_id,
            ChannelProduct.external_sku == external_sku,
        )
        .first()
    )
    if not existing_mapping:
        db.add(
            ChannelProduct(
                business_id=batch.business_id,
                product_id=product.id,
                channel_id=batch.channel_id,
                external_sku=external_sku,
                external_name=mapped.get("name"),
                mapping_method="exact",
            )
        )


def _process_returns_row(db: Session, batch: ImportBatch, mapped: dict, date_format: str | None) -> date | None:
    external_sku = mapped.get("external_sku")
    if not external_sku:
        raise IngestionRowError("missing SKU")

    product, channel_product = resolve_product(db, batch.business_id, batch.channel_id, external_sku)
    if not product:
        raise IngestionRowError(f"UNMAPPED_SKU:{normalize_sku(external_sku)}")

    external_order_id = mapped.get("external_order_id")
    if not external_order_id:
        raise IngestionRowError("missing order id")

    order = (
        db.query(Order)
        .filter(
            Order.business_id == batch.business_id,
            Order.channel_id == batch.channel_id,
            Order.external_order_id == external_order_id,
        )
        .first()
    )
    if not order:
        raise IngestionRowError(f"no matching order for return: {external_order_id}")

    order_line = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id, OrderLine.channel_product_id == channel_product.id)
        .first()
    )
    if not order_line:
        raise IngestionRowError(
            f"no matching order line for return: order {external_order_id} / sku {external_sku}"
        )

    return_date_raw = mapped.get("return_date")
    if not return_date_raw:
        raise IngestionRowError("missing return date")
    return_date_value = normalize_date(return_date_raw, date_format)

    # dedup: the same order_line returned again on the same return_date is
    # treated as a re-upload of the same return event, not a second return.
    existing_return = (
        db.query(Return)
        .filter(Return.order_line_id == order_line.id, Return.return_date == return_date_value)
        .first()
    )
    if existing_return:
        raise DuplicateRowSkipped(
            f"duplicate return: order {external_order_id} / sku {external_sku} on {return_date_value}"
        )

    quantity = int(mapped.get("quantity") or order_line.quantity)

    refund_amount_raw = mapped.get("refund_amount")
    if refund_amount_raw:
        refund_amount = normalize_currency(refund_amount_raw)
    else:
        # No refund amount given — fall back to the net amount paid on the
        # original line (gross - discount). A simplification: doesn't
        # prorate for partial-quantity returns on a multi-unit line.
        refund_amount = order_line.gross_amount - order_line.discount_amount

    db.add(
        Return(
            business_id=batch.business_id,
            order_line_id=order_line.id,
            quantity=quantity,
            return_date=return_date_value,
            reason=mapped.get("reason"),
            refund_amount=refund_amount,
        )
    )

    return return_date_value
