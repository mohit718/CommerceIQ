"""
Tests the aggregation writer (app/jobs/analytics_jobs.py) and read service
(app/modules/analytics/service.py) directly against the ORM, with
hand-computed expected values — the same style as test_ingestion.py.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.jobs.analytics_jobs import recompute_metrics_for_dates
from app.models import Business, Channel, DailyChannelMetric, DailyProductMetric, Order, OrderLine, Product, Return
from app.modules.analytics import service


@pytest.fixture()
def scenario(db_session):
    """One business, one channel, one product, two dated orders, plus a
    return that lands on a THIRD date — this is what exercises the
    return_date-bucketing behavior specifically."""
    business = Business(name="Analytics Test Co")
    db_session.add(business)
    db_session.flush()

    channel = Channel(business_id=business.id, name="Amazon", type="marketplace")
    db_session.add(channel)
    db_session.flush()

    product = Product(
        business_id=business.id, sku="AD141-BLK", name="Airdopes 141 Black",
        cost_price=Decimal("450"), selling_price=Decimal("1299"),
    )
    db_session.add(product)
    db_session.flush()

    day1 = date(2026, 8, 1)
    day2 = date(2026, 8, 2)
    return_day = date(2026, 8, 10)  # deliberately NOT day1 or day2

    order1 = Order(business_id=business.id, channel_id=channel.id, external_order_id="O-1", order_date=day1)
    db_session.add(order1)
    db_session.flush()
    line1 = OrderLine(
        business_id=business.id, order_id=order1.id, product_id=product.id,
        quantity=2, gross_amount=Decimal("2598"), discount_amount=Decimal("260"),
        fee_amount=Decimal("180"), shipping_amount=Decimal("0"), tax_amount=Decimal("130"),
    )
    db_session.add(line1)
    db_session.flush()

    order2 = Order(business_id=business.id, channel_id=channel.id, external_order_id="O-2", order_date=day2)
    db_session.add(order2)
    db_session.flush()
    line2 = OrderLine(
        business_id=business.id, order_id=order2.id, product_id=product.id,
        quantity=1, gross_amount=Decimal("1299"), discount_amount=Decimal("0"),
        fee_amount=Decimal("90"), shipping_amount=Decimal("0"), tax_amount=Decimal("65"),
    )
    db_session.add(line2)
    db_session.flush()

    # a return of line1's sale, processed 9 days later
    ret = Return(
        business_id=business.id, order_line_id=line1.id, quantity=1,
        return_date=return_day, reason="Defective", refund_amount=Decimal("1169"),
    )
    db_session.add(ret)
    db_session.flush()

    return {
        "business": business, "channel": channel, "product": product,
        "day1": day1, "day2": day2, "return_day": return_day,
    }


def test_product_metrics_computed_correctly_for_sale_day(db_session, scenario):
    recompute_metrics_for_dates(
        db_session, scenario["business"].id, {scenario["day1"], scenario["day2"], scenario["return_day"]}
    )

    m1 = (
        db_session.query(DailyProductMetric)
        .filter(DailyProductMetric.date == scenario["day1"])
        .first()
    )
    assert m1.units_sold == 2
    assert m1.gross_revenue == Decimal("2598")
    assert m1.net_revenue == Decimal("2338")  # 2598 - 260
    assert m1.realized_price == Decimal("1169")  # 2338 / 2
    # day1 has NO returns_amount — the return is bucketed on return_day, not order_date
    assert m1.returns_amount == Decimal("0")
    # contribution_profit = net_revenue - fee - shipping - returns - (units * cost)
    # = 2338 - 180 - 0 - 0 - (2 * 450) = 1258
    assert m1.contribution_profit == Decimal("1258")


def test_return_lands_on_return_date_not_order_date(db_session, scenario):
    recompute_metrics_for_dates(
        db_session, scenario["business"].id, {scenario["day1"], scenario["day2"], scenario["return_day"]}
    )

    return_day_metric = (
        db_session.query(DailyProductMetric)
        .filter(DailyProductMetric.date == scenario["return_day"])
        .first()
    )
    # no sale happened on return_day, but a returns_amount-only row should
    # still exist there because of the return
    assert return_day_metric is not None
    assert return_day_metric.units_sold == 0
    assert return_day_metric.gross_revenue == Decimal("0")
    assert return_day_metric.returns_amount == Decimal("1169")
    # contribution_profit on this date = 0 - 0 - 0 - 1169 - 0 = -1169
    assert return_day_metric.contribution_profit == Decimal("-1169")


def test_channel_metrics_roll_up_from_product_metrics(db_session, scenario):
    recompute_metrics_for_dates(db_session, scenario["business"].id, {scenario["day1"]})

    cm = (
        db_session.query(DailyChannelMetric)
        .filter(DailyChannelMetric.date == scenario["day1"])
        .first()
    )
    assert cm.orders == 1
    assert cm.units == 2
    assert cm.net_revenue == Decimal("2338")


def test_recompute_is_idempotent_not_additive(db_session, scenario):
    recompute_metrics_for_dates(db_session, scenario["business"].id, {scenario["day1"]})
    recompute_metrics_for_dates(db_session, scenario["business"].id, {scenario["day1"]})  # run twice

    rows = (
        db_session.query(DailyProductMetric)
        .filter(DailyProductMetric.date == scenario["day1"])
        .all()
    )
    assert len(rows) == 1  # not doubled
    assert rows[0].units_sold == 2  # not doubled to 4


def test_overview_growth_calculation(db_session, scenario):
    recompute_metrics_for_dates(
        db_session, scenario["business"].id, {scenario["day1"], scenario["day2"], scenario["return_day"]}
    )

    # period = day1..day2, previous period = two days immediately before day1
    # (which have no data at all -> previous net_revenue == 0 -> growth is None)
    result = service.get_overview(db_session, scenario["business"].id, scenario["day1"], scenario["day2"])
    assert result["revenue"] == Decimal("2338") + Decimal("1299")  # 3637
    assert result["orders"] == 2
    assert result["units"] == 3
    assert result["revenue_growth_pct"] is None  # no baseline period data


def test_product_breakdown_sorted_by_revenue(db_session, scenario):
    recompute_metrics_for_dates(db_session, scenario["business"].id, {scenario["day1"], scenario["day2"]})

    result = service.get_product_breakdown(
        db_session, scenario["business"].id, scenario["day1"], scenario["day2"], sort_by="revenue"
    )
    assert len(result) == 1
    assert result[0]["sku"] == "AD141-BLK"
    assert result[0]["units"] == 3
    assert result[0]["revenue"] == Decimal("3637")
