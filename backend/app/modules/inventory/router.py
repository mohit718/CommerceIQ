from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import InventorySnapshot
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/snapshots")
def list_snapshots(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Raw snapshot listing. Coverage-days / stockout-risk calculations are
    built in Phase 4 (Inventory Intelligence) on top of this data."""
    snapshots = (
        db.query(InventorySnapshot)
        .filter(InventorySnapshot.business_id == context.business_id)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "product_id": s.product_id,
            "channel_id": s.channel_id,
            "snapshot_date": s.snapshot_date,
            "quantity_on_hand": s.quantity_on_hand,
        }
        for s in snapshots
    ]
