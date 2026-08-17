"""
Database engine + session management.

Single engine for the whole app (connection pooling handled by SQLAlchemy).
`get_db` is the FastAPI dependency every route uses to obtain a request-scoped
session — it always closes the session, even if the request raises.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # avoids "server closed the connection" after idle periods
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
