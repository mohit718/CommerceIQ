"""
Read-side of the analytics engine. Everything here queries the already-
materialized daily_product_metrics / daily_channel_metrics tables — never
recomputes from order_lines directly (that's app/jobs/analytics_jobs.py's
job). Growth % is computed here, at read time, by comparing the requested
period against the immediately preceding period of equal length — it's
cheap (summing two small date ranges) and avoids the alternative of storing
a growth number that would need retroactive rewriting whenever a past
period's data changes (e.g. a late return recomputes an old date).
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Channel, DailyChannelMetric, DailyProductMetric, Product


def _default_range() -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=29)
    return start, end


def _previous_period(start_date: date, end_date: date) -> tuple[date, date]:
    period_days = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)
    return prev_start, prev_end


def _pct_growth(current: Decimal, previous: Decimal) -> float | None:
    """None (not 0 or infinity) when there's no prior-period baseline to
    compare against — a product/channel with zero sales last period
    doesn't have a meaningful 'growth %', and returning None lets the
    caller render 'new' instead of a misleading number."""
    if previous == 0:
        return None
    return float((current - previous) / previous * 100)


def get_overview(
    db: Session, business_id: int, start_date: date | None, end_date: date | None
) -> dict:
    if not start_date or not end_date:
        start_date, end_date = _default_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    current = _sum_channel_metrics(db, business_id, start_date, end_date)
    previous = _sum_channel_metrics(db, business_id, prev_start, prev_end)

    aov = (current["net_revenue"] / current["orders"]) if current["orders"] else Decimal("0")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "revenue": current["net_revenue"],
        "gross_revenue": current["gross_revenue"],
        "orders": current["orders"],
        "units": current["units"],
        "average_order_value": aov,
        "contribution_profit": current["contribution_profit"],
        "revenue_growth_pct": _pct_growth(current["net_revenue"], previous["net_revenue"]),
        "profit_growth_pct": _pct_growth(current["contribution_profit"], previous["contribution_profit"]),
    }


def _sum_channel_metrics(db: Session, business_id: int, start_date: date, end_date: date) -> dict:
    row = (
        db.query(
            func.coalesce(func.sum(DailyChannelMetric.gross_revenue), 0),
            func.coalesce(func.sum(DailyChannelMetric.net_revenue), 0),
            func.coalesce(func.sum(DailyChannelMetric.orders), 0),
            func.coalesce(func.sum(DailyChannelMetric.units), 0),
            func.coalesce(func.sum(DailyChannelMetric.contribution_profit), 0),
        )
        .filter(
            DailyChannelMetric.business_id == business_id,
            DailyChannelMetric.date >= start_date,
            DailyChannelMetric.date <= end_date,
        )
        .first()
    )
    gross, net, orders, units, profit = row
    return {
        "gross_revenue": Decimal(gross),
        "net_revenue": Decimal(net),
        "orders": int(orders),
        "units": int(units),
        "contribution_profit": Decimal(profit),
    }


def get_channel_breakdown(
    db: Session, business_id: int, start_date: date | None, end_date: date | None
) -> list[dict]:
    if not start_date or not end_date:
        start_date, end_date = _default_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    current_rows = _channel_grouped(db, business_id, start_date, end_date)
    prev_by_channel = {r["channel_id"]: r for r in _channel_grouped(db, business_id, prev_start, prev_end)}
    channel_names = {c.id: c.name for c in db.query(Channel).filter(Channel.business_id == business_id).all()}

    result = []
    for r in current_rows:
        prev = prev_by_channel.get(r["channel_id"])
        prev_net = prev["net_revenue"] if prev else Decimal("0")
        result.append(
            {
                "channel_id": r["channel_id"],
                "channel_name": channel_names.get(r["channel_id"], "Unknown"),
                "revenue": r["net_revenue"],
                "gross_revenue": r["gross_revenue"],
                "orders": r["orders"],
                "units": r["units"],
                "contribution_profit": r["contribution_profit"],
                "growth_pct": _pct_growth(r["net_revenue"], prev_net),
            }
        )
    result.sort(key=lambda x: x["revenue"], reverse=True)
    return result


def _channel_grouped(db: Session, business_id: int, start_date: date, end_date: date) -> list[dict]:
    rows = (
        db.query(
            DailyChannelMetric.channel_id,
            func.coalesce(func.sum(DailyChannelMetric.gross_revenue), 0),
            func.coalesce(func.sum(DailyChannelMetric.net_revenue), 0),
            func.coalesce(func.sum(DailyChannelMetric.orders), 0),
            func.coalesce(func.sum(DailyChannelMetric.units), 0),
            func.coalesce(func.sum(DailyChannelMetric.contribution_profit), 0)
        )
        .filter(
            DailyChannelMetric.business_id == business_id,
            DailyChannelMetric.date >= start_date,
            DailyChannelMetric.date <= end_date,
        )
        .group_by(DailyChannelMetric.channel_id)
        .all()
    )
    return [
        {
            "channel_id": channel_id,
            "gross_revenue": Decimal(gross),
            "net_revenue": Decimal(net),
            "orders": int(orders),
            "units": int(units),
            "contribution_profit": Decimal(profit),
        }
        for channel_id, gross, net, orders, units, profit in rows
    ]


def get_product_breakdown(
    db: Session,
    business_id: int,
    start_date: date | None,
    end_date: date | None,
    sort_by: str = "revenue",
) -> list[dict]:
    if not start_date or not end_date:
        start_date, end_date = _default_range()
    prev_start, prev_end = _previous_period(start_date, end_date)

    current_rows = _product_grouped(db, business_id, start_date, end_date)
    prev_by_product = {r["product_id"]: r for r in _product_grouped(db, business_id, prev_start, prev_end)}
    products = {p.id: p for p in db.query(Product).filter(Product.business_id == business_id).all()}

    result = []
    for r in current_rows:
        product = products.get(r["product_id"])
        if not product:
            continue  # defensive — shouldn't happen, but don't crash the dashboard over it
        prev = prev_by_product.get(r["product_id"])
        prev_net = prev["net_revenue"] if prev else Decimal("0")
        realized_price = (r["net_revenue"] / r["units"]).quantize(Decimal("0.01")) if r["units"] else Decimal("0")
        result.append(
            {
                "product_id": r["product_id"],
                "sku": product.sku,
                "name": product.name,
                "units": r["units"],
                "revenue": r["net_revenue"],
                "contribution_profit": r["contribution_profit"],
                "realized_price": realized_price,
                "growth_pct": _pct_growth(r["net_revenue"], prev_net),
            }
        )

    sort_key_fns = {
        "revenue": lambda x: x["revenue"],
        "units": lambda x: x["units"],
        "growth": lambda x: x["growth_pct"] if x["growth_pct"] is not None else float("-inf"),
    }
    result.sort(key=sort_key_fns.get(sort_by, sort_key_fns["revenue"]), reverse=True)
    return result


def _product_grouped(db: Session, business_id: int, start_date: date, end_date: date) -> list[dict]:
    rows = (
        db.query(
            DailyProductMetric.product_id,
            func.coalesce(func.sum(DailyProductMetric.units_sold), 0),
            func.coalesce(func.sum(DailyProductMetric.net_revenue), 0),
            func.coalesce(func.sum(DailyProductMetric.contribution_profit), 0)
        )
        .filter(
            DailyProductMetric.business_id == business_id,
            DailyProductMetric.date >= start_date,
            DailyProductMetric.date <= end_date,
        )
        .group_by(DailyProductMetric.product_id)
        .all()
    )
    return [
        {
            "product_id": product_id,
            "units": int(units),
            "net_revenue": Decimal(net),
            "contribution_profit": Decimal(profit),
        }
        for product_id, units, net, profit in rows
    ]
