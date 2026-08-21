"""
Deterministic insight detection engine (Phase 5 / M5).

Every insight this module writes is derived from numbers already computed
by (daily_product_metrics) and (inventory intelligence) —
never invented, never LLM-scored. Confidence is always a function of how
much underlying data supports the number, not a black-box guess
(Principle 3 — deterministic calculations before AI).

Lifecycle: generate_insights_for_business() is idempotent and safe to call
repeatedly (e.g. after every import). For every detection rule, on each run:
  - condition true, no open insight yet       -> create it
  - condition true, insight already open       -> update severity/confidence/
                                                    message in place; created_at
                                                    is NEVER touched (preserves
                                                    "when did this start")
  - insight open, condition no longer true     -> resolved_at = now

Insights operate at the PRODUCT level, pooled across channels — not
per-channel. Firing 4 separate stockout alerts for the same product across
4 channels is exactly the alert-fatigue Principle 5 warns against.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DailyProductMetric, Product, ProductInsight
from app.modules.inventory.service import compute_inventory_status
from app.shared.logging import get_logger

logger = get_logger(__name__)

WINDOW_DAYS = 30
MIN_BASELINE_UNITS = 10  # minimum prior-period units before trusting a growth %
MIN_BASELINE_REVENUE = Decimal("1000")  # minimum prior-period/current net revenue before trusting margin/discount %

DEMAND_SPIKE_THRESHOLD_PCT = 50.0
DEMAND_DECLINE_THRESHOLD_PCT = -30.0
HIGH_DISCOUNT_THRESHOLD_PCT = 20.0

# Mirrors the defaults baked into compute_inventory_status() —
# kept as separate constants here rather than imported, since this module
# only needs them for confidence/severity framing, not for reproducing the
# classification itself.
INVENTORY_VELOCITY_WINDOW_DAYS = 30
SLOW_MOVING_THRESHOLD_DAYS = 60


@dataclass(frozen=True)
class DetectedInsight:
    product_id: int
    type: str
    severity: str
    confidence: Decimal
    message: str
    recommendation: str | None


# ==============================================================================
# PUBLIC API
# ==============================================================================


def generate_insights_for_business(db: Session, business_id: int, as_of_date: date | None = None) -> None:
    as_of_date = as_of_date or date.today()

    detected: list[DetectedInsight] = []
    detected += _detect_inventory_insights(db, business_id, as_of_date)
    detected += _detect_performance_insights(db, business_id, as_of_date)

    _reconcile(db, business_id, detected)
    db.commit()
    logger.info("generated insights for business %s: %d active", business_id, len(detected))


# ==============================================================================
# RECONCILIATION (open -> still-true -> resolved lifecycle)
# ==============================================================================


def _reconcile(db: Session, business_id: int, detected: list[DetectedInsight]) -> None:
    open_insights = (
        db.query(ProductInsight)
        .filter(ProductInsight.business_id == business_id, ProductInsight.resolved_at.is_(None))
        .all()
    )
    open_by_key = {(i.product_id, i.type): i for i in open_insights}
    detected_keys: set[tuple[int, str]] = set()

    now = datetime.now(timezone.utc)

    for d in detected:
        key = (d.product_id, d.type)
        detected_keys.add(key)
        existing = open_by_key.get(key)
        if existing:
            # still true -> update in place; created_at deliberately untouched
            existing.severity = d.severity
            existing.confidence = d.confidence
            existing.message = d.message
            existing.recommendation = d.recommendation
        else:
            db.add(
                ProductInsight(
                    business_id=business_id,
                    product_id=d.product_id,
                    type=d.type,
                    severity=d.severity,
                    confidence=d.confidence,
                    message=d.message,
                    recommendation=d.recommendation,
                )
            )

    for key, existing in open_by_key.items():
        if key not in detected_keys:
            existing.resolved_at = now


# ==============================================================================
# INVENTORY-DERIVED INSIGHTS (built on compute_inventory_status)
# ==============================================================================


def _detect_inventory_insights(db: Session, business_id: int, as_of_date: date) -> list[DetectedInsight]:
    rows = compute_inventory_status(db, business_id, channel_id=None, as_of_date=as_of_date)

    product_ids = [r["product_id"] for r in rows]
    window_start = as_of_date - timedelta(days=INVENTORY_VELOCITY_WINDOW_DAYS - 1)
    sales_days_by_product = _count_sales_days_by_product(db, business_id, product_ids, window_start, as_of_date)

    results = []
    for row in rows:
        if row["status"] in ("stockout_risk", "out_of_stock"):
            results.append(_build_stockout_insight(row, sales_days_by_product))
        elif row["status"] == "slow_moving":
            results.append(_build_slow_moving_insight(row))
    return results


def _build_stockout_insight(row: dict, sales_days_by_product: dict) -> DetectedInsight:
    """Confidence = fraction of the velocity window that actually had
    sales — a coverage estimate built on 3 sales-days out of 30 is
    genuinely less trustworthy than one built on 25."""
    sales_days = sales_days_by_product.get(row["product_id"], 0)
    confidence = Decimal(min(1.0, sales_days / INVENTORY_VELOCITY_WINDOW_DAYS)).quantize(Decimal("0.01"))

    coverage = row["coverage_days"]
    if row["status"] == "out_of_stock":
        severity = "critical"
        message = f"{row['sku']} is out of stock."
    elif coverage is not None and coverage <= 7:
        severity = "high"
        message = f"{row['sku']} may stock out in approximately {coverage} days."
    elif coverage is not None:
        severity = "medium"
        message = f"{row['sku']} may stock out in approximately {coverage} days."
    else:
        severity = "medium"
        message = f"{row['sku']} is low on stock with no recent sales to estimate coverage."

    if row["reorder_qty"]:
        recommendation = f"Reorder approximately {row['reorder_qty']} units to restore healthy coverage."
    else:
        recommendation = "Reorder quantity cannot be estimated — insufficient recent sales velocity."

    return DetectedInsight(
        product_id=row["product_id"],
        type="STOCKOUT_RISK",
        severity=severity,
        confidence=confidence,
        message=message,
        recommendation=recommendation,
    )


def _build_slow_moving_insight(row: dict) -> DetectedInsight:
    """Confidence here answers 'how stale is stale' — idle time relative to
    the threshold — NOT 'how much sales data backs this'. Slow-moving is
    triggered BY absence of sales, so the stockout confidence formula
    (sales days / window) would point the wrong direction here: it would
    read as low confidence exactly when we're most sure the product isn't
    selling. Scaled so hitting the threshold exactly reads as 0.5
    confidence, and double the threshold reads as fully confident (1.0)."""
    aging_days = row["aging_days"] or 0
    confidence = Decimal(min(1.0, aging_days / (SLOW_MOVING_THRESHOLD_DAYS * 2))).quantize(Decimal("0.01"))
    severity = "high" if aging_days >= SLOW_MOVING_THRESHOLD_DAYS * 2 else "medium"

    message = f"{row['sku']} has had no sales in {aging_days} days. Inventory value: ₹{row['inventory_value']:.2f}."
    recommendation = "Consider promotional pricing or clearance to free up locked capital."

    return DetectedInsight(
        product_id=row["product_id"],
        type="SLOW_MOVING",
        severity=severity,
        confidence=confidence,
        message=message,
        recommendation=recommendation,
    )


def _count_sales_days_by_product(
    db: Session, business_id: int, product_ids: list[int], window_start: date, as_of_date: date
) -> dict[int, int]:
    if not product_ids:
        return {}
    rows = (
        db.query(DailyProductMetric.product_id, DailyProductMetric.date, DailyProductMetric.units_sold)
        .filter(
            DailyProductMetric.business_id == business_id,
            DailyProductMetric.product_id.in_(product_ids),
            DailyProductMetric.date >= window_start,
            DailyProductMetric.date <= as_of_date,
        )
        .all()
    )
    days_with_sales: dict[int, set] = defaultdict(set)
    for product_id, d, units in rows:
        if (units or 0) > 0:
            days_with_sales[product_id].add(d)
    return {pid: len(days) for pid, days in days_with_sales.items()}


# ==============================================================================
# PERFORMANCE INSIGHTS (built on daily_product_metrics)
# ==============================================================================


def _detect_performance_insights(db: Session, business_id: int, as_of_date: date) -> list[DetectedInsight]:
    window_start = as_of_date - timedelta(days=WINDOW_DAYS - 1)
    prev_end = window_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=WINDOW_DAYS - 1)

    current = _product_period_aggregate(db, business_id, window_start, as_of_date)
    previous = _product_period_aggregate(db, business_id, prev_start, prev_end)
    products = {p.id: p for p in db.query(Product).filter(Product.business_id == business_id).all()}

    results: list[DetectedInsight] = []
    for product_id, curr in current.items():
        product = products.get(product_id)
        if not product:
            continue
        prev = previous.get(product_id)

        results += _check_demand_change(product, curr, prev)
        results += _check_margin_erosion(product, curr, prev)
        results += _check_high_discount_dependency(product, curr)

    return results


def _product_period_aggregate(db: Session, business_id: int, start_date: date, end_date: date) -> dict[int, dict]:
    rows = (
        db.query(
            DailyProductMetric.product_id,
            func.sum(DailyProductMetric.units_sold),
            func.sum(DailyProductMetric.gross_revenue),
            func.sum(DailyProductMetric.net_revenue),
            func.sum(DailyProductMetric.discount_amount),
            func.sum(DailyProductMetric.contribution_profit),
        )
        .filter(
            DailyProductMetric.business_id == business_id,
            DailyProductMetric.date >= start_date,
            DailyProductMetric.date <= end_date,
        )
        .group_by(DailyProductMetric.product_id)
        .all()
    )
    return {
        product_id: {
            "units": int(units or 0),
            "gross_revenue": Decimal(gross or 0),
            "net_revenue": Decimal(net or 0),
            "discount_amount": Decimal(discount or 0),
            "contribution_profit": Decimal(profit or 0),
        }
        for product_id, units, gross, net, discount, profit in rows
    }


def _pct_change(current, previous) -> float | None:
    if previous == 0:
        return None
    return float((current - previous) / previous * 100)


def _check_demand_change(product: Product, curr: dict, prev: dict | None) -> list[DetectedInsight]:
    """Guarded by MIN_BASELINE_UNITS so a jump like 2 -> 3 units (which is
    technically +50%) can't trigger a spike insight — the prior period
    needs a meaningful baseline before a growth % is trustworthy."""
    if not prev or prev["units"] < MIN_BASELINE_UNITS:
        return []
    growth = _pct_change(curr["units"], prev["units"])
    if growth is None:
        return []

    if growth >= DEMAND_SPIKE_THRESHOLD_PCT:
        confidence = Decimal(min(1.0, prev["units"] / MIN_BASELINE_UNITS)).quantize(Decimal("0.01"))
        severity = "high" if growth >= 100 else "medium"
        return [
            DetectedInsight(
                product_id=product.id,
                type="DEMAND_SPIKE",
                severity=severity,
                confidence=confidence,
                message=(
                    f"{product.sku} units sold grew {growth:.0f}% over the last {WINDOW_DAYS} days "
                    f"({prev['units']} -> {curr['units']} units)."
                ),
                recommendation="Consider increasing inventory to sustain the growth.",
            )
        ]

    if growth <= DEMAND_DECLINE_THRESHOLD_PCT:
        confidence = Decimal(min(1.0, prev["units"] / MIN_BASELINE_UNITS)).quantize(Decimal("0.01"))
        severity = "high" if growth <= -50 else "medium"
        return [
            DetectedInsight(
                product_id=product.id,
                type="DEMAND_DECLINE",
                severity=severity,
                confidence=confidence,
                message=(
                    f"{product.sku} units sold fell {abs(growth):.0f}% over the last {WINDOW_DAYS} days "
                    f"({prev['units']} -> {curr['units']} units)."
                ),
                recommendation="Investigate the drop — consider promotions or reviewing pricing/competition.",
            )
        ]

    return []


def _check_margin_erosion(product: Product, curr: dict, prev: dict | None) -> list[DetectedInsight]:
    """The exact Section 11 pattern: revenue went up, but profit went down
    — usually discounts/fees eating the gain. Guarded on prior-period
    revenue so a near-zero baseline can't produce a wild swing."""
    if not prev or prev["net_revenue"] < MIN_BASELINE_REVENUE:
        return []
    revenue_growth = _pct_change(curr["net_revenue"], prev["net_revenue"])
    profit_growth = _pct_change(curr["contribution_profit"], prev["contribution_profit"])
    if revenue_growth is None or profit_growth is None:
        return []

    if revenue_growth > 0 and profit_growth < 0:
        confidence = Decimal(
            min(1.0, float(prev["net_revenue"]) / float(MIN_BASELINE_REVENUE))
        ).quantize(Decimal("0.01"))
        severity = "high" if profit_growth <= -20 else "medium"
        return [
            DetectedInsight(
                product_id=product.id,
                type="MARGIN_EROSION",
                severity=severity,
                confidence=confidence,
                message=(
                    f"{product.sku} revenue grew {revenue_growth:.0f}% but contribution profit fell "
                    f"{abs(profit_growth):.0f}% over the last {WINDOW_DAYS} days."
                ),
                recommendation="Review discounting and marketplace fees eating into margin.",
            )
        ]
    return []


def _check_high_discount_dependency(product: Product, curr: dict) -> list[DetectedInsight]:
    """Uses the CURRENT period only (no prior-period comparison needed) —
    guarded on current gross revenue so a tiny-volume product can't trigger
    off one heavily-discounted single sale."""
    if curr["gross_revenue"] < MIN_BASELINE_REVENUE:
        return []
    discount_rate = float(curr["discount_amount"] / curr["gross_revenue"]) * 100
    if discount_rate >= HIGH_DISCOUNT_THRESHOLD_PCT:
        confidence = Decimal(
            min(1.0, float(curr["gross_revenue"]) / float(MIN_BASELINE_REVENUE))
        ).quantize(Decimal("0.01"))
        severity = "high" if discount_rate >= 35 else "medium"
        return [
            DetectedInsight(
                product_id=product.id,
                type="HIGH_DISCOUNT_DEPENDENCY",
                severity=severity,
                confidence=confidence,
                message=(
                    f"{discount_rate:.0f}% of {product.sku}'s gross revenue was given away in discounts "
                    f"over the last {WINDOW_DAYS} days."
                ),
                recommendation="Evaluate whether this discount depth is necessary to sustain sales.",
            )
        ]
    return []
