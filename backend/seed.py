"""Simple CSV seed for a fresh CommerceIQ database.

Run from backend/:
    python seed.py

Optional:
    python seed.py --reset
    python seed.py --reset --verbose
    python seed.py --data-dir sample_data/csv

The script creates one demo business, one owner user, four channels, then
imports products -> sales -> returns -> inventory for every channel.
"""
from __future__ import annotations

import argparse
import logging
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func

from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.ingestion.csv.channel_configs import PRODUCT_IMPORT_CONFIG, resolve_channel_config
from app.ingestion.normalization.normalizers import normalize_currency, normalize_date, normalize_sku
from app.ingestion.normalization.product_mapper import resolve_product
from app.models import (
    Business,
    Channel,
    ChannelProduct,
    ImportBatch,
    ImportRawRow,
    InventorySnapshot,
    Order,
    OrderLine,
    Product,
    Return,
    User,
)
from app.jobs.analytics_jobs import recompute_metrics_for_dates
from app.schemas.analytics import RecomputeRequest

EMAIL = "owner@commerceiq.demo"
PASSWORD = "Demo@12345"

CHANNELS = {
    "amazon": ("Amazon", "marketplace"),
    "flipkart": ("Flipkart", "marketplace"),
    "shopify": ("Shopify", "d2c"),
    "offline": ("Offline", "offline"),
}

RETURN_COLUMNS = {
    "amazon": {
        "return_id": "return-id", "order_id": "order-id", "sku": "sku",
        "date": "return-date", "quantity": "quantity", "reason": "reason",
        "refund": "refund-amount",
    },
    "flipkart": {
        "return_id": "Return Id", "order_id": "Order Id", "sku": "SKU",
        "date": "Return Date", "quantity": "Quantity", "reason": "Return Reason",
        "refund": "Refund Amount",
    },
    "shopify": {
        "return_id": "Return ID", "order_id": "Order Name", "sku": "Lineitem sku",
        "date": "Return Date", "quantity": "Return Quantity", "reason": "Return Reason",
        "refund": "Refund Amount",
    },
    "offline": {
        "return_id": "Return No", "order_id": "Bill No", "sku": "Item Code",
        "date": "Return Date", "quantity": "Qty", "reason": "Reason",
        "refund": "Refund Amount",
    },
}

logger = logging.getLogger("seed")

def setup_logger(verbose: bool = False):
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

def get_dates(start_date, end_date):
    dates: set[date] = set()
    d = start_date
    while d <= end_date:
        dates.add(d)
        d += timedelta(days=1)
    return dates

def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def reset_database(db):
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(delete(table))
    db.commit()


def make_batch(db, business_id, channel_id, path: Path, import_type: str, rows: list[dict]):
    batch = ImportBatch(
        business_id=business_id,
        channel_id=channel_id,
        file_name=path.name,
        storage_path=str(path),
        import_type=import_type,
        status="processing",
        row_count=len(rows),
        error_count=0,
    )
    db.add(batch)
    db.flush()
    for row in rows:
        db.add(ImportRawRow(import_batch_id=batch.id, raw_data=row, processed=False))
    db.flush()
    return batch


def import_products(db, business, channel, path: Path):
    rows = read_csv(path)
    batch = make_batch(db, business.id, channel.id, path, "products", rows)

    for raw in rows:
        sku = normalize_sku(raw["Master SKU"])
        product = (
            db.query(Product)
            .filter(Product.business_id == business.id, Product.sku == sku)
            .first()
        )
        if not product:
            product = Product(
                business_id=business.id,
                sku=sku,
                name=raw.get("Product Name") or sku,
                brand=raw.get("Brand") or None,
                category=raw.get("Category") or None,
                cost_price=normalize_currency(raw.get("Cost Price")) or None,
                selling_price=normalize_currency(raw.get("Selling Price")) or None,
            )
            db.add(product)
            db.flush()

        external_sku = (raw.get("Channel SKU") or sku).strip()
        mapping = (
            db.query(ChannelProduct)
            .filter(
                ChannelProduct.business_id == business.id,
                ChannelProduct.channel_id == channel.id,
                ChannelProduct.external_sku == external_sku,
            )
            .first()
        )
        if not mapping:
            db.add(ChannelProduct(
                business_id=business.id,
                product_id=product.id,
                channel_id=channel.id,
                external_sku=external_sku,
                external_name=raw.get("Product Name"),
                mapping_method="exact",
            ))

    batch.status = "completed"
    db.flush()


