import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

# In-memory SQLite for fast unit tests. NOTE: JSONB (used by ImportRawRow)
# is Postgres-specific — the model falls back to plain JSON on SQLite via
# a `.with_variant()` type, so it's safe here. Integration tests that need
# real Postgres-only behavior (e.g. JSONB containment queries) should run
# against a real Postgres test database instead.
#
# StaticPool is required: plain sqlite:///:memory: opens a *new* empty
# in-memory database per connection, so without a shared pool, the schema
# created in the fixture and the schema seen by the app's request-scoped
# session would silently be two different databases.
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
