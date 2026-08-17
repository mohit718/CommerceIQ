from pydantic import BaseModel
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Channel
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelCreate(BaseModel):
    name: str
    type: str  # marketplace | d2c | offline | erp


class ChannelResponse(ChannelCreate):
    id: int
    business_id: int

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ChannelResponse])
def list_channels(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return db.query(Channel).filter(Channel.business_id == context.business_id).all()


@router.post("", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreate,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    channel = Channel(business_id=context.business_id, **payload.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel
