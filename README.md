# PartFlow

Internal manufacturing tracking system for barcode-driven movement of
production quantities through the factory.

This repository currently contains the **Phase 1 Repository Foundation**
only:

- `frontend/` — React + TypeScript (Vite) with a single backend-connectivity screen
- `backend/` — FastAPI with one operational health endpoint (`GET /api/health`)
- PostgreSQL 16 with Alembic migration wiring (no-op baseline revision)
- Docker Compose development stack with health checks

**Phase 1 contains no production domain schema and no business
workflows.** Domain entities, barcode scanning, PO intake, and all
tracking behavior arrive in later phases. See
`docs/IMPLEMENTATION_ROADMAP.md` for phase boundaries and
`docs/PROJECT_PROFILE.md` for the authoritative project specification.

## Prerequisites

- Docker with Docker Compose v2 (`docker compose`)
- For development outside Docker (optional): Node.js 22+, Python 3.12+, [uv](https://docs.astral.sh/uv/)

## Environment setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Adjust values in `.env` if needed. These are development-only credentials; the real `.env` is git-ignored and must never contain shared or production secrets.

Dependency lockfiles (`backend/uv.lock`, `frontend/package-lock.json`)
are committed; the Docker builds install strictly from them
(`uv sync --frozen`, `npm ci`). No extra bootstrap step is required on a
clean checkout.

## Start the complete stack

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Health endpoint: <http://localhost:8000/api/health> (also proxied at <http://localhost:5173/api/health>)

The frontend shows a single connectivity screen: loading, connected, or backend-unavailable.

Stop the stack with `Ctrl+C`, then:

```bash
docker compose down
```

(`docker compose down -v` additionally deletes the PostgreSQL data volume — normally not needed.)

After changing backend or frontend dependencies
(`pyproject.toml`/`uv.lock`, `package.json`/`package-lock.json`),
rebuild and renew the anonymous dependency volumes so stale
`.venv`/`node_modules` contents do not shadow the rebuilt images:

```bash
docker compose up --build -V
```

## Database migrations (Alembic)

Apply migrations (currently a no-op repository-foundation baseline):

```bash
docker compose exec backend uv run alembic upgrade head
```

## Quality commands

The Linux containers are the canonical environment for all quality
gates. Run these with the Compose stack up (`docker compose up -d`).

### Backend

```bash
docker compose exec backend uv run ruff format --check .   # formatting check (ruff format . to fix)
docker compose exec backend uv run ruff check .            # lint
docker compose exec backend uv run mypy app tests          # type check (strict)
docker compose exec backend uv run pytest                  # tests (see below)
docker compose exec backend uv run alembic upgrade head    # migrations
```

The pytest suite contains two kinds of tests:

- `tests/test_health.py` — health-endpoint **behavior** tests that mock
  `ping_database` (success and safe 503 responses; no database needed).
- `tests/test_database_connectivity.py` — one **integration** test that
  calls `GET /api/health` through the real application wiring with no
  mocking, so it requires the PostgreSQL service to be reachable via
  `DATABASE_URL`.

### Frontend

```bash
docker compose exec frontend npm run format        # format with Prettier
docker compose exec frontend npm run format:check  # formatting check
docker compose exec frontend npm run lint          # ESLint
docker compose exec frontend npm run typecheck     # TypeScript (strict, no emit)
docker compose exec frontend npm run test          # Vitest + React Testing Library
docker compose exec frontend npm run build         # type check + production build
```

### Running directly on the host (optional, best effort)

The same commands can be run without the `docker compose exec …` prefix
from `backend/` (with uv) or `frontend/` (with Node 22), but the host is
not the canonical environment: toolchain versions and OS behavior may
differ from the Linux containers used by Docker and CI. Backend notes
for host runs:

- `DATABASE_URL` must be resolvable. `backend/tests/conftest.py`
  defaults it to the development database from `.env.example`; anything
  outside pytest (e.g. `uv run alembic upgrade head`) needs the variable
  set explicitly or a `backend/.env` file.
- The pytest integration test performs a real database check through
  `GET /api/health`, so the Compose `db` service must be running (its
  port 5432 is published to the host). If your `.env` uses custom
  credentials, set `DATABASE_URL` to match before running pytest on the
  host.

When host results disagree with container results, the container
results win.

## Docker development notes

When using the Docker development environment, treat the Linux
containers as the canonical development environment.

### Frontend formatting

Run Prettier **inside the frontend container** before checking
formatting:

```bash
docker compose exec frontend npm run format
```

Then verify:

```bash
docker compose exec frontend npm run format:check
```

Running Prettier directly on the host machine may produce different
results from the Linux container used by Docker and GitHub Actions.

### Complete frontend quality gate

```bash
docker compose exec frontend sh -lc "npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build"
```

### Complete backend quality gate

```bash
docker compose exec backend sh -lc "uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest"
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs the same quality gates
on every push to `main` and every pull request: backend format check,
lint, mypy, Alembic migration, and pytest (mocked behavior tests plus
the real PostgreSQL integration test) against PostgreSQL 16; frontend
format check, lint, typecheck, tests, and production build. A separate
`docker` job verifies that the Docker Compose development images build
(`docker compose build`).

## Repository layout

```text
frontend/          Vite + React + TypeScript app (connectivity screen only)
backend/
  app/api/         HTTP routes (health endpoint)
  app/core/        configuration (pydantic-settings)
  app/infrastructure/  database engine and connectivity check
  tests/           pytest suite
  alembic/         migration environment and baseline revision
compose.yaml       development stack (db, backend, frontend)
docs/              canonical project documentation
```
