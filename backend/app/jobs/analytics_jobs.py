"""
Aggregation writer: recomputes daily_product_metrics and daily_channel_metrics
from order_lines (bucketed by order_date) and returns (bucketed by
return_date, per the product decision — a return's cash/profit impact lands
on the day it happened, not retroactively rewriting the original sale's day).

recompute_metrics_for_dates() is idempotent: for each date it deletes and
rewrites the rows for that date, rather than incrementally patching them.
That makes it safe to call repeatedly (e.g. once per ingested batch) without
double-counting, and simple to reason about — no running-total drift to
worry about.

Known simplification: contribution_profit uses the PRODUCT'S CURRENT
cost_price, not the cost at time of sale. If cost prices change over time,
recomputing historical metrics later will subtly use today's cost instead
of the original one. A proper fix (versioned cost history) is real
over-engineering for MVP — flagged here rather than solved.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DailyChannelMetric,
    DailyProductMetric,
    Order,
    OrderLine,
    Product,
    Return,
)
from app.shared.logging import get_logger

logger = get_logger(__name__)


def recompute_metrics_for_dates(
    db: Session,
    business_id: int,
    dates: set[date],
    channel_id: int | None = None,
) -> None:
    """Recomputes daily_product_metrics and daily_channel_metrics for every
    date in `dates`. Pass channel_id to scope the recompute to one channel
    (used when a single channel's import batch is what triggered this) —
    other channels' rows for the same date are left untouched."""
    if not dates:
        return

    for d in sorted(dates):
        _recompute_product_metrics_for_date(db, business_id, d, channel_id)
        # SessionLocal is configured with autoflush=False (see
        # app/core/database.py), so the DailyProductMetric rows just added
        # above aren't visible to the channel-level rollup query below
        # without an explicit flush.
        db.flush()
        _recompute_channel_metrics_for_date(db, business_id, d, channel_id)

    db.commit()
    logger.info(
        "recomputed metrics for business %s: %d date(s)%s",
        business_id, len(dates), f" (channel {channel_id})" if channel_id else "",
    )


def _recompute_product_metrics_for_date(
    db: Session, business_id: int, d: date, channel_id: int | None
) -> None:
    # --- sales side: grouped by (product, channel), bucketed by order_date
    sales_q = (
        db.query(
            OrderLine.product_id,
            Order.channel_id,
            func.sum(OrderLine.quantity),
            func.sum(OrderLine.gross_amount),
            func.sum(OrderLine.discount_amount),
            func.sum(OrderLine.fee_amount),
            func.sum(OrderLine.shipping_amount),
        )
        .join(Order, OrderLine.order_id == Order.id)
        .filter(Order.business_id == business_id, Order.order_date == d)
    )
    if channel_id is not None:
        sales_q = sales_q.filter(Order.channel_id == channel_id)
    sales_q = sales_q.group_by(OrderLine.product_id, Order.channel_id)

    sales_by_key = {
        (product_id, ch_id): {
            "units": int(units or 0),
            "gross": Decimal(gross or 0),
            "discount": Decimal(discount or 0),
            "fee": Decimal(fee or 0),
            "shipping": Decimal(shipping or 0),
        }
        for product_id, ch_id, units, gross, discount, fee, shipping in sales_q.all()
    }

    # --- returns side: grouped by (product, channel), bucketed by return_date
    returns_q = (
        db.query(
            OrderLine.product_id,
            Order.channel_id,
            func.sum(Return.refund_amount),
        )
        .join(OrderLine, Return.order_line_id == OrderLine.id)
        .join(Order, OrderLine.order_id == Order.id)
        .filter(Return.business_id == business_id, Return.return_date == d)
    )
    if channel_id is not None:
        returns_q = returns_q.filter(Order.channel_id == channel_id)
    returns_q = returns_q.group_by(OrderLine.product_id, Order.channel_id)

    returns_by_key = {
        (product_id, ch_id): Decimal(refund_sum or 0)
        for product_id, ch_id, refund_sum in returns_q.all()
    }

    keys = set(sales_by_key) | set(returns_by_key)

    # idempotent recompute: wipe this date's existing rows first
    del_q = db.query(DailyProductMetric).filter(
        DailyProductMetric.business_id == business_id, DailyProductMetric.date == d
    )
    if channel_id is not None:
        del_q = del_q.filter(DailyProductMetric.channel_id == channel_id)
    del_q.delete(synchronize_session=False)

    if not keys:
        return

    product_ids = {k[0] for k in keys}
    cost_by_product = {
        p.id: (p.cost_price or Decimal("0"))
        for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }

    for product_id, ch_id in keys:
        sales = sales_by_key.get((product_id, ch_id), {})
        units = sales.get("units", 0)
        gross = sales.get("gross", Decimal("0"))
        discount = sales.get("discount", Decimal("0"))
        fee = sales.get("fee", Decimal("0"))
        shipping = sales.get("shipping", Decimal("0"))
        returns_amount = returns_by_key.get((product_id, ch_id), Decimal("0"))

        net_revenue = gross - discount
        realized_price = (net_revenue / units).quantize(Decimal("0.01")) if units else Decimal("0")
        cost_price = cost_by_product.get(product_id, Decimal("0"))
        contribution_profit = (
            net_revenue - fee - shipping - returns_amount - (Decimal(units) * cost_price)
        )

        db.add(
            DailyProductMetric(
                business_id=business_id,
                product_id=product_id,
                channel_id=ch_id,
                date=d,
                units_sold=units,
                gross_revenue=gross,
                net_revenue=net_revenue,
                discount_amount=discount,
                fee_amount=fee,
                returns_amount=returns_amount,
                contribution_profit=contribution_profit,
                realized_price=realized_price,
            )
        )


def _recompute_channel_metrics_for_date(
    db: Session, business_id: int, d: date, channel_id: int | None
) -> None:
    # Rolled up from the daily_product_metrics rows just written above —
    # avoids re-deriving from order_lines/returns a second time.
    del_q = db.query(DailyChannelMetric).filter(
        DailyChannelMetric.business_id == business_id, DailyChannelMetric.date == d
    )
    if channel_id is not None:
        del_q = del_q.filter(DailyChannelMetric.channel_id == channel_id)
    del_q.delete(synchronize_session=False)

    q = db.query(
        DailyProductMetric.channel_id,
        func.sum(DailyProductMetric.gross_revenue),
        func.sum(DailyProductMetric.net_revenue),
        func.sum(DailyProductMetric.units_sold),
        func.sum(DailyProductMetric.contribution_profit),
    ).filter(DailyProductMetric.business_id == business_id, DailyProductMetric.date == d)
    if channel_id is not None:
        q = q.filter(DailyProductMetric.channel_id == channel_id)
    q = q.group_by(DailyProductMetric.channel_id)

    rows = q.all()
    if not rows:
        return

    orders_q = db.query(
        Order.channel_id, func.count(func.distinct(Order.id))
    ).filter(Order.business_id == business_id, Order.order_date == d)
    if channel_id is not None:
        orders_q = orders_q.filter(Order.channel_id == channel_id)
    orders_q = orders_q.group_by(Order.channel_id)
    orders_by_channel = {ch_id: count for ch_id, count in orders_q.all()}

    for ch_id, gross, net, units, profit in rows:
        db.add(
            DailyChannelMetric(
                business_id=business_id,
                channel_id=ch_id,
                date=d,
                gross_revenue=Decimal(gross or 0),
                net_revenue=Decimal(net or 0),
                orders=orders_by_channel.get(ch_id, 0),
                units=int(units or 0),
                contribution_profit=Decimal(profit or 0),
            )
        )
