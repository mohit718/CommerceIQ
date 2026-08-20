from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import InventorySnapshot
from app.modules.inventory import service
from app.schemas.inventory import InventoryOverviewResponse, InventoryStatusItem
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/snapshots")
def list_snapshots(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Raw snapshot listing."""
    snapshots = (
        db.query(InventorySnapshot)
        .filter(InventorySnapshot.business_id == context.business_id)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "product_id": s.product_id,
            "channel_id": s.channel_id,
            "snapshot_date": s.snapshot_date,
            "quantity_on_hand": s.quantity_on_hand,
        }
        for s in snapshots
    ]


@dataclass
class InventoryQueryParams:
    channel_id: int | None
    velocity_window_days: int
    stockout_threshold_days: int
    slow_moving_threshold_days: int
    overstock_threshold_days: int
    target_coverage_days: int
    as_of_date: date | None


def get_inventory_params(
    channel_id: int | None = Query(None, description="Restrict to one channel; omit to pool stock/velocity across all channels"),
    velocity_window_days: int = Query(30, ge=1, le=365, description="Trailing window used to compute average daily sales velocity"),
    stockout_threshold_days: int = Query(14, ge=1, description="Coverage days at or below this -> stockout_risk"),
    slow_moving_threshold_days: int = Query(60, ge=1, description="Aging days at or above this (with stock > 0) -> slow_moving"),
    overstock_threshold_days: int = Query(90, ge=1, description="Coverage days at or above this -> overstock"),
    target_coverage_days: int = Query(30, ge=1, description="Coverage target used to compute reorder_qty"),
    as_of_date: date | None = Query(None, description="Defaults to today"),
) -> InventoryQueryParams:
    return InventoryQueryParams(
        channel_id=channel_id,
        velocity_window_days=velocity_window_days,
        stockout_threshold_days=stockout_threshold_days,
        slow_moving_threshold_days=slow_moving_threshold_days,
        overstock_threshold_days=overstock_threshold_days,
        target_coverage_days=target_coverage_days,
        as_of_date=as_of_date,
    )


@router.get("/status", response_model=list[InventoryStatusItem])
def inventory_status(
    params: InventoryQueryParams = Depends(get_inventory_params),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.compute_inventory_status(db, context.business_id, **vars(params))


@router.get("/stockout-risks", response_model=list[InventoryStatusItem])
def stockout_risks(
    params: InventoryQueryParams = Depends(get_inventory_params),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    rows = service.compute_inventory_status(db, context.business_id, **vars(params))
    filtered = [r for r in rows if r["status"] in ("out_of_stock", "stockout_risk")]
    # most urgent first: out_of_stock (coverage_days None -> treated as -1) then ascending coverage_days
    filtered.sort(key=lambda r: r["coverage_days"] if r["coverage_days"] is not None else Decimal("-1"))
    return filtered


@router.get("/slow-moving", response_model=list[InventoryStatusItem])
def slow_moving(
    params: InventoryQueryParams = Depends(get_inventory_params),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    rows = service.compute_inventory_status(db, context.business_id, **vars(params))
    filtered = [r for r in rows if r["status"] == "slow_moving"]
    filtered.sort(key=lambda r: r["inventory_value"], reverse=True)  # biggest locked capital first
    return filtered


@router.get("/overview", response_model=InventoryOverviewResponse)
def inventory_overview(
    params: InventoryQueryParams = Depends(get_inventory_params),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.get_inventory_overview(db, context.business_id, **vars(params))
