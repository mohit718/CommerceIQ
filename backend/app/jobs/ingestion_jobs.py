"""
Entrypoint for FastAPI BackgroundTasks. This is the only place that opens
its own SessionLocal() directly rather than using Depends(get_db) — a
background task runs after the HTTP response is sent, so the request-scoped
session is already closed by the time this executes.

process_batch() itself (app/ingestion/normalization/pipeline.py) takes a
plain Session and knows nothing about BackgroundTasks or HTTP — that's what
keeps it directly unit-testable.
"""
from app.core.database import SessionLocal
from app.ingestion.normalization.pipeline import process_batch
from app.models import ImportBatch
from app.shared.logging import get_logger

logger = get_logger(__name__)


def run_import_job(batch_id: int) -> None:
    db = SessionLocal()
    try:
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if not batch:
            logger.warning("run_import_job: batch %s not found", batch_id)
            return

        batch.status = "processing"
        db.commit()

        process_batch(db, batch)
        logger.info(
            "import batch %s completed: %s rows, %s errors",
            batch_id, batch.row_count, batch.error_count,
        )

    except Exception:
        logger.exception("import batch %s failed", batch_id)
        db.rollback()
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if batch:
            batch.status = "failed"
            db.commit()

    finally:
        db.close()
