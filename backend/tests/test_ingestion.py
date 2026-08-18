"""
These tests call process_batch() directly with the test's db_session,
rather than going through the HTTP upload endpoint + BackgroundTasks. The
background job (app/jobs/ingestion_jobs.py) opens its own SessionLocal()
bound to the *production* DATABASE_URL — that's correct for real usage,
but means it can't be exercised against the test's in-memory SQLite session
without reworking session wiring. Testing process_batch() directly still
covers all the actual ingestion logic; it just skips FastAPI's own
(separately-tested) BackgroundTasks mechanism.
"""
from pathlib import Path

import pytest

from app.ingestion.normalization.pipeline import process_batch
from app.ingestion.storage.local import LocalFileStorage
from app.models import Channel, ImportBatch, InventorySnapshot, Order, OrderLine, Product


@pytest.fixture()
def storage(tmp_path):
    return LocalFileStorage(base_dir=str(tmp_path))


@pytest.fixture()
def business_and_channel(db_session):
    """Minimal business + Amazon channel, created directly via the ORM
    (bypassing the API) since these tests focus on the ingestion pipeline,
    not auth/tenancy — that's covered in test_health.py."""
    from app.models import Business

    business = Business(name="Test Seller")
    db_session.add(business)
    db_session.flush()

    channel = Channel(business_id=business.id, name="Amazon", type="marketplace")
    db_session.add(channel)
    db_session.flush()

    return business, channel


def _make_batch(db_session, business, channel, storage, csv_bytes, import_type, filename):
    storage_path = storage.save(business.id, filename, csv_bytes)
    batch = ImportBatch(
        business_id=business.id,
        channel_id=channel.id,
        file_name=filename,
        storage_path=storage_path,
        import_type=import_type,
        status="pending",
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def test_product_import_creates_products_and_mapping(db_session, business_and_channel, storage):
    business, channel = business_and_channel
    csv_bytes = (
        b"Master SKU,Channel SKU,Product Name,Brand,Category,Cost Price,Selling Price\n"
        b"AD141-BLK,B08XYZ141,Airdopes 141 Black,boAt,Audio,450,1299\n"
    )
    batch = _make_batch(db_session, business, channel, storage, csv_bytes, "products", "products.csv")

    process_batch(db_session, batch, storage=storage)

    product = db_session.query(Product).filter(Product.business_id == business.id).first()
    assert product is not None
    assert product.sku == "AD141-BLK"
    assert product.name == "Airdopes 141 Black"
    assert batch.status == "completed"
    assert batch.error_count == 0


def test_sales_row_with_unmapped_sku_is_recorded_as_error(db_session, business_and_channel, storage):
    business, channel = business_and_channel
    csv_bytes = (
        b"order-id,sku,purchase-date,quantity,item-price,item-promotion-discount,"
        b"commission,shipping-price,item-tax\n"
        b"402-0001,B08UNKNOWN,2026-08-01,1,999,0,60,0,45\n"
    )
    batch = _make_batch(db_session, business, channel, storage, csv_bytes, "sales", "sales.csv")

    process_batch(db_session, batch, storage=storage)

    assert batch.error_count == 1
    assert db_session.query(Order).count() == 0

    raw_row = batch.raw_rows[0]
    assert raw_row.processed is False
    assert raw_row.error_message.startswith("UNMAPPED_SKU:")


def test_sales_row_resolves_mapping_and_creates_order_line(db_session, business_and_channel, storage):
    business, channel = business_and_channel

    # seed the product catalog first
    products_csv = (
        b"Master SKU,Channel SKU,Product Name,Brand,Category,Cost Price,Selling Price\n"
        b"AD141-BLK,B08XYZ141,Airdopes 141 Black,boAt,Audio,450,1299\n"
    )
    product_batch = _make_batch(db_session, business, channel, storage, products_csv, "products", "p.csv")
    process_batch(db_session, product_batch, storage=storage)

    sales_csv = (
        b"order-id,sku,purchase-date,quantity,item-price,item-promotion-discount,"
        b"commission,shipping-price,item-tax\n"
        b"402-0002,B08XYZ141,2026-08-01,2,2598,260,180,0,130\n"
    )
    sales_batch = _make_batch(db_session, business, channel, storage, sales_csv, "sales", "s.csv")
    process_batch(db_session, sales_batch, storage=storage)

    assert sales_batch.error_count == 0
    order = db_session.query(Order).filter(Order.external_order_id == "402-0002").first()
    assert order is not None
    line = db_session.query(OrderLine).filter(OrderLine.order_id == order.id).first()
    assert line.quantity == 2
    from decimal import Decimal
    assert line.gross_amount == Decimal("2598")
    assert line.discount_amount == Decimal("260")


def test_reuploading_same_sales_file_is_deduped_not_duplicated(db_session, business_and_channel, storage):
    business, channel = business_and_channel

    products_csv = (
        b"Master SKU,Channel SKU,Product Name,Brand,Category,Cost Price,Selling Price\n"
        b"AD141-BLK,B08XYZ141,Airdopes 141 Black,boAt,Audio,450,1299\n"
    )
    pb = _make_batch(db_session, business, channel, storage, products_csv, "products", "p.csv")
    process_batch(db_session, pb, storage=storage)

    sales_csv = (
        b"order-id,sku,purchase-date,quantity,item-price,item-promotion-discount,"
        b"commission,shipping-price,item-tax\n"
        b"402-0003,B08XYZ141,2026-08-01,1,1299,0,90,0,65\n"
    )
    batch1 = _make_batch(db_session, business, channel, storage, sales_csv, "sales", "s1.csv")
    process_batch(db_session, batch1, storage=storage)
    assert db_session.query(OrderLine).count() == 1

    # re-upload the identical file
    batch2 = _make_batch(db_session, business, channel, storage, sales_csv, "sales", "s2.csv")
    process_batch(db_session, batch2, storage=storage)

    # still just 1 order line — duplicate was skipped, not treated as error
    assert db_session.query(OrderLine).count() == 1
    assert batch2.error_count == 0
    assert batch2.raw_rows[0].processed is True
    assert "duplicate" in batch2.raw_rows[0].error_message.lower()


def test_inventory_snapshot_upserts_on_same_date(db_session, business_and_channel, storage):
    business, channel = business_and_channel

    products_csv = (
        b"Master SKU,Channel SKU,Product Name,Brand,Category,Cost Price,Selling Price\n"
        b"AD141-BLK,B08XYZ141,Airdopes 141 Black,boAt,Audio,450,1299\n"
    )
    pb = _make_batch(db_session, business, channel, storage, products_csv, "products", "p.csv")
    process_batch(db_session, pb, storage=storage)

    inv_csv_1 = b"sku,date,quantity-available\nB08XYZ141,2026-08-01,480\n"
    batch1 = _make_batch(db_session, business, channel, storage, inv_csv_1, "inventory", "inv1.csv")
    process_batch(db_session, batch1, storage=storage)

    assert db_session.query(InventorySnapshot).count() == 1
    snapshot = db_session.query(InventorySnapshot).first()
    assert snapshot.quantity_on_hand == 480

    # corrected inventory for the SAME date should overwrite, not duplicate
    inv_csv_2 = b"sku,date,quantity-available\nB08XYZ141,2026-08-01,465\n"
    batch2 = _make_batch(db_session, business, channel, storage, inv_csv_2, "inventory", "inv2.csv")
    process_batch(db_session, batch2, storage=storage)

    assert db_session.query(InventorySnapshot).count() == 1  # still just one row
    db_session.refresh(snapshot)
    assert snapshot.quantity_on_hand == 465  # overwritten
