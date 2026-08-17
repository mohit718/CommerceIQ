from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InventorySnapshot(Base):
    """Dated stock-level records rather than a single mutable 'current stock'
    field. This is what makes inventory aging, coverage-days trends, and
    stockout-risk-over-time computable (Phase 4) — a single current-stock
    column can't answer 'how has coverage trended over the last 30 days'."""

    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
