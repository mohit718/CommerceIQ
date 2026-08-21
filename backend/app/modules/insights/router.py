from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.jobs.insight_jobs import generate_insights_for_business
from app.modules.insights import service
from app.schemas.insights import InsightResponse
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=list[InsightResponse])
def list_insights(
    status: str = Query("open", pattern="^(open|resolved|all)$"),
    type: str | None = Query(None),
    severity: str | None = Query(None),
    product_id: int | None = Query(None),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    return service.list_insights(db, context.business_id, status, type, severity, product_id)


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(
    background_tasks: BackgroundTasks,
    context: RequestContext = Depends(get_current_context),
):
    """Manual trigger — e.g. for a backfill on pre-existing data, same
    pattern as POST /analytics/recompute. Runs with its own DB session
    since it executes after the response is sent (see
    app/jobs/ingestion_jobs.py for why)."""

    def _run() -> None:
        session = SessionLocal()
        try:
            generate_insights_for_business(session, context.business_id)
        finally:
            session.close()

    background_tasks.add_task(_run)
    return {"status": "generation_scheduled"}
