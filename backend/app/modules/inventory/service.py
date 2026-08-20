"""
Inventory intelligence — computed at read time, not materialized into a new
table. Unlike Phase 3's sales analytics (where metric definitions are
fixed), inventory status depends on THRESHOLDS that naturally vary per read
(a seller adjusting "flag stockout at N days" interactively). More
importantly, inventory status can change purely because time passed (a
product idle for 55 days becomes "slow_moving" on day 60 even with zero new
data) — correctly refreshing a materialized table for that would need a
nightly scheduled job, which needs Celery, which we're deliberately not
adding until Phase 7. So this stays live, reading from data we already
have: the latest inventory_snapshots per product, and sales velocity from
the already-materialized daily_product_metrics (Phase 3).

Aging proxy: "how stale is this SKU" is approximated two ways, in priority
order:
  1. days since its most recent sale (if it has ever sold)
  2. days since we first saw it in an inventory snapshot (if it never sold)
A product with neither (no sales, no snapshot) can't be aged at all.
This is a proxy, not true FIFO lot-level aging — we don't have a
stock-receipt ledger to know when a specific batch arrived. A real
lot-aging feature would need a new receiving-event table; flagged as a
known simplification rather than built now.

File layout:
  1. Public API        — compute_inventory_status(), get_inventory_overview()
  2. Data fetching      — queries that pull raw facts from the DB
  3. Metric calculations — pure functions, one per metric, no DB access.
     Kept separate from data fetching so each formula can be read, tested,
     and reused (e.g. by Phase 5's insight engine) independently of how the
     underlying data was queried.
"""
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Channel, DailyProductMetric, InventorySnapshot, Product

# ==============================================================================
# 1. PUBLIC API
# ==============================================================================


def compute_inventory_status(
    db: Session,
    business_id: int,
    channel_id: int | None = None,
    velocity_window_days: int = 30,
    stockout_threshold_days: int = 14,
    slow_moving_threshold_days: int = 60,
    overstock_threshold_days: int = 90,
    target_coverage_days: int = 30,
    as_of_date: date | None = None,
) -> list[dict]:
    """Returns one row per product (or per product+channel if channel_id is
    given). When channel_id is None, stock and velocity are pooled across
    every channel that product sells on — some sellers track inventory
    per-channel, others track one combined number, so this supports both.

    Step-by-step per row: fetch latest stock -> fetch sales signals ->
    compute velocity -> compute coverage days -> compute aging -> compute
    inventory value -> classify status -> compute reorder qty (only if at
    risk). See the module docstring's section 3 for each formula.
    """
    as_of_date = as_of_date or date.today()
    window_start = as_of_date - timedelta(days=velocity_window_days - 1)

    # --- fetch raw facts -----------------------------------------------------
    latest_by_pc, first_seen_by_pc = fetch_latest_and_first_seen_snapshots(
        db, business_id, channel_id, as_of_date
    )
    if not latest_by_pc:
        return []

    stock_rows = resolve_stock_rows(latest_by_pc, first_seen_by_pc, channel_id)
    product_ids = {key[0] for key in stock_rows}

    velocity_by_key, last_sale_by_key = fetch_sales_signals(
        db, business_id, product_ids, channel_id, window_start, as_of_date
    )

    products = {
        p.id: p
        for p in db.query(Product).filter(Product.business_id == business_id, Product.id.in_(product_ids)).all()
    }
    channels = {c.id: c.name for c in db.query(Channel).filter(Channel.business_id == business_id).all()}

    # --- compute metrics, one product(+channel) row at a time ----------------
    results = []
    for (product_id, ch_id), stock_info in stock_rows.items():
        product = products.get(product_id)
        if not product:
            continue  # defensive — shouldn't happen given the FK, but don't crash a dashboard read over it

        velocity_key = (product_id, ch_id) if channel_id is not None else product_id
        latest_stock = stock_info["stock"]

        avg_daily_velocity = calculate_average_daily_velocity(
            units_sold_in_window=velocity_by_key.get(velocity_key, 0),
            window_days=velocity_window_days,
        )

        coverage_days = calculate_coverage_days(latest_stock, avg_daily_velocity)

        last_sale_date = last_sale_by_key.get(velocity_key)
        aging_days, aging_basis = calculate_days_since_last_sale(
            as_of_date, last_sale_date, stock_info["first_seen"]
        )

        inventory_value = calculate_inventory_value(latest_stock, product.cost_price)

        status = classify_inventory_status(
            latest_stock, coverage_days, aging_days,
            stockout_threshold_days, slow_moving_threshold_days, overstock_threshold_days,
        )

        reorder_qty = calculate_reorder_quantity(
            status, avg_daily_velocity, target_coverage_days, latest_stock
        )

        results.append(
            {
                "product_id": product_id,
                "sku": product.sku,
                "name": product.name,
                "channel_id": ch_id,
                "channel_name": channels.get(ch_id) if ch_id else None,
                "latest_stock": latest_stock,
                "snapshot_date": stock_info["snapshot_date"],
                "avg_daily_velocity": avg_daily_velocity.quantize(Decimal("0.01")),
                "coverage_days": coverage_days.quantize(Decimal("0.1")) if coverage_days is not None else None,
                "last_sale_date": last_sale_date,
                "first_seen_date": stock_info["first_seen"],
                "aging_days": aging_days,
                "aging_basis": aging_basis,
                "inventory_value": inventory_value,
                "status": status,
                "reorder_qty": reorder_qty,
            }
        )

    return results


def get_inventory_overview(db: Session, business_id: int, **kwargs) -> dict:
    as_of_date = kwargs.get("as_of_date") or date.today()
    rows = compute_inventory_status(db, business_id, **kwargs)

    slow_rows = [r for r in rows if r["status"] == "slow_moving"]
    return {
        "as_of_date": as_of_date,
        "total_inventory_value": sum((r["inventory_value"] for r in rows), Decimal("0")),
        "stockout_risk_count": sum(1 for r in rows if r["status"] == "stockout_risk"),
        "out_of_stock_count": sum(1 for r in rows if r["status"] == "out_of_stock"),
        "slow_moving_count": len(slow_rows),
        "overstock_count": sum(1 for r in rows if r["status"] == "overstock"),
        "slow_moving_locked_value": sum((r["inventory_value"] for r in slow_rows), Decimal("0")),
    }


# ==============================================================================
# 2. DATA FETCHING (touches the DB)
# ==============================================================================


def fetch_latest_and_first_seen_snapshots(
    db: Session, business_id: int, channel_id: int | None, as_of_date: date
) -> tuple[dict, dict]:
    """Returns ({(product_id, channel_id): (snapshot_date, qty)},
    {(product_id, channel_id): first_snapshot_date}). Computed in Python
    over an ascending-ordered query rather than a DISTINCT ON / window
    function — portable across Postgres and the SQLite used in tests, and
    at prototype snapshot volumes (periodic, not per-sale) this is cheap."""
    q = db.query(InventorySnapshot).filter(
        InventorySnapshot.business_id == business_id,
        InventorySnapshot.snapshot_date <= as_of_date,
    )
    if channel_id is not None:
        q = q.filter(InventorySnapshot.channel_id == channel_id)
    rows = q.order_by(InventorySnapshot.snapshot_date.asc()).all()

    latest: dict[tuple[int, int], tuple[date, int]] = {}
    first_seen: dict[tuple[int, int], date] = {}
    for r in rows:
        key = (r.product_id, r.channel_id)
        if key not in first_seen:
            first_seen[key] = r.snapshot_date
        latest[key] = (r.snapshot_date, r.quantity_on_hand)  # ascending order -> last write wins = latest
    return latest, first_seen


def resolve_stock_rows(latest_by_pc: dict, first_seen_by_pc: dict, channel_id: int | None) -> dict:
    """Turns the raw per-(product, channel) snapshot lookup into the rows
    compute_inventory_status() actually iterates over.
    channel_id given -> keep per-(product, channel) rows as-is (one row per
    channel a product is tracked on).
    channel_id None -> pool across channels: stock sums, snapshot_date
    takes the freshest channel's date, first_seen takes the earliest."""
    if channel_id is not None:
        return {
            key: {"stock": qty, "snapshot_date": snap_date, "first_seen": first_seen_by_pc[key]}
            for key, (snap_date, qty) in latest_by_pc.items()
        }

    # Channel is None, so merge the product listed on all channels
    pooled: dict[int, dict] = {}
    for key, (snap_date, qty) in latest_by_pc.items():
        product_id, channel_id = key
        entry = pooled.setdefault(product_id, {"stock": 0, "snapshot_date": None, "first_seen": None})
        entry["stock"] += qty
        entry["snapshot_date"] = max(entry["snapshot_date"], snap_date) if entry["snapshot_date"] else snap_date
        fs = first_seen_by_pc[key]
        entry["first_seen"] = min(entry["first_seen"], fs) if entry["first_seen"] else fs

    return {(product_id, None): v for product_id, v in pooled.items()}


