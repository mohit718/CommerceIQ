from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class InsightResponse(BaseModel):
    id: int
    product_id: int
    sku: str
    product_name: str
    type: str
    severity: str
    confidence: Decimal
    message: str
    recommendation: str | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
