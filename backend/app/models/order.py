from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Order(Base):
    """Order header — one row per real customer order. Kept separate from
    OrderLine because order-level metrics (Orders count, AOV) must count
    distinct orders, not line items."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    external_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    customer_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order")


class OrderLine(Base):
    """The core sales fact table. Nearly every revenue/profit/pricing metric
    (Net Revenue, Realized Price, Contribution Profit) is derived from these
    rows. channel_product_id is kept alongside the resolved product_id so a
    historical line can always be traced back to its raw channel SKU, even
    if the mapping is corrected later."""

    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    channel_product_id: Mapped[int | None] = mapped_column(ForeignKey("channel_products.id"))

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="lines")


class Return(Base):
    """Linked to the originating order line so refunds can be netted against
    the exact sale (channel, price, discount) they came from."""

    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
