from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ImportBatch
from app.shared.tenancy import RequestContext, get_current_context

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_import(
    channel_id: int = Form(...),
    import_type: str = Form(...),  # sales | inventory | products
    file: UploadFile = File(...),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Creates the import_batch record and stores the raw file. The actual
    CSV parsing -> import_raw_rows -> validation -> normalization pipeline
    (ingestion/csv, ingestion/normalization) is implemented in Phase 2 and
    triggered from here as a background job."""
    batch = ImportBatch(
        business_id=context.business_id,
        channel_id=channel_id,
        file_name=file.filename,
        import_type=import_type,
        status="pending",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    # TODO (Phase 2): persist `file` to object storage -> batch.storage_path,
    # then enqueue app.jobs.ingestion_jobs.process_import_batch(batch.id)

    return {"import_batch_id": batch.id, "status": batch.status}


@router.get("/{batch_id}")
def get_import_status(
    batch_id: int,
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    batch = db.query(ImportBatch).filter(
        ImportBatch.id == batch_id,
        ImportBatch.business_id == context.business_id,
    ).first()
    if not batch:
        return {"error": "not found"}
    return {
        "id": batch.id,
        "status": batch.status,
        "row_count": batch.row_count,
        "error_count": batch.error_count,
    }
