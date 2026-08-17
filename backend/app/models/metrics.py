"""
Materialized analytics tables. These are NOT source of truth — they are
computed from order_lines / inventory_snapshots by scheduled jobs (Phase 3).
Scaffolded now so the FK shape is correct from day one, but they stay empty
until the analytics engine (Phase 3) is built.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyProductMetric(Base):
    __tablename__ = "daily_product_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    units_sold: Mapped[int] = mapped_column(Integer, default=0)
    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    net_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    returns_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    contribution_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    realized_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class DailyChannelMetric(Base):
    __tablename__ = "daily_channel_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    net_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    contribution_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
