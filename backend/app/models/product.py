from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Product(Base):
    """The master catalog entry — the single canonical product that every
    channel's listing eventually maps to (see ChannelProduct)."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    selling_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    channel_products: Mapped[list["ChannelProduct"]] = relationship(back_populates="product")


class Channel(Base):
    """A sales channel this business sells through (Amazon, Flipkart, etc.)."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # marketplace | d2c | offline | erp


class ChannelProduct(Base):
    """The SKU-mapping table: translates a channel's raw product identity
    (external_sku / external_product_id) into a resolved master Product.
    This is what lets ingestion resolve 'B08XYZ123' -> product_id=101."""

    __tablename__ = "channel_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    external_product_id: Mapped[str | None] = mapped_column(String(255))
    external_sku: Mapped[str | None] = mapped_column(String(255), index=True)
    external_name: Mapped[str | None] = mapped_column(String(255))
    mapping_method: Mapped[str] = mapped_column(String(20), default="manual")  # exact | manual | alias
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="channel_products")
