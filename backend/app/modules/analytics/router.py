from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.jobs.analytics_jobs import recompute_metrics_for_dates
from app.modules.analytics import service
from app.schemas.analytics import ChannelBreakdown, OverviewResponse, ProductBreakdown, RecomputeRequest
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewResponse)
def overview(
    start_date: date | None = Query(None, description="Defaults to trailing 30 days"),
    end_date: date | None = Query(None),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.get_overview(db, context.business_id, start_date, end_date)


@router.get("/channels", response_model=list[ChannelBreakdown])
def channels(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.get_channel_breakdown(db, context.business_id, start_date, end_date)


@router.get("/products", response_model=list[ProductBreakdown])
def products(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    sort_by: str = Query("revenue", pattern="^(revenue|units|growth)$"),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.get_product_breakdown(db, context.business_id, start_date, end_date, sort_by)


@router.post("/recompute", status_code=status.HTTP_202_ACCEPTED)
def recompute(
    payload: RecomputeRequest,
    background_tasks: BackgroundTasks,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Manual backfill trigger — e.g. after correcting a product mapping or
    editing historical order data. Runs in the background like ingestion
    does, using its own DB session (see app/jobs/ingestion_jobs.py for why)."""
    dates: set[date] = set()
    d = payload.start_date
    while d <= payload.end_date:
        dates.add(d)
        d += timedelta(days=1)

    def _run() -> None:
        session = SessionLocal()
        try:
            recompute_metrics_for_dates(session, context.business_id, dates, channel_id=payload.channel_id)
        finally:
            session.close()

    background_tasks.add_task(_run)
    return {"status": "recompute_scheduled", "dates_queued": len(dates)}
