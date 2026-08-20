"""
Tests app/modules/inventory/service.py directly against the ORM, hand-built
scenarios with a FIXED as_of_date (not date.today()) so aging math is
deterministic and reproducible on any run date.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models import Business, Channel, DailyProductMetric, InventorySnapshot, Order, OrderLine, Product
from app.modules.inventory import service

AS_OF = date(2026, 8, 20)


@pytest.fixture()
def base(db_session):
    business = Business(name="Inventory Test Co")
    db_session.add(business)
    db_session.flush()

    channel = Channel(business_id=business.id, name="Amazon", type="marketplace")
    db_session.add(channel)
    db_session.flush()

    return {"business": business, "channel": channel}


def _make_product(db_session, business, sku, cost_price="450"):
    product = Product(business_id=business.id, sku=sku, name=f"Product {sku}", cost_price=Decimal(cost_price))
    db_session.add(product)
    db_session.flush()
    return product


def _snapshot(db_session, business, product, channel, snap_date, qty):
    db_session.add(
        InventorySnapshot(
            business_id=business.id, product_id=product.id, channel_id=channel.id,
            snapshot_date=snap_date, quantity_on_hand=qty,
        )
    )


def _daily_metric(db_session, business, product, channel, d, units_sold):
    """Directly seeds daily_product_metrics — bypasses the Phase 3
    aggregation job since these tests only need velocity/last-sale signals,
    not full order_line/return correctness (that's test_analytics.py's job)."""
    db_session.add(
        DailyProductMetric(
            business_id=business.id, product_id=product.id, channel_id=channel.id, date=d,
            units_sold=units_sold, gross_revenue=Decimal("0"), net_revenue=Decimal("0"),
            discount_amount=Decimal("0"), fee_amount=Decimal("0"), returns_amount=Decimal("0"),
            contribution_profit=Decimal("0"), realized_price=Decimal("0"),
        )
    )


# --- the specific case from the user's question -----------------------------


def test_never_sold_product_ages_from_first_seen_date(db_session, base):
    """A product that has NEVER sold should still be flagged slow_moving
    once enough time has passed since it was first seen in an inventory
    snapshot — not silently stay 'healthy' forever just because there's no
    last_sale_date to anchor on."""
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "NEVER-SOLD-001")

    first_seen = date(2026, 6, 10)  # 71 days before AS_OF
    _snapshot(db_session, business, product, channel, first_seen, qty=200)
    _snapshot(db_session, business, product, channel, date(2026, 7, 1), qty=200)  # still there, unchanged
    db_session.flush()

    rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF)
    assert len(rows) == 1
    row = rows[0]

    assert row["last_sale_date"] is None
    assert row["first_seen_date"] == first_seen
    assert row["aging_basis"] == "first_seen"
    assert row["aging_days"] == (AS_OF - first_seen).days  # 71
    assert row["status"] == "slow_moving"


def test_new_never_sold_product_is_not_flagged_prematurely(db_session, base):
    """The flip side: a product first seen only a few days ago shouldn't
    be flagged slow_moving just because it hasn't sold yet — it hasn't
    had time to."""
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "NEW-ARRIVAL-001")

    _snapshot(db_session, business, product, channel, date(2026, 8, 18), qty=50)  # 2 days before AS_OF
    db_session.flush()

    rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF)
    row = rows[0]

    assert row["aging_basis"] == "first_seen"
    assert row["aging_days"] == 2
    assert row["status"] == "healthy"


# --- other status classifications -------------------------------------------


def test_product_with_recent_sales_uses_last_sale_basis_and_is_healthy(db_session, base):
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "HEALTHY-001")

    _snapshot(db_session, business, product, channel, date(2026, 8, 15), qty=100)
    # 2 units/day across the FULL 30-day velocity window (not just 10 days —
    # partial-window sales dilute the average and can misclassify as overstock)
    for i in range(30):
        _daily_metric(
            db_session, business, product, channel,
            AS_OF - __import__("datetime").timedelta(days=i), units_sold=2,
        )
    db_session.flush()

    rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF)
    row = rows[0]

    assert row["aging_basis"] == "last_sale"
    assert row["last_sale_date"] == AS_OF  # most recent day in the loop (i=0)
    # coverage = 100 stock / 2 per day = 50 days -> between stockout (14) and overstock (90)
    assert row["coverage_days"] == Decimal("50.0")
    assert row["status"] == "healthy"


def test_out_of_stock(db_session, base):
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "OOS-001")
    _snapshot(db_session, business, product, channel, date(2026, 8, 19), qty=0)
    db_session.flush()

    rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF)
    assert rows[0]["status"] == "out_of_stock"


def test_stockout_risk_and_reorder_qty(db_session, base):
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "LOWSTOCK-001")

    _snapshot(db_session, business, product, channel, date(2026, 8, 19), qty=20)
    # velocity: 2 units/day average over a 30 day window (60 units total)
    for i in range(30):
        _daily_metric(db_session, business, product, channel, AS_OF - __import__("datetime").timedelta(days=i), units_sold=2)
    db_session.flush()

    rows = service.compute_inventory_status(
        db_session, business.id, as_of_date=AS_OF, stockout_threshold_days=14, target_coverage_days=30,
    )
    row = rows[0]

    # coverage = 20 stock / 2 per day = 10 days <= 14 threshold
    assert row["coverage_days"] == Decimal("10.0")
    assert row["status"] == "stockout_risk"
    # reorder target: (2 * 30) - 20 = 40
    assert row["reorder_qty"] == 40


def test_overstock(db_session, base):
    business, channel = base["business"], base["channel"]
    product = _make_product(db_session, business, "OVERSTOCK-001")

    _snapshot(db_session, business, product, channel, date(2026, 8, 19), qty=1000)
    # low but nonzero velocity: 1 unit/day over a 30-day window -> coverage = 1000 days
    for i in range(5):
        _daily_metric(db_session, business, product, channel, AS_OF - __import__("datetime").timedelta(days=i), units_sold=1)
    db_session.flush()

    rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF, overstock_threshold_days=90)
    row = rows[0]

    assert row["status"] == "overstock"
    assert row["reorder_qty"] is None  # overstocked items don't get a reorder suggestion


# --- pooled vs channel-scoped -------------------------------------------------


def test_pooled_mode_sums_stock_across_channels(db_session, base):
    business = base["business"]
    amazon = base["channel"]
    offline = Channel(business_id=business.id, name="Offline Store", type="offline")
    db_session.add(offline)
    db_session.flush()

    product = _make_product(db_session, business, "MULTI-CHANNEL-001")
    _snapshot(db_session, business, product, amazon, date(2026, 8, 10), qty=100)
    _snapshot(db_session, business, product, offline, date(2026, 8, 15), qty=50)
    db_session.flush()

    pooled_rows = service.compute_inventory_status(db_session, business.id, as_of_date=AS_OF)
    assert len(pooled_rows) == 1
    assert pooled_rows[0]["latest_stock"] == 150  # 100 + 50
    assert pooled_rows[0]["channel_id"] is None
    assert pooled_rows[0]["snapshot_date"] == date(2026, 8, 15)  # freshest of the two

    amazon_only_rows = service.compute_inventory_status(db_session, business.id, channel_id=amazon.id, as_of_date=AS_OF)
    assert len(amazon_only_rows) == 1
    assert amazon_only_rows[0]["latest_stock"] == 100
    assert amazon_only_rows[0]["channel_id"] == amazon.id


# --- overview aggregation -----------------------------------------------------


def test_overview_counts_and_values(db_session, base):
    business, channel = base["business"], base["channel"]

    oos = _make_product(db_session, business, "OV-OOS", cost_price="100")
    _snapshot(db_session, business, oos, channel, date(2026, 8, 19), qty=0)

    slow = _make_product(db_session, business, "OV-SLOW", cost_price="200")
    _snapshot(db_session, business, slow, channel, date(2026, 6, 1), qty=10)  # first seen, never sold -> slow_moving

    db_session.flush()

    result = service.get_inventory_overview(db_session, business.id, as_of_date=AS_OF)

    assert result["out_of_stock_count"] == 1
    assert result["slow_moving_count"] == 1
    assert result["slow_moving_locked_value"] == Decimal("2000")  # 10 * 200
    assert result["total_inventory_value"] == Decimal("2000")  # oos contributes 0 * 100 = 0
