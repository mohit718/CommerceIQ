"""
Scaffolded now for FK shape; populated starting Phase 5 (Insight Engine).

This is the structured artifact the AI layer (Section 14) reads and narrates
— severity/confidence/recommendation are always computed deterministically
upstream, never by the LLM.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProductInsight(Base):
    __tablename__ = "product_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. STOCKOUT_RISK, DEMAND_SPIKE
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # low | medium | high | critical
    confidence: Mapped[float] = mapped_column(Numeric(3, 2))  # 0.00 - 1.00
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    recommendation: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
