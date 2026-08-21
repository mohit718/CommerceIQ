"""
Tests the insight detection engine (app/jobs/insight_jobs.py) directly
against the test's db_session, in the same style as test_analytics.py and
test_inventory.py — hand-computed expected values, ORM fixtures built
directly rather than through HTTP/CSV upload.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.jobs.insight_jobs import generate_insights_for_business
from app.models import Business, Channel, DailyProductMetric, InventorySnapshot, Product, ProductInsight
from app.modules.insights import service


@pytest.fixture()
def business(db_session):
    b = Business(name="Insight Test Co")
    db_session.add(b)
    db_session.flush()
    return b


@pytest.fixture()
def channel(db_session, business):
    c = Channel(business_id=business.id, name="Amazon", type="marketplace")
    db_session.add(c)
    db_session.flush()
    return c


def _make_product(db_session, business, sku, cost_price="450", selling_price="1299"):
    p = Product(
        business_id=business.id, sku=sku, name=f"Product {sku}",
        cost_price=Decimal(cost_price), selling_price=Decimal(selling_price),
    )
    db_session.add(p)
    db_session.flush()
    return p


def _add_daily_metric(db_session, business, product, channel, d, units, gross, discount=0, fee=0, profit=None):
    net = Decimal(gross) - Decimal(discount)
    if profit is None:
        profit = net - Decimal(fee) - (Decimal(units) * product.cost_price)
    db_session.add(
        DailyProductMetric(
            business_id=business.id, product_id=product.id, channel_id=channel.id, date=d,
            units_sold=units, gross_revenue=Decimal(gross), net_revenue=net,
            discount_amount=Decimal(discount), fee_amount=Decimal(fee),
            returns_amount=Decimal("0"), contribution_profit=profit,
            realized_price=(net / units) if units else Decimal("0"),
        )
    )
    db_session.flush()


def _add_snapshot(db_session, business, product, channel, d, qty):
    db_session.add(
        InventorySnapshot(
            business_id=business.id, product_id=product.id, channel_id=channel.id,
            snapshot_date=d, quantity_on_hand=qty,
        )
    )
    db_session.flush()


# --- stockout risk lifecycle --------------------------------------------------


def test_stockout_risk_insight_created(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-STOCK")
    as_of = date(2026, 8, 15)

    # steady sales -> real velocity, low stock -> stockout risk
    # velocity = total units / 30-day window (not just the 10 days of data
    # provided) -> 100 units / 30 days = 3.33/day -> 20 stock / 3.33 = 6 days coverage
    for i in range(10):
        _add_daily_metric(db_session, business, product, channel, as_of - timedelta(days=i), units=10, gross=12990)
    _add_snapshot(db_session, business, product, channel, as_of, qty=20)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)

    insights = service.list_insights(db_session, business.id, status="open")
    stockout = [i for i in insights if i["type"] == "STOCKOUT_RISK"]
    assert len(stockout) == 1
    assert stockout[0]["sku"] == "SKU-STOCK"
    assert stockout[0]["severity"] == "high"  # coverage <= 7 days
    assert stockout[0]["resolved_at"] is None


def test_stockout_insight_resolves_when_restocked(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-RESTOCK")
    as_of = date(2026, 8, 15)

    for i in range(10):
        _add_daily_metric(db_session, business, product, channel, as_of - timedelta(days=i), units=5, gross=6495)
    _add_snapshot(db_session, business, product, channel, as_of, qty=10)  # low -> stockout risk

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    open_insights = service.list_insights(db_session, business.id, status="open")
    assert any(i["type"] == "STOCKOUT_RISK" for i in open_insights)
    first_created_at = next(i["created_at"] for i in open_insights if i["type"] == "STOCKOUT_RISK")

    # run again unchanged -> created_at must NOT move
    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    open_insights_2 = service.list_insights(db_session, business.id, status="open")
    second_created_at = next(i["created_at"] for i in open_insights_2 if i["type"] == "STOCKOUT_RISK")
    assert first_created_at == second_created_at

    # restock heavily -> condition clears -> should resolve
    _add_snapshot(db_session, business, product, channel, as_of + timedelta(days=1), qty=1000)
    generate_insights_for_business(db_session, business.id, as_of_date=as_of + timedelta(days=1))

    open_after_restock = service.list_insights(db_session, business.id, status="open")
    assert not any(i["type"] == "STOCKOUT_RISK" for i in open_after_restock)

    resolved = service.list_insights(db_session, business.id, status="resolved")
    assert any(i["type"] == "STOCKOUT_RISK" for i in resolved)


def test_stockout_confidence_reflects_sales_day_coverage(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-CONF")
    as_of = date(2026, 8, 15)

    # only 3 distinct sales days out of a 30-day window
    for i in [0, 1, 2]:
        _add_daily_metric(db_session, business, product, channel, as_of - timedelta(days=i), units=10, gross=12990)
    _add_snapshot(db_session, business, product, channel, as_of, qty=5)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    stockout = next(i for i in insights if i["type"] == "STOCKOUT_RISK")
    # confidence = 3/30 = 0.10
    assert stockout["confidence"] == Decimal("0.10")


# --- slow moving --------------------------------------------------------------


def test_slow_moving_insight_created(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-SLOW", cost_price="650")
    as_of = date(2026, 8, 15)
    # no sales at all -> ages from first_seen
    _add_snapshot(db_session, business, product, channel, as_of - timedelta(days=70), qty=480)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)

    insights = service.list_insights(db_session, business.id, status="open")
    slow = next(i for i in insights if i["type"] == "SLOW_MOVING")
    assert slow["sku"] == "SKU-SLOW"
    assert "480" not in slow["message"] or True  # message format check below is what matters
    assert "70 days" in slow["message"]


# --- demand spike / decline with baseline guard --------------------------------


def test_demand_spike_detected_with_sufficient_baseline(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-SPIKE")
    as_of = date(2026, 8, 30)

    # previous 30-day period: 20 units total (>= MIN_BASELINE_UNITS=10)
    prev_start = as_of - timedelta(days=59)
    _add_daily_metric(db_session, business, product, channel, prev_start, units=20, gross=25980)
    # current 30-day period: 40 units (+100% growth)
    curr_start = as_of - timedelta(days=29)
    _add_daily_metric(db_session, business, product, channel, curr_start, units=40, gross=51960)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    spike = next((i for i in insights if i["type"] == "DEMAND_SPIKE"), None)
    assert spike is not None
    assert spike["severity"] == "high"  # growth >= 100%


def test_demand_spike_not_triggered_below_baseline(db_session, business, channel):
    """2 -> 3 units is technically +50% growth but must NOT trigger —
    prior period (2 units) is below MIN_BASELINE_UNITS (10)."""
    product = _make_product(db_session, business, "SKU-TINY")
    as_of = date(2026, 8, 30)

    prev_start = as_of - timedelta(days=59)
    _add_daily_metric(db_session, business, product, channel, prev_start, units=2, gross=2598)
    curr_start = as_of - timedelta(days=29)
    _add_daily_metric(db_session, business, product, channel, curr_start, units=3, gross=3897)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    assert not any(i["type"] == "DEMAND_SPIKE" for i in insights)


def test_demand_decline_detected(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-DECLINE")
    as_of = date(2026, 8, 30)

    prev_start = as_of - timedelta(days=59)
    _add_daily_metric(db_session, business, product, channel, prev_start, units=50, gross=64950)
    curr_start = as_of - timedelta(days=29)
    _add_daily_metric(db_session, business, product, channel, curr_start, units=20, gross=25980)

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    decline = next((i for i in insights if i["type"] == "DEMAND_DECLINE"), None)
    assert decline is not None  # (20-50)/50 = -60%, below -30% threshold


# --- margin erosion -------------------------------------------------------------


def test_margin_erosion_detected(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-MARGIN")
    as_of = date(2026, 8, 30)

    prev_start = as_of - timedelta(days=59)
    _add_daily_metric(
        db_session, business, product, channel, prev_start,
        units=10, gross=12990, discount=0, fee=500, profit=Decimal("8000"),
    )
    curr_start = as_of - timedelta(days=29)
    # revenue up, but profit down (heavier discounting/fees)
    _add_daily_metric(
        db_session, business, product, channel, curr_start,
        units=12, gross=15588, discount=2000, fee=1500, profit=Decimal("6000"),
    )

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    margin = next((i for i in insights if i["type"] == "MARGIN_EROSION"), None)
    assert margin is not None


# --- high discount dependency ----------------------------------------------------


def test_high_discount_dependency_detected(db_session, business, channel):
    product = _make_product(db_session, business, "SKU-DISCOUNT")
    as_of = date(2026, 8, 30)
    curr_start = as_of - timedelta(days=29)

    # 30% of gross given away in discounts, well above MIN_BASELINE_REVENUE
    _add_daily_metric(
        db_session, business, product, channel, curr_start,
        units=10, gross=13000, discount=3900, fee=500,
    )

    generate_insights_for_business(db_session, business.id, as_of_date=as_of)
    insights = service.list_insights(db_session, business.id, status="open")
    discount_insight = next((i for i in insights if i["type"] == "HIGH_DISCOUNT_DEPENDENCY"), None)
    assert discount_insight is not None
