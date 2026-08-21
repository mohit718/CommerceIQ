"""
Entrypoint for FastAPI BackgroundTasks. This is the only place that opens
its own SessionLocal() directly rather than using Depends(get_db) — a
background task runs after the HTTP response is sent, so the request-scoped
session is already closed by the time this executes.

process_batch() itself (app/ingestion/normalization/pipeline.py) takes a
plain Session and knows nothing about BackgroundTasks or HTTP — that's what
keeps it directly unit-testable. Same for recompute_metrics_for_dates()
(app/jobs/analytics_jobs.py), which this calls immediately after ingestion
so the dashboard reflects a new upload without waiting for a scheduled job.
"""
from app.core.database import SessionLocal
from app.ingestion.normalization.pipeline import process_batch
from app.jobs.analytics_jobs import recompute_metrics_for_dates
from app.jobs.insight_jobs import generate_insights_for_business
from app.models import ImportBatch
from app.shared.logging import get_logger

logger = get_logger(__name__)

# Only these import types produce order_lines/returns rows that feed
# daily_product_metrics / daily_channel_metrics. Inventory and product
# catalog imports don't affect sales analytics.
ANALYTICS_RELEVANT_IMPORT_TYPES = {"sales", "returns"}

# These three can change the signals insights depend on (sales/returns feed
# Phase 3 metrics; inventory feeds Phase 4 stock levels). Product catalog
# imports don't change either, so they're excluded.
INSIGHT_RELEVANT_IMPORT_TYPES = {"sales", "returns", "inventory"}


def run_import_job(batch_id: int) -> None:
    db = SessionLocal()
    try:
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if not batch:
            logger.warning("run_import_job: batch %s not found", batch_id)
            return

        batch.status = "processing"
        db.commit()

        touched_dates = process_batch(db, batch)
        logger.info(
            "import batch %s completed: %s rows, %s errors",
            batch_id, batch.row_count, batch.error_count,
        )

        if batch.import_type in ANALYTICS_RELEVANT_IMPORT_TYPES and touched_dates:
            recompute_metrics_for_dates(
                db, batch.business_id, touched_dates, channel_id=batch.channel_id
            )

        if batch.import_type in INSIGHT_RELEVANT_IMPORT_TYPES:
            generate_insights_for_business(db, batch.business_id)

    except Exception:
        logger.exception("import batch %s failed", batch_id)
        db.rollback()
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if batch:
            batch.status = "failed"
            db.commit()

    finally:
        db.close()
