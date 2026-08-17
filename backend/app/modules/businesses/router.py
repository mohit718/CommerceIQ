from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Business
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/businesses", tags=["businesses"])


@router.get("/me")
def get_my_business(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    business = db.query(Business).filter(Business.id == context.business_id).first()
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
    return {"id": business.id, "name": business.name, "created_at": business.created_at}
