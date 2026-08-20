from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class InventoryStatusItem(BaseModel):
    product_id: int
    sku: str
    name: str
    channel_id: int | None
    channel_name: str | None
    latest_stock: int
    snapshot_date: date | None
    avg_daily_velocity: Decimal
    coverage_days: Decimal | None
    last_sale_date: date | None
    first_seen_date: date | None
    aging_days: int | None
    aging_basis: str  # 'last_sale' | 'first_seen' | 'unknown'
    inventory_value: Decimal
    status: str  # out_of_stock | stockout_risk | slow_moving | overstock | healthy
    reorder_qty: int | None


class InventoryOverviewResponse(BaseModel):
    as_of_date: date
    total_inventory_value: Decimal
    stockout_risk_count: int
    out_of_stock_count: int
    slow_moving_count: int
    overstock_count: int
    slow_moving_locked_value: Decimal