def import_sales(db, business, channel, path: Path):
    rows = read_csv(path)
    batch = make_batch(db, business.id, channel.id, path, "sales", rows)
    config = resolve_channel_config(channel.name, "sales")
    cmap = config["column_map"]
    date_format = config.get("date_format")

    for raw in rows:
        external_order_id = raw.get(cmap["external_order_id"])
        external_sku = raw.get(cmap["external_sku"])
        if not external_order_id or not external_sku:
            batch.error_count += 1
            continue

        product, channel_product = resolve_product(
            db, business.id, channel.id, external_sku
        )
        if not product:
            batch.error_count += 1
            continue

        order_date = normalize_date(raw[cmap["order_date"]], date_format)
        order = (
            db.query(Order)
            .filter(
                Order.business_id == business.id,
                Order.channel_id == channel.id,
                Order.external_order_id == external_order_id,
            )
            .first()
        )
        if not order:
            order = Order(
                business_id=business.id,
                channel_id=channel.id,
                external_order_id=external_order_id,
                order_date=order_date,
            )
            db.add(order)
            db.flush()

        existing = (
            db.query(OrderLine)
            .filter(
                OrderLine.order_id == order.id,
                OrderLine.channel_product_id == channel_product.id,
            )
            .first()
        )
        if existing:
            continue

        gross = normalize_currency(raw.get(cmap.get("gross_amount")))
        discount = normalize_currency(raw.get(cmap.get("discount_amount")))
        shipping = normalize_currency(raw.get(cmap.get("shipping_amount")))
        tax = normalize_currency(raw.get(cmap.get("tax_amount")))
        fee_col = cmap.get("fee_amount")
        if fee_col:
            fee = normalize_currency(raw.get(fee_col))
        else:
            fee = (gross * Decimal(str(config.get("fee_pct_of_gross", 0)))).quantize(Decimal("0.01"))

        db.add(OrderLine(
            business_id=business.id,
            order_id=order.id,
            product_id=product.id,
            channel_product_id=channel_product.id,
            quantity=int(float(raw[cmap["quantity"]])),
            gross_amount=gross,
            discount_amount=discount,
            fee_amount=fee,
            shipping_amount=shipping,
            tax_amount=tax,
        ))

    batch.status = "completed" if batch.error_count < batch.row_count else "failed"
    db.flush()


def import_returns(db, business, channel, path: Path):
    rows = read_csv(path)
    batch = make_batch(db, business.id, channel.id, path, "returns", rows)
    cols = RETURN_COLUMNS[channel.name.lower()]

    for raw in rows:
        order_id = raw.get(cols["order_id"])
        external_sku = raw.get(cols["sku"])
        if not order_id or not external_sku:
            batch.error_count += 1
            continue

        order = (
            db.query(Order)
            .filter(
                Order.business_id == business.id,
                Order.channel_id == channel.id,
                Order.external_order_id == order_id,
            )
            .first()
        )
        if not order:
            batch.error_count += 1
            continue

        product, channel_product = resolve_product(
            db, business.id, channel.id, external_sku
        )
        if not channel_product:
            batch.error_count += 1
            continue

        line = (
            db.query(OrderLine)
            .filter(
                OrderLine.order_id == order.id,
                OrderLine.channel_product_id == channel_product.id,
            )
            .first()
        )
        if not line:
            batch.error_count += 1
            continue

        # Return IDs are retained only as raw CSV data; the DB Return model
        # links the return to the originating order line.
        db.add(Return(
            business_id=business.id,
            order_line_id=line.id,
            quantity=int(float(raw[cols["quantity"]])),
            return_date=normalize_date(raw[cols["date"]], _return_date_format(channel.name.lower())),
            reason=raw.get(cols["reason"]) or None,
            refund_amount=normalize_currency(raw[cols["refund"]]),
        ))

    batch.status = "completed" if batch.error_count < batch.row_count else "failed"
    db.flush()


def _return_date_format(channel: str):
    return {
        "amazon": "%Y-%m-%d",
        "flipkart": "%d-%m-%Y",
        "shopify": "%Y-%m-%d",
        "offline": "%d/%m/%Y",
    }[channel]


