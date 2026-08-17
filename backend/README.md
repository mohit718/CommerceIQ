# CommerceIQ Backend — Phase 1 Boilerplate

Unified commerce data → analytics → insights. This is the Phase 1 (M1 — Unified
Commerce Database) foundation: schema, auth, tenancy enforcement, and one fully
working vertical slice (auth + products) that every later module follows.

## What's implemented

- **14 SQLAlchemy models** matching the finalized schema (businesses, users,
  products, channels, channel_products, orders, order_lines, returns,
  inventory_snapshots, import_batches, import_raw_rows, daily_product_metrics,
  daily_channel_metrics, product_insights)
- **JWT auth** with `business_id` embedded in every token (`app/core/security.py`)
- **Tenant isolation dependency** (`app/shared/tenancy.py`) — every route
  depends on `get_current_context()` and must filter queries by `business_id`
- **Alembic** wired to autogenerate from the models (`alembic/env.py`)
- **Working endpoints**: signup, login, me, products CRUD, channel mapping,
  channels, orders (read), inventory snapshots (read), import upload (stub)
- **Docker Compose**: API + Postgres + Redis for local dev
- **Tests**: signup → login → tenant-scoped product creation, verified passing

## Not yet implemented (later phases, per the roadmap)

- CSV parsing / normalization pipeline (Phase 2) — `app/ingestion/` is scaffolded
  but empty; `POST /imports` currently only creates the batch record
- Analytics aggregation jobs that populate `daily_product_metrics` (Phase 3)
- Insight rule engine that populates `product_insights` (Phase 5)
- Celery job wiring (Redis is running in docker-compose, not yet consumed)

## Local setup

### Option A — Docker (recommended)

```bash
cp .env.example .env
# edit .env if needed — the compose file overrides DATABASE_URL/REDIS_URL
# to point at the `db`/`redis` service names automatically

docker compose up --build
```

API available at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

Then run the first migration inside the running container:

```bash
docker compose exec api alembic revision --autogenerate -m "initial schema"
docker compose exec api alembic upgrade head
```

### Option B — Local Python (no Docker)

Requires a running Postgres instance.

```bash
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# set DATABASE_URL to your local Postgres, e.g.:
# postgresql+psycopg2://commerceiq:commerceiq@localhost:5432/commerceiq

alembic revision --autogenerate -m "initial schema"
alembic upgrade head

uvicorn app.main:app --reload
```

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Tests run against in-memory SQLite (no Postgres needed) — the one Postgres-only
type (`JSONB` on `import_raw_rows`) is declared with a SQLite-compatible
fallback via `.with_variant()` specifically so this works.

## Try it end-to-end

```bash
# 1. Sign up (creates a business + owner user, returns a JWT)
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"business_name": "Boat Lifestyle Retail", "email": "owner@boat.com", "password": "supersecret123"}'

# 2. Use the returned access_token for authenticated requests
curl -X POST http://localhost:8000/api/v1/products \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"sku": "MASTER-AD141-BLK", "name": "Airdopes 141 Black", "brand": "boAt", "selling_price": "1299.00"}'

curl http://localhost:8000/api/v1/products -H "Authorization: Bearer <token>"
```

## Project structure

```
app/
├── main.py                  # FastAPI app, router wiring
├── core/                    # config, database session, security (JWT/hashing)
├── models/                  # SQLAlchemy models — one source of truth for schema
├── schemas/                 # Pydantic request/response models
├── modules/                 # one package per business domain (auth, products, ...)
│   └── <module>/
│       ├── router.py        # FastAPI routes
│       └── service.py       # business logic (kept separate from routing)
├── ingestion/                # CSV parsers + normalization (Phase 2)
└── shared/                  # exceptions, logging, tenancy enforcement
alembic/                     # migrations — run `alembic revision --autogenerate`
tests/                       # pytest, in-memory SQLite
```

## Key architectural rule

**Every query in every module must filter by `business_id`.** The
`RequestContext` from `app/shared/tenancy.py` carries it, decoded from the JWT.
This is the entire tenant-isolation guarantee at this stage (Postgres Row-Level
Security is a future hardening layer, not yet applied). When adding a new
route, copy the pattern in `app/modules/products/router.py`.