def fetch_sales_signals(
    db: Session,
    business_id: int,
    product_ids: set[int],
    channel_id: int | None,
    window_start: date,
    as_of_date: date,
) -> tuple[dict, dict]:
    """Returns (units_sold_by_key: total units sold in the window,
    last_sale_by_key: most recent date with units_sold > 0), keyed by
    product_id when pooled, or (product_id, channel_id) when channel-scoped.
    Reads from daily_product_metrics (Phase 3's materialized table) — never
    order_lines directly."""
    velocity_q = db.query(
        DailyProductMetric.product_id,
        DailyProductMetric.channel_id,
        DailyProductMetric.date,
        DailyProductMetric.units_sold,
    ).filter(
        DailyProductMetric.business_id == business_id,
        DailyProductMetric.product_id.in_(product_ids),
        DailyProductMetric.date >= window_start,
        DailyProductMetric.date <= as_of_date,
    )
    if channel_id is not None:
        velocity_q = velocity_q.filter(DailyProductMetric.channel_id == channel_id)

    units_sold_by_key: dict = defaultdict(int)
    last_sale_by_key: dict = {}
    for product_id, ch_id, d, units in velocity_q.all():
        key = (product_id, ch_id) if channel_id is not None else product_id
        units = units or 0
        units_sold_by_key[key] += units
        if units > 0 and (key not in last_sale_by_key or d > last_sale_by_key[key]):
            last_sale_by_key[key] = d

    return units_sold_by_key, last_sale_by_key


# ==============================================================================
# 3. METRIC CALCULATIONS (pure functions — no DB access, no side effects)
# ==============================================================================


def calculate_average_daily_velocity(units_sold_in_window: int, window_days: int) -> Decimal:
    """Average Daily Sales Velocity = units sold in the trailing window /
    window length in days.

    Returns full (unrounded) precision — callers round only for display;
    coverage_days and reorder_qty below are computed from this un-rounded
    value so those two numbers stay internally consistent with each other."""
    return Decimal(units_sold_in_window) / Decimal(window_days)


def calculate_coverage_days(latest_stock: int, avg_daily_velocity: Decimal) -> Decimal | None:
    """Coverage Days = latest stock / average daily sales velocity.

    Returns None when velocity is 0 — "no recent sales, can't estimate how
    long stock will last" is a distinct, meaningful state from "0 days,
    about to run out," so it must not collapse into a number."""
    if avg_daily_velocity <= 0:
        return None
    return Decimal(latest_stock) / avg_daily_velocity


def calculate_days_since_last_sale(
    as_of_date: date, last_sale_date: date | None, first_seen_date: date | None
) -> tuple[int | None, str]:
    """Days Since Last Sale — the aging proxy. Priority order:
    1. as_of_date - last_sale_date, if the product has ever sold
       (aging_basis = 'last_sale')
    2. as_of_date - first_seen_date, if it never sold but we've seen it in
       an inventory snapshot (aging_basis = 'first_seen') — this stops a
       genuinely brand-new never-sold product from being misread the same
       way as a long-idle one.
    3. (None, 'unknown') if neither exists."""
    if last_sale_date is not None:
        return (as_of_date - last_sale_date).days, "last_sale"
    if first_seen_date is not None:
        return (as_of_date - first_seen_date).days, "first_seen"
    return None, "unknown"


def calculate_inventory_value(latest_stock: int, cost_price: Decimal | None) -> Decimal:
    """Inventory Value = latest stock x product cost price.
    Treats a missing cost_price as 0 rather than erroring — a product
    without a cost price shouldn't crash the whole inventory read, it just
    can't contribute a meaningful value figure."""
    return Decimal(latest_stock) * (cost_price or Decimal("0"))


def calculate_reorder_quantity(
    status: str, avg_daily_velocity: Decimal, target_coverage_days: int, latest_stock: int
) -> int | None:
    """Reorder Qty = max(0, ceil(target coverage stock - latest stock)),
    where target coverage stock = avg_daily_velocity x target_coverage_days.
    Only computed when status indicates the product actually needs
    reordering, and only when there's a velocity to size the order against
    — a product with zero recent sales can't get a meaningful reorder
    quantity even if it happens to be out of stock."""
    if status not in ("stockout_risk", "out_of_stock") or avg_daily_velocity <= 0:
        return None
    target_stock = avg_daily_velocity * Decimal(target_coverage_days)
    return max(0, math.ceil(target_stock - latest_stock))


def classify_inventory_status(
    latest_stock: int,
    coverage_days: Decimal | None,
    aging_days: int | None,
    stockout_threshold_days: int,
    slow_moving_threshold_days: int,
    overstock_threshold_days: int,
) -> str:
    """Classifies a product into exactly one status, checked in priority
    order (first match wins):
      1. out_of_stock   — latest_stock == 0
      2. stockout_risk   — coverage_days <= stockout_threshold_days
      3. slow_moving      — aging_days >= slow_moving_threshold_days (and stock > 0)
      4. overstock        — coverage_days >= overstock_threshold_days
      5. healthy           — none of the above"""
    if latest_stock == 0:
        return "out_of_stock"
    if coverage_days is not None and coverage_days <= stockout_threshold_days:
        return "stockout_risk"
    if aging_days is not None and aging_days >= slow_moving_threshold_days:
        return "slow_moving"
    if coverage_days is not None and coverage_days >= overstock_threshold_days:
        return "overstock"
    return "healthy"