def import_inventory(db, business, channel, path: Path):
    rows = read_csv(path)
    batch = make_batch(db, business.id, channel.id, path, "inventory", rows)
    config = resolve_channel_config(channel.name, "inventory")
    cmap = config["column_map"]
    date_format = config.get("date_format")

    for raw in rows:
        external_sku = raw.get(cmap["external_sku"])
        product, _ = resolve_product(db, business.id, channel.id, external_sku)
        if not product:
            batch.error_count += 1
            continue

        snapshot_date = normalize_date(raw[cmap["snapshot_date"]], date_format)
        qty = int(float(raw[cmap["quantity_on_hand"]]))
        existing = (
            db.query(InventorySnapshot)
            .filter(
                InventorySnapshot.business_id == business.id,
                InventorySnapshot.product_id == product.id,
                InventorySnapshot.channel_id == channel.id,
                InventorySnapshot.snapshot_date == snapshot_date,
            )
            .first()
        )
        if existing:
            existing.quantity_on_hand = qty
        else:
            db.add(InventorySnapshot(
                business_id=business.id,
                product_id=product.id,
                channel_id=channel.id,
                snapshot_date=snapshot_date,
                quantity_on_hand=qty,
            ))

    batch.status = "completed" if batch.error_count < batch.row_count else "failed"
    db.flush()


def get_global_date_range(db):
    min_date, max_date = db.query(
        func.min(Order.order_date),
        func.max(Order.order_date)
    ).one()
    return min_date, max_date


def compute_metrics(db, business_id):
    min_date, max_date = get_global_date_range(db)
    dates = get_dates(min_date, max_date)
    recompute_metrics_for_dates(db, business_id, dates)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Clear all tables first")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed seed progress logs",
    )
    parser.add_argument("--data-dir", default="sample_data/csv")
    args = parser.parse_args()

    setup_logger(args.verbose)

    data_dir = Path(args.data_dir).resolve()
    required = []
    for key in CHANNELS:
        required += [
            data_dir / f"{key}_products_120.csv",
            data_dir / f"{key}_sales_400.csv",
            data_dir / f"{key}_returns_120.csv",
            data_dir / f"{key}_inventory_240.csv",
        ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing CSV files:\n" + "\n".join(missing))

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.reset:
            reset_database(db)

        if db.query(Business).first():
            raise RuntimeError("Database is not empty. Use --reset for a fresh seed.")

        logger.info("Starting database seed...")

        business = Business(name="CommerceIQ Demo Store")
        db.add(business)
        db.flush()

        logger.info("Business created.")

        db.add(User(
            business_id=business.id,
            email=EMAIL,
            hashed_password=hash_password(PASSWORD),
            role="owner",
        ))

        logger.info("User created.")

        channels = {}
        for key, (name, channel_type) in CHANNELS.items():
            channel = Channel(business_id=business.id, name=name, type=channel_type)
            db.add(channel)
            channels[key] = channel
        db.flush()

        logger.info(f"Channels created: {','.join(CHANNELS.keys())}.")

        # Products must be imported before sales, returns, and inventory.
        for key, channel in channels.items():
            import_products(db, business, channel, data_dir / f"{key}_products_120.csv")
        db.commit()

        logger.info(f"Products imported.")

        for key, channel in channels.items():
            import_sales(db, business, channel, data_dir / f"{key}_sales_400.csv")
        db.commit()

        logger.info(f"Sales records imported.")

        for key, channel in channels.items():
            import_returns(db, business, channel, data_dir / f"{key}_returns_120.csv")
        db.commit()

        logger.info(f"Return records imported.")

        for key, channel in channels.items():
            import_inventory(db, business, channel, data_dir / f"{key}_inventory_240.csv")
        db.commit()

        logger.info(f"Inventory snapshot imported.")

        compute_metrics(db, business.id)

        logger.info(f"Analytics metrics computed.")
        
        print("\nSeed complete.")
        print(f'Login: {EMAIL} / {PASSWORD}')
        for model in (Business, User, Channel, Product, ChannelProduct, Order, OrderLine, Return, InventorySnapshot, ImportBatch, ImportRawRow):
            print(f"{model.__tablename__:22} {db.query(model).count()}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    main()
