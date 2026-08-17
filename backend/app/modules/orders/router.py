from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Order
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("")
def list_orders(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Raw order listing. Aggregated analytics (revenue, growth, AOV) land
    in the analytics module once daily_product_metrics is populated
    (Phase 3) — this endpoint stays intentionally simple for now."""
    orders = db.query(Order).filter(Order.business_id == context.business_id).limit(100).all()
    return [
        {
            "id": o.id,
            "channel_id": o.channel_id,
            "external_order_id": o.external_order_id,
            "order_date": o.order_date,
        }
        for o in orders
    ]
