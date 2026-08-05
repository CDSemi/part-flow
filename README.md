# PartFlow

Internal manufacturing tracking system for barcode-driven movement of
production quantities through the factory.

This repository currently contains the **Phase 1 Repository Foundation**
and the **Phase 2 Frontend Design System and Application Shell**:

- `frontend/` — React + TypeScript (Vite): design tokens with switchable
  Dark/Light themes (Dark default), application shell with routing, the
  nine approved GUI views with development-only mock data, and the real
  `/api/health` connectivity integration
- `backend/` — FastAPI with one operational health endpoint (`GET /api/health`)
- PostgreSQL 16 with Alembic migration wiring (no-op baseline revision)
- Docker Compose development stack with health checks

**Phases 1–2 contain no production domain schema and no business
workflows.** All view content is development-only mock data; barcode
resolution, Work Order intake, and all tracking behavior arrive in later phases.
See `docs/IMPLEMENTATION_ROADMAP.md` for phase boundaries,
`docs/PROJECT_PROFILE.md` for the authoritative project specification,
and `docs/GUI_DESIGN.md` (with `docs/mockups/partflow-gui-mockup-v16.html`)
for the approved target UI.

## Phase 2 frontend

Routes (meaningful URLs; browser back/forward works; unknown routes show
an application-level not-found state):

| URL | View |
|---|---|
| `/scan-station` | Scan Station — Station Selector (root `/` redirects here; never auto-redirects to a station) |
| `/scan-station/:stationId` | One Scan Station in standard mode (e.g. `/scan-station/LATHE-ST-01`); unknown or inactive Station IDs show an explicit error |
| `/scan-station/:stationId/production` | The same Scan Station in production mode — the top application navigation is hidden so operators stay on the station (presentation only, not a security boundary) |
| `/production-board` | Production Board (large display, read-only) |
| `/production-board/kiosk` | Production Board in kiosk mode — the top application navigation is hidden and the board renders its own wall-display header (presentation only) |
| `/management/area-board` | Management → Area Board (All Areas overview + per-Area detail) |
| `/management/machines` | Management → Machines (Machine lifecycle and maintenance — permission-based production master data) |
| `/management/tracking` | Management → Tracking (PN-centric) |
| `/management/work-orders` | Management → Work Orders |
| `/management/planned-routes` | Management → Planned Routes (reusable route definitions — permission-based production master data) |
| `/management/priority` | Management → Priority (Hot WO Demand ranking) |
| `/administration` | Administration |

`/management` opens the last-used sub view of the current session
(Area Board on first open). Routing is a small history-based router
(`src/app/router-core.ts` route table and resolution,
`src/app/router-context.ts` Context and `useRouter`,
`src/app/router-provider.tsx` history state and redirects,
`src/app/link.tsx` client-side `Link`) — no routing dependency was added.

Frontend structure:

- `src/styles/` — semantic design tokens (`tokens.css`; `body.dark` /
  `body.light` supply the values) and shared primitives (`global.css`).
  Component CSS consumes semantic tokens only.
- `src/app/` — shell infrastructure: router, theme provider (Dark
  default, session-only), connectivity provider with fast detection
  (browser online/offline events, ~1 s `/api/health` polling with a
  request timeout below the probe interval, recheck on tab
  focus/visibility, explicit Retry; no optimistic writes — a write is
  recorded only after the server confirms it), dev state preview.
- `src/mocks/` — the development-only mock datasets. Views read from
  here and pass data to components via props; nothing in `src/mocks`
  encodes production business rules or is written to the backend.
  Mock views and datasets are reachable only through the dev-only
  registry `src/app/dev-views.ts` (`import.meta.env.DEV`), so a
  production build excludes them entirely and every route renders an
  explicit "not connected to a production data source yet" state.
  `npm run build` verifies this by scanning the generated assets for
  known mock sentinel values (`scripts/check-production-boundary.mjs`).
  Shared view-model types live in `src/views/view-models.ts` (types
  only — production-safe).
- `src/views/<view>/` — one folder per GUI view. `src/views/scan-station/barcode.ts`
  holds the deterministic `PF:` barcode parsing (PN barcodes carry the PN
  itself — `PF:PN:<part-number>` — with case-insensitive PN identity).
- `src/components/` — genuinely shared pieces (Area dot, Hot/Type chips,
  view-state blocks, accessible mock dialog, quantity keypad, and the
  shared Area/Machine monitoring components used by both the Scan
  Station and the Area Board detail).

### Previewing UI states (development only)

Append `?state=…` to any view URL in a development build to force that
view's deterministic state:

- `?state=loading` — skeleton loading state
- `?state=empty` — empty state
- `?state=error` — error state
- `?state=long` — long-data set (over-long PNs, many rows)

Example: `http://localhost:5173/management/tracking?state=long`. The
override is gated by `import.meta.env.DEV` and does not exist in a
production build. The disconnected state is real: stop the backend (or
let the health check fail) and the shell shows the persistent OFFLINE
banner with a Retry action while production-write mock controls disable.

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

The frontend serves the Phase 2 application shell (see “Phase 2
frontend” above). The top-navigation chip shows the real backend
connectivity state: CONNECTING…, ONLINE, or OFFLINE with a persistent
banner and Retry action.

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
docker compose exec frontend npm run build         # type check + production build + mock-boundary check
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
frontend/          Vite + React + TypeScript app (Phase 2 shell + mock views)
  src/styles/      semantic design tokens and shared primitives
  src/app/         router, theme, connectivity, dev state preview
  src/mocks/       development-only mock datasets (excluded from production builds)
  src/views/       one folder per approved GUI view
  src/components/  shared presentation components
backend/
  app/api/         HTTP routes (health endpoint)
  app/core/        configuration (pydantic-settings)
  app/infrastructure/  database engine and connectivity check
  tests/           pytest suite
  alembic/         migration environment and baseline revision
compose.yaml       development stack (db, backend, frontend)
docs/              canonical project documentation
```
