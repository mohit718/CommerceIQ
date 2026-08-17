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
