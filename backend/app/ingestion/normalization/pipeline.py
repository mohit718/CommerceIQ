"""
Orchestrates: raw file -> import_raw_rows -> validate -> normalize ->
product mapping -> dedup/upsert -> orders/order_lines or
inventory_snapshots -> import_batch status.

This module is intentionally decoupled from *how* it gets invoked — see
app/jobs/ingestion_jobs.py for the BackgroundTasks entrypoint that opens
its own DB session. process_batch() itself just takes a Session, so it's
directly unit-testable without going through HTTP or BackgroundTasks.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ingestion.csv.channel_configs import PRODUCT_IMPORT_CONFIG, resolve_channel_config
from app.ingestion.csv.parser import map_row, parse_csv_bytes
from app.ingestion.normalization.normalizers import normalize_currency, normalize_date, normalize_sku
from app.ingestion.normalization.product_mapper import resolve_product
from app.ingestion.storage import get_storage
from app.ingestion.storage.base import FileStorage
from app.models import Channel, ImportBatch, ImportRawRow, InventorySnapshot, Order, OrderLine, Product
from app.shared.exceptions import DuplicateRowSkipped, IngestionRowError
from app.shared.logging import get_logger

logger = get_logger(__name__)


def process_batch(db: Session, batch: ImportBatch, storage: FileStorage | None = None) -> None:
    """Processes every row of an import batch. Never raises for row-level
    problems — those are captured per-row on ImportRawRow. Only raises for
    batch-level failures (file unreadable, totally unparseable CSV), which
    the caller (ingestion_jobs.run_import_job) marks as batch.status =
    'failed'."""
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

    for raw in rows:
        raw_row = ImportRawRow(import_batch_id=batch.id, raw_data=raw)
        db.add(raw_row)
        db.flush()  # need raw_row.id-less insert is fine; flush just persists it in-session

        mapped = map_row(raw, column_map)

        try:
            if batch.import_type == "sales":
                _process_sales_row(db, batch, mapped, config)
            elif batch.import_type == "inventory":
                _process_inventory_row(db, batch, mapped, date_format)
            elif batch.import_type == "products":
                _process_product_row(db, batch, mapped)
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


# --- per-row-type processors -------------------------------------------------


def _process_sales_row(db: Session, batch: ImportBatch, mapped: dict, config: dict) -> None:
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

    # get-or-create order header (dedup at the order level — Section on
    # dedup: (business_id, channel_id, external_order_id) is unique)
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
        db.flush()  # need order.id for the line below

    # dedup at the line level: same order + same resolved channel_product
    # means this exact row was already imported (e.g. re-uploaded file).
    existing_line = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id, OrderLine.channel_product_id == channel_product.id)
        .first()
    )
    if existing_line:
        raise DuplicateRowSkipped(f"duplicate row: order {external_order_id} / sku {external_sku} already imported")

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
    from app.models import ChannelProduct

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
