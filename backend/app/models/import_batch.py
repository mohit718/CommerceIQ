from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base

# Renders as JSONB on Postgres (indexable, queryable) and falls back to
# plain JSON on SQLite so the unit test suite can run without a real DB.
JSONVariant = JSONB().with_variant(JSON(), "sqlite")


class ImportBatch(Base):
    """Metadata about one file-upload event. Does NOT hold row content —
    see ImportRawRow for that. storage_path points at the literal original
    file (object storage) so a buggy parser can be fixed and rows re-parsed
    from scratch, not just re-validated from already-parsed JSON."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(500))
    import_type: Mapped[str] = mapped_column(String(20), nullable=False)  # sales | inventory | products
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|completed|failed
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_rows: Mapped[list["ImportRawRow"]] = relationship(back_populates="batch")


class ImportRawRow(Base):
    """The untouched original data, one row per CSV row, preserved as JSONB
    before any cleansing/normalization. Enables replaying failed rows after
    fixing a mapping/validation rule, without re-uploading the source file."""

    __tablename__ = "import_raw_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    processed: Mapped[bool] = mapped_column(default=False)
    error_message: Mapped[str | None] = mapped_column(String(500))

    batch: Mapped["ImportBatch"] = relationship(back_populates="raw_rows")
