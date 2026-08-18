from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.ingestion.storage import get_storage
from app.jobs.ingestion_jobs import run_import_job
from app.models import Channel, ImportBatch, ImportRawRow
from app.shared.tenancy import RequestContext, get_current_context

VALID_IMPORT_TYPES = {"sales", "inventory", "products", "returns"}

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_import(
    background_tasks: BackgroundTasks,
    channel_id: int = Form(...),
    import_type: str = Form(...),  # sales | inventory | products
    file: UploadFile = File(...),
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Saves the uploaded file, creates the import_batch record, and kicks
    off processing in the background — the response returns immediately
    with status 'pending' rather than blocking on the whole file. Poll
    GET /imports/{id} for progress."""
    if import_type not in VALID_IMPORT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"import_type must be one of {sorted(VALID_IMPORT_TYPES)}",
        )

    channel = db.query(Channel).filter(
        Channel.id == channel_id,
        Channel.business_id == context.business_id,
    ).first()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    content = await file.read()
    storage_path = get_storage().save(context.business_id, file.filename, content)

    batch = ImportBatch(
        business_id=context.business_id,
        channel_id=channel_id,
        file_name=file.filename,
        storage_path=storage_path,
        import_type=import_type,
        status="pending",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    background_tasks.add_task(run_import_job, batch.id)

    return {"import_batch_id": batch.id, "status": batch.status}


@router.get("/unmapped")
def list_unmapped_rows(
    context: RequestContext = Depends(get_current_context),
    db: Session = Depends(get_db),
):
    """Rows that failed because their SKU has no product mapping yet —
    surfaced here so a seller can resolve them via POST /products/mapping
    without re-uploading the file (the raw row is preserved, see
    import_raw_rows in Phase 1)."""
    rows = (
        db.query(ImportRawRow)
        .join(ImportBatch, ImportRawRow.import_batch_id == ImportBatch.id)
        .filter(
            ImportBatch.business_id == context.business_id,
            ImportRawRow.processed == False,  # noqa: E712
            ImportRawRow.error_message.like("UNMAPPED_SKU:%"),
        )
        .all()
    )
    return [
        {
            "import_raw_row_id": r.id,
            "import_batch_id": r.import_batch_id,
            "channel_id": r.batch.channel_id,
            "unmapped_sku": r.error_message.split("UNMAPPED_SKU:", 1)[1],
            "raw_data": r.raw_data,
        }
        for r in rows
    ]


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return {
        "id": batch.id,
        "status": batch.status,
        "row_count": batch.row_count,
        "error_count": batch.error_count,
    }
