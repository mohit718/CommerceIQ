from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class OverviewResponse(BaseModel):
    start_date: date
    end_date: date
    revenue: Decimal  # net revenue — the headline metric per Phase 0 decision
    gross_revenue: Decimal
    orders: int
    units: int
    average_order_value: Decimal
    contribution_profit: Decimal
    revenue_growth_pct: float | None  # None when there's no prior-period baseline
    profit_growth_pct: float | None


class ChannelBreakdown(BaseModel):
    channel_id: int
    channel_name: str
    revenue: Decimal
    gross_revenue: Decimal
    orders: int
    units: int
    contribution_profit: Decimal
    growth_pct: float | None


class ProductBreakdown(BaseModel):
    product_id: int
    sku: str
    name: str
    units: int
    revenue: Decimal
    contribution_profit: Decimal
    realized_price: Decimal
    growth_pct: float | None


class RecomputeRequest(BaseModel):
    start_date: date
    end_date: date
    channel_id: int | None = None
