"""Domain-level exceptions, kept separate from FastAPI's HTTPException so
business logic (services/) doesn't need to import FastAPI at all."""


class CommerceIQError(Exception):
    """Base class for all domain errors."""


class NotFoundError(CommerceIQError):
    def __init__(self, entity: str, identifier: object):
        self.entity = entity
        self.identifier = identifier
        super().__init__(f"{entity} with id={identifier} not found")


class DuplicateEntityError(CommerceIQError):
    def __init__(self, entity: str, detail: str = ""):
        self.entity = entity
        super().__init__(f"{entity} already exists. {detail}".strip())


class ValidationFailedError(CommerceIQError):
    def __init__(self, detail: str):
        super().__init__(detail)


class IngestionRowError(CommerceIQError):
    """A single CSV row could not be processed (bad data, unmapped SKU,
    etc). The pipeline catches this per-row so one bad row doesn't fail
    the whole import batch — it's recorded on ImportRawRow.error_message
    and counted in ImportBatch.error_count."""
    def __init__(self, detail: str):
        super().__init__(detail)


class DuplicateRowSkipped(CommerceIQError):
    """Raised when a row is a legitimate duplicate of already-imported
    data (e.g. re-uploading the same sales file). This is NOT counted as
    an error — it's expected, intentional dedup behavior."""
    def __init__(self, detail: str = "duplicate row, skipped"):
        super().__init__(detail)
