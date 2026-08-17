from decimal import Decimal

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    sku: str = Field(..., max_length=100)
    name: str = Field(..., max_length=255)
    brand: str | None = None
    category: str | None = None
    cost_price: Decimal | None = None
    selling_price: Decimal | None = None


class ProductResponse(ProductCreate):
    id: int
    business_id: int

    model_config = {"from_attributes": True}


class ChannelProductMappingCreate(BaseModel):
    product_id: int
    channel_id: int
    external_product_id: str | None = None
    external_sku: str | None = None
    external_name: str | None = None
    mapping_method: str = "manual"
