# PartFlow

Internal manufacturing tracking system for barcode-driven movement of production quantities through the factory.

This repository currently contains the **Phase 1 Repository Foundation** only:

- `frontend/` — React + TypeScript (Vite) with a single backend-connectivity screen
- `backend/` — FastAPI with one operational health endpoint (`GET /api/health`)
- PostgreSQL 16 with Alembic migration wiring (no-op baseline revision)
- Docker Compose development stack with health checks

**Phase 1 contains no production domain schema and no business workflows.** Domain entities, barcode scanning, PO intake, and all tracking behavior arrive in later phases. See `docs/IMPLEMENTATION_ROADMAP.md` for phase boundaries and `docs/PROJECT_PROFILE.md` for the authoritative project specification.

## Prerequisites

- Docker with Docker Compose v2 (`docker compose`)
- For development outside Docker (optional): Node.js 22+, Python 3.12+, [uv](https://docs.astral.sh/uv/)

## Environment setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Adjust values in `.env` if needed. These are development-only credentials; the real `.env` is git-ignored and must never contain shared or production secrets.

3. First-time dependency lock (only if the lockfiles are not yet present in the repository):

   ```bash
   cd backend && uv lock && cd ..
   cd frontend && npm install && cd ..
   ```

   `backend/uv.lock` and `frontend/package-lock.json` must be committed; the Docker builds install strictly from them (`uv sync --frozen`, `npm ci`).

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

## Database migrations (Alembic)

Apply migrations (currently a no-op repository-foundation baseline):

```bash
docker compose exec backend uv run alembic upgrade head
```

Outside Docker (from `backend/`, with `DATABASE_URL` set): `uv run alembic upgrade head`.

## Quality commands

### Backend (run from `backend/`, or via `docker compose exec backend …`)

```bash
uv run ruff format --check .   # formatting check (ruff format . to fix)
uv run ruff check .            # lint
uv run mypy app tests          # type check (strict)
uv run pytest                  # tests (connectivity test needs PostgreSQL running)
uv run alembic upgrade head    # migrations
```

### Frontend (run from `frontend/`, or via `docker compose exec frontend …`)

```bash
npm run format        # format with Prettier
npm run format:check  # formatting check
npm run lint          # ESLint
npm run typecheck     # TypeScript (strict, no emit)
npm run test          # Vitest + React Testing Library
npm run build         # type check + production build
```

## Repository layout

```
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
