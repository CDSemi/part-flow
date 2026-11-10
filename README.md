# PartFlow

> **Language:** English is the source of truth. [Tiếng Việt](./README.vi.md).
> See the [documentation index](./docs/README.md) and
> [deployment guide](./docs/DEPLOYMENT.md).

PartFlow is the factory's internal system for tracking production quantities as
they move through the plant. Operators scan barcodes at Scan Stations, and every
scan that changes where a quantity is or what state it is in is recorded as an
immutable Movement. Managers see in real time where every Part Number (PN) is, how
much of each Work Order (WO) is still open, and what is late.

This README introduces PartFlow to a newcomer and is the developer guide for the
repository: how to set up, run, test and inspect the application on a workstation.
It does not define business rules or the user interface; those live in the
canonical documents listed under [Where to read next](#where-to-read-next).

## Who uses PartFlow

- **Operators** work at Scan Stations on the shop floor. A Scan Station is a
  browser on a barcode workstation that an administrator has enrolled for one
  station, and each station is bound to one Area of the factory.
- **Production managers and planners** use the Management screens to enter Work
  Orders, set priorities, follow Part Numbers and correct recorded production.
- **Administrators** configure the factory (Departments, Areas, Operations,
  Machines, Scan Stations, Workers, users and roles).
- **Everyone on the floor** can read the Production Board on a wall display.

## What PartFlow can do today

Every screen of the approved user interface is implemented against the real
backend. In user terms:

- **Work Orders.** Enter a Work Order and its demand lines (what Part Number, how
  many, when), then release production quantity as a separate, explicit action,
  in one or several parts. Saving demand never creates production quantity. Work
  Orders can also be imported from a CSV or Excel (`.xlsx`) file: the file is
  checked first, and changes to existing Open or Released Work Orders are applied
  only after a typed confirmation. Completed Work Orders have a read-only history
  with search and filters.
- **Scan Stations.** Scan a Part Number, a Machine or an Area barcode and record
  what happens to the quantity: transfer it into the station's Area, assign it to a
  Machine, return it to the queue (`QUEUE`), mark the work done (`DONE`), or finish
  processing in an Area that has no Machines. Every action accepts part of a
  quantity, and quantities of one Part Number can be combined explicitly
  (`Combine quantities`). A Part Number that has no open demand can be taken in
  with `Receive Quantity`.
- **Corrections at the station.** Scrap quantity, add quantity
  (`Add more quantity`), send quantity back for repair
  (`Return quantity for repair`), and Undo a recent scan. Every correction is
  recorded; nothing in the Movement history is ever overwritten.
- **Stockroom.** Receive finished quantity into the Stockroom
  (`Receive into Stockroom`) and allocate it to Work Order demand in the standard
  order. A Work Order is complete when its demand is fully allocated. Managers can
  allocate stock later, reverse an allocation, and, with a reason, correct an
  allocation beyond the remaining demand.
- **Monitoring.** The Production Board shows every Part Number in production in a
  Department, refreshes itself and has a kiosk mode for wall displays. The Area
  Board shows every Area and what is queued, on each Machine and finished. PN
  Tracking shows one Part Number's demand, current quantities, history and audit
  trail, and is where managers adjust the remaining steps of an assigned route.
- **Priority.** Managers keep a ranked Hot list of Work Order demand lines; the
  ranking orders the boards and the stock allocation.
- **Master data.** Machines (with automatically assigned Asset Tags, maintenance
  and retirement), Part Numbers (details, image and barcode label) and Planned
  Routes (reusable sequences of Areas and Operations).
- **Workers and sign-in.** Workers identify themselves at Scan Stations with
  badges, and an Area can require a badge to confirm certain actions. Application
  users sign in with a login name and password, and what each user may do follows
  the permissions of the user's role.
- **Administration.** Factory configuration, Scan Station devices, Workers,
  users, roles and permissions, sign-in settings, Production Board display
  settings and the Due Soon policy.

Not available yet: archival and purge of old Movement history (the retention
period can be stored but nothing is archived or purged), and the Scan behavior
settings in Administration. The full implementation state is in
[`docs/IMPLEMENTATION_ROADMAP.md`](./docs/IMPLEMENTATION_ROADMAP.md).

## Where to read next

- [`docs/README.md`](./docs/README.md) — the documentation index: what each
  document is for.
- [`docs/PROJECT_PROFILE.md`](./docs/PROJECT_PROFILE.md) — the authoritative
  specification: domain terms, business rules and workflows.
- [`docs/GUI_DESIGN.md`](./docs/GUI_DESIGN.md) — the approved user interface, with
  [`docs/mockups/partflow-gui-mockup-v18.html`](./docs/mockups/partflow-gui-mockup-v18.html)
  as the visual reference.
- [`docs/IMPLEMENTATION_ROADMAP.md`](./docs/IMPLEMENTATION_ROADMAP.md) — what is
  implemented, in which order, and the temporary limitations.
- [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md) and
  [`docs/deployment/SYNOLOGY_NAS.md`](./docs/deployment/SYNOLOGY_NAS.md) —
  production. PartFlow runs in production on the company Synology NAS. By the
  owner's decision of 2026-11-09 it goes directly into real use once the
  production stack is installed: there is no pilot phase and no list of release
  gates, and problems are fixed in later releases.

The rest of this README is the developer guide.

## Frontend routes and structure

Routes use meaningful URLs, the browser's back and forward buttons work, and an
unknown route shows an application-level not-found state:

| URL | View |
|---|---|
| `/scan-station` | Scan Station — Station Selector (root `/` redirects here; never auto-redirects to a station) |
| `/scan-station/:stationId` | One Scan Station in standard mode (e.g. `/scan-station/LATHE-ST-01`); unknown or inactive Station IDs show an explicit error |
| `/scan-station/:stationId/production` | The same Scan Station in production mode — the top application navigation is hidden so operators stay on the station (presentation only, not a security boundary) |
| `/production-board` | Production Board (large display, read-only) |
| `/production-board/kiosk` | Production Board in kiosk mode — the top application navigation is hidden and the board renders its own wall-display header (presentation only) |
| `/management/area-board` | Management → Area Board (All Areas overview + per-Area detail) |
| `/management/machines` | Management → Machines (Machine lifecycle and maintenance — permission-based production master data) |
| `/management/tracking` | Management → PN Tracking (PN-centric list + modeless detail overlay) |
| `/management/work-orders` | Management → Work Orders |
| `/management/work-orders/completed` | Management → Work Orders → Completed Work Orders (read-only history) |
| `/management/planned-routes` | Management → Planned Routes (reusable route definitions — permission-based production master data) |
| `/management/part-numbers` | Management → Part Numbers (PartNumber master metadata and barcode labels — permission-based production master data) |
| `/management/priority` | Management → Priority (Hot WO Demand ranking) |
| `/administration` | Administration |

`/management` opens the sub view last used in the current session (Area Board on
first open) when the signed-in user may open it, and otherwise the first sub view
the user may open. A deep link is never redirected. Routing is a small
history-based router (`src/app/router-core.ts` route table and resolution,
`src/app/router-context.ts` Context and `useRouter`,
`src/app/router-provider.tsx` history state and redirects, `src/app/link.tsx`
client-side `Link`); no routing dependency is used.

Frontend structure:

- `src/styles/` — semantic design tokens (`tokens.css`; `body.dark` /
  `body.light` supply the values) and shared primitives (`global.css`).
  Component CSS consumes semantic tokens only.
- `src/app/` — shell infrastructure: the router; the theme provider (Dark by
  default; the signed-in User's preference applies on every route, otherwise the
  Scan Station's saved preference on station routes, and elsewhere the choice
  lasts for the session); the connectivity provider with fast detection (browser
  online and offline events, `/api/health` polling about every second with a
  request timeout below the probe interval, a recheck on tab focus or visibility,
  and an explicit Retry; there are no optimistic writes — a write is shown as
  recorded only after the server confirms it); the session provider; the real view
  registry (`real-views.ts`); and the development state preview (`view-state.ts`).
- `src/api/` — the shared API client layer of the real views: a thin typed fetch
  core that translates the backend's `{"detail": …}` errors into user-facing
  messages, one module per backend area (for example `scan-station.ts`,
  `production-board.ts`, `area-board.ts`, `hot-list.ts`), the ONE shared Area
  monitoring model that both the Scan Station and the Area Board render
  (`area-inventory.ts`), the snake_case ↔ camelCase mapping, the ISO 8601
  duration helpers, and the `useApiData` loading, error and reload hook. It is
  production-safe and never imports from `src/mocks/`.
- `src/mocks/` — development-only mock datasets. Every approved view is real, so
  their only reader is the development-only Scan Station preview
  (`ScanStationMockView.tsx`, opened with `?preview=mock` on a Scan Station route,
  behind `import.meta.env.DEV`). Nothing in `src/mocks` encodes production business
  rules or is written to the backend, and a production build excludes the datasets
  entirely. The real views (`src/app/real-views.ts`) ship in every build against
  the live `/api` surface.
- The production boundary is verified twice. `npm run build` scans the generated
  assets for known mock sentinel values (`scripts/check-production-boundary.mjs`),
  and `src/production-boundary.test.ts` walks the production module graph
  transitively from `src/main.tsx` and fails if a production module imports from
  `src/mocks/`. The walk stops at lazy imports guarded by `import.meta.env.DEV`:
  the mock Scan Station preview, and the development-only demo badges module of
  the Worker sign-in modal (`scan-station-dev-badges.tsx`, imported only by
  `scan-station-dev-badges-slot.tsx`, which the sign-in modal and the
  badge-confirmation gate share). An ordinary production dynamic import is still
  followed. Shared view-model types live in `src/views/view-models.ts` (types
  only, production-safe).
- `src/views/<view>/` — one folder per GUI view. `src/views/scan-station/barcode.ts`
  holds the deterministic `PF:` barcode parsing and PN normalization (PN barcodes
  carry the canonical uppercase, whitespace-free PN itself —
  `PF:PN:<part-number>`).
- `src/components/` — genuinely shared pieces (Area dot, Hot and Type chips,
  view-state blocks, dialogs, the quantity keypad, and the shared Area and Machine
  monitoring components used by both the Scan Station and the Area Board detail).

### Previewing UI states (development only)

Append `?state=…` to a view URL in a development build to force a deterministic
state (each view implements the preview states that are meaningful for it):

- `?state=loading` — skeleton loading state
- `?state=empty` — empty state
- `?state=error` — error state
- `?state=long` — long-data set (over-long PNs, many rows) on the data-heavy views
  that define a deterministic long-data fixture; a view without one (e.g.
  Administration, Priority) renders its normal sample data

Example: `http://localhost:5173/management/tracking?state=long`. The override is
gated by `import.meta.env.DEV` and does not exist in a production build. The
disconnected state is real: stop the backend (or let the health check fail) and
the shell shows the persistent OFFLINE banner with a Retry action, and write
controls are disabled.

## Prerequisites

- Docker with Docker Compose v2 (`docker compose`)
- For development outside Docker (optional): Node.js 24+, Python 3.12+, [uv](https://docs.astral.sh/uv/)

## Environment setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Adjust the values in `.env` if needed. They are development-only credentials;
   the real `.env` is ignored by git and must never contain shared or production
   secrets. `SITE_TIMEZONE` (an IANA time zone name, `UTC` by default) is the
   factory's calendar: the backend turns a completion timestamp into its done date
   in this zone for the completed history's Done range, due outcome and displayed
   date alike. Set it to the site's zone (e.g. `America/Los_Angeles`) so that "on
   time" and "late" follow the factory's day, never a browser's.

The dependency lockfiles (`backend/uv.lock`, `frontend/package-lock.json`) are
committed, and the Docker builds install strictly from them (`uv sync --frozen`,
`npm ci`). A clean checkout needs no other bootstrap step.

## Start the complete stack

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Health endpoint: <http://localhost:8000/api/health> (also proxied at <http://localhost:5173/api/health>);
  liveness only (no database): <http://localhost:8000/api/health/live>

The container health checks use the liveness route, so the stack starts healthy
before `alembic upgrade head`. `/api/health` is the readiness check and answers
503 until the database is at the migration head (see
[Database migrations (Alembic)](#database-migrations-alembic)).

The frontend serves the application shell with every approved view, all of them
reading and writing the real backend. The connectivity chip in the top navigation
shows the real backend state: CONNECTING…, ONLINE, or OFFLINE with a persistent
banner and a Retry action.

Stop the stack with `Ctrl+C`, then:

```bash
docker compose down
```

(`docker compose down -v` additionally deletes the PostgreSQL data volume — normally not needed.)

After changing backend or frontend dependencies (`pyproject.toml`/`uv.lock`,
`package.json`/`package-lock.json`), including after pulling a change that
changed them, rebuild and renew the anonymous dependency volumes so that stale
`.venv` or `node_modules` contents do not shadow the rebuilt images:

```bash
docker compose up --build -V
```

## Database migrations (Alembic)

Apply the migrations (from the no-op repository-foundation baseline up to the
current head, `0032_phase14_route_adjusted`):

```bash
docker compose exec backend uv run alembic upgrade head
```

Until `alembic upgrade head` has run, a development backend answers `/api/health`
with 503 (`schema: mismatch`) and refuses every write with 503 `not_ready`; reads
and `/api/health/live` keep working.

The upgrade to `0030_phase14_station_devices` is refused when no role is named
Operator, because Scan Stations use that role's permissions. On such a database,
rename the role whose permissions Scan Stations should use to Operator, run the
upgrade, then rename it back.

### Creating, resetting, and inspecting the development database

The `postgres` image applies `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` from `.env` **only once — when the data volume is first
initialized**. Changing `.env` later does not change the roles inside an existing
volume, so a mismatched `psql -U …` or IDE data source fails with
`FATAL: role "…" does not exist`. Always connect with the user your `.env`
actually declares, and reset the volume when the credentials changed:

```bash
# connect with the user from YOUR .env (defaults are in .env.example)
docker compose exec db psql -U <POSTGRES_USER> -d partflow

# create/update the development schema
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head

# verify the schema
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\dt"
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\d part_movements"

# full reset — DESTRUCTIVE: deletes the postgres_data volume and all
# development data (everything is recreated by `alembic upgrade head`)
docker compose down -v
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head
```

Two more caveats:

- Re-applying a migration that was edited **before it was ever
  committed or shared** requires `alembic downgrade base` +
  `alembic upgrade head` (or the full reset above). Run that only against the
  disposable development database. Many downgrades refuse, writing nothing, while
  the database holds data that their revision introduced (for example users,
  Worker Sessions or recorded route adjustments); use the full reset then. A
  migration that has been committed or shared is never edited in place — write a
  new revision instead.
- A `$` inside `POSTGRES_PASSWORD` can collide with Docker Compose variable
  interpolation in `.env`; escape it as `$$` if Compose warns about an unset
  variable.

## First-run setup and account recovery

A new database has no Administrator. While none exists, the backend writes a
one-time setup token to its log when it starts, and the **Set up PartFlow**
button in the top navigation opens the first-run dialog:

```bash
docker compose logs backend | grep "Setup token"
```

Open PartFlow, choose **Set up PartFlow**, paste the token and create the first
Administrator. The first creation closes setup for good and the token is then
discarded; a restart of the backend announces a new token only while no
Administrator exists. The token is the only protection of first-run setup, so
complete it before the service is reachable by anyone else and restrict access to
the log until then. Each backend process announces its own token.

In the production stack (`compose.production.yaml`, see
[Production stack](#production-stack)) the same steps use the production command
form. Start the backend with one worker so that there is a single token, then
restore the configured worker count once setup is complete:

```bash
PF="docker compose -f compose.production.yaml --env-file .env.production"
PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend
$PF logs backend | grep "Setup token"
$PF up -d backend
```

The recovery commands below run the same way in production, for example
`$PF run --rm --no-deps backend python -m app.cli reset-password --login-name <name>`
(there is no `uv run` in the production image).

### Resetting a forgotten password

If the only Administrator forgets the password, reset it from the host:

```bash
docker compose exec backend uv run python -m app.cli reset-password --login-name <name>
```

The new password is typed at the prompt, never on the command line. The command
works only for a user that already has a password and only while an Administrator
exists; it never creates an Administrator. It sets a temporary password, ends the
user's sign-ins and clears a lock. If the database connection fails while the
reset is being saved, the command says the outcome is unknown (exit code 2); run
it again, because it sets the password again either way.

### Restoring the permission to manage correction permissions

Nobody can grant the permission to manage correction permissions
(`MANAGE_CORRECTION_PERMISSIONS`) once no active user with a password holds it.
The backend then logs a warning at startup. To see who holds the two
permission-management keys, count the active users with a password whose role
holds each key in the database shell
(`docker compose exec db psql -U <POSTGRES_USER> -d partflow`):

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

A missing row means no holder. Both counts should be at least 1, or
`MANAGE_USERS_AND_ROLES` should be absent (first-run setup is then open). If
`MANAGE_CORRECTION_PERMISSIONS` has no holder while `MANAGE_USERS_AND_ROLES` has,
restore it from the host:

```bash
docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>
```

The command works only while no active user with a password may manage correction
permissions, and the named role must have such a member. It grants the permission
to that role, appends an audit row and changes nothing else. Each refusal (a
holder already exists, no such role, no active user with a password in the role)
writes nothing and exits 1.

### Secure session cookie

The backend reads `SESSION_COOKIE_SECURE` (default `false`, because the
development stack runs over plain HTTP). Set it to `true` in the backend
environment when PartFlow is served over HTTPS, so that the session cookie is sent
only on secure connections; the development `compose.yaml` does not pass it, and
the production stack fixes it to `true`.

## Enrolling Scan Station devices

A Scan Station works only from a browser that an administrator has enrolled for
that station; development stations are enrolled the same way.

1. Sign in with a user who may manage Scan Stations.
2. Open Administration → Scan Stations → `Devices…` for the station and choose
   `Enroll device…`.
3. Enter the one-time code (`XXXXX-XXXXX`, valid 15 minutes, single use, shown
   once) on the station within that time. The station browser exchanges it for a
   device token, keeps the token in its browser storage and sends it in
   `X-PartFlow-Station-Device` with every request.

A browser whose storage is cleared must be enrolled again. `Re-enroll…` replaces
a device when its new code is used, and `Revoke…` stops a lost device at once.
While the role applied at Scan Stations holds a correction permission (initially
Undo recent eligible scans), enrolling also needs the permission to manage
correction permissions. What an enrolled station may do follows the permissions
of the role marked **Applied at Scan Stations** in Roles & permissions (initially
Operator). Device tokens and enrollment codes are bearer credentials: serve
PartFlow over HTTPS and keep the header out of reverse-proxy logs
([deployment guide](./docs/DEPLOYMENT.md)).

## Reconciliation (read-only)

`python -m app.cli reconcile` checks that the stored production state agrees with
Movement history, allocations and the canonical identity rules. It runs in one
read-only database snapshot, prints one JSON report on standard output and never
changes data or repairs a finding.

```bash
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

Options: `--check ID` (repeatable, `a` to `j`), `--statement-timeout SECONDS`
(1-3600, default 300) and `--max-findings N` (1-10000, default 100). Exit codes:
`0` clean, `1` mismatch, `2` could not run. The exit status counts only when the
report is one complete JSON document whose `exit_code` equals it; an empty or
unparseable report means it could not run. Check (g) reports `not_applicable`
until Movement-history archival exists, and check (h) reports `not_applicable`
on a database without the PartFlow database roles, which includes the
development stack. Do not run a migration while it runs. The checks, the
production form and the operating rules are in
[`docs/deployment/OPERATIONS_RUNBOOK.md`](./docs/deployment/OPERATIONS_RUNBOOK.md)
§7.

## IntelliJ IDEA / PyCharm setup (database tools and SQL inspections)

Optional, but recommended when working in a JetBrains IDE (verified with IntelliJ
IDEA 2026.2.1). Without these settings the IDE reports misleading SQL "errors" in
the Alembic migrations and database tests and may block commits on them.

1. **Data source.** Database tool window → `+` → Data Source → PostgreSQL: host
   `localhost`, port `5432`, database `partflow`, user and password **from your
   `.env`** (see the credential caveat above). In the schema selection,
   introspect `public` **and `pg_catalog`** — the schema tests query `pg_proc` to
   verify the append-only trigger function, and `pg_catalog` is not introspected
   by default.
2. **SQL dialect.** Settings → Languages & Frameworks → SQL Dialects → set the
   Project SQL Dialect to **PostgreSQL**, so that SQL embedded in Python strings
   is parsed with the right syntax.
3. **SQL resolution scope.** Settings → Languages & Frameworks → SQL Resolution
   Scopes → map the project (or the `backend` directory) to your data source's
   `partflow.public` schema, so that table and function names in embedded SQL
   resolve against the real development database.
4. **Refresh after migrating.** The IDE resolves names against its last
   introspection snapshot — refresh the data source (Ctrl+F5) after every
   `alembic upgrade` or reset, or new tables stay "unresolved".

Expected residual warnings that are safe to ignore:

- In a migration's `CREATE TRIGGER` string, the IDE may still report the trigger
  function or `part_movements` as unresolved: each embedded SQL fragment is
  analyzed independently, and those objects are created by the same migration
  (partly through Python `op.create_table`, which the SQL resolver cannot see).
- pytest test classes legitimately have no `__init__`; the "Class has no
  `__init__` method" inspection is noise for this codebase.

These are IDE code-analysis findings, not project quality gates — the canonical
gates are the Docker commands under "Quality commands". If the commit dialog's
"Analyze code" check blocks a commit on them, use "Commit Anyway" or disable that
check (Settings → Version Control → Commit).

## Quality commands

The Linux containers are the canonical environment for all quality gates. Run
these with the Compose stack up (`docker compose up -d`).

### Backend

```bash
docker compose exec backend uv run ruff format --check .   # formatting check (ruff format . to fix)
docker compose exec backend uv run ruff check .            # lint
docker compose exec backend uv run mypy app tests          # type check (strict)
docker compose exec backend uv run pytest                  # tests (see below)
docker compose exec backend uv run alembic upgrade head    # migrations
```

The pytest suite holds three kinds of tests:

- **Behavior tests** — `tests/test_health.py` tests readiness and liveness with a
  mocked database revision read (ready, schema mismatch, accepted override,
  unreachable database; safe 503 responses), plus one test against the real
  database.
- **Unit tests** that need no database, for example
  `tests/test_part_number_normalization.py` (the canonical Part Number
  normalization), `tests/test_password_hashing.py` and
  `tests/test_password_policy.py` (scrypt password hashing and the password
  policy), `tests/test_station_device_domain.py` (the enrollment code and device
  name rules), `tests/test_worker_badge_normalization.py` (the canonical Worker
  badge rule: trim, uppercase, `PF:` and length refusals) and
  `tests/test_route_access.py`. The last one checks the route registry in
  `app/api/route_access.py` and fails on any route the registry does not
  classify, so a new route must be added there.
- **Integration tests** — most other modules. They need the PostgreSQL service
  reachable through `DATABASE_URL` and run against dedicated temporary databases
  (`partflow_test_*`; the reconciliation tests use template-cloned databases).
  `tests/test_database_connectivity.py` calls `GET /api/health` through the real
  application wiring with no mocking. The schema modules
  (`tests/test_phase<N>_schema.py`) run the real Alembic migrations (upgrade →
  downgrade → upgrade), each stopping at its own boundary revision, while
  `tests/test_phase13_schema.py` and `tests/test_phase14_schema.py` cover the
  revisions from `0014_phase13_workers` to the single head
  `0032_phase14_route_adjusted`: the constraints, the widened audit vocabulary,
  the refusing downgrades and the parity between the models and the migrations.
  `tests/test_release_gate_api.py`, `tests/test_migrate_cli.py` and
  `tests/test_revision_cli.py` cover the write gate (409 `release_mismatch`, 503
  `not_ready`), the `migrate` job (one transaction, refusals, locks) and the
  `revision` report. The API test modules exercise the endpoints end to end.

Tests call signed-in or permission routes through `client_as` / `admin_of` from
`tests/auth_harness.py`, not through an anonymous client. Calls to Scan Station
routes go through `station_device_client` (an enrolled device for the addressed
station), and a new station route must declare `RequireStationDevice` and be
classified in `route_access.py`.

The configured database role must be a superuser (the Compose and CI
`partflow_user` is), because the suite creates databases and temporary database
roles (`partflow_app_t…`, `partflow_maint_t…`), and some cases set
`session_replication_role`.

CI runs the suite with `PARTFLOW_TEST_DATABASE_ROLE=app`. Every connection to a
test database then runs under a temporary application database role, except
Alembic, `owner_engine` and `database_owner` modules. Run it locally the same way
before committing a change that touches database writes, grants or test setup;
the local default stays the owner role:

```bash
docker compose exec -e PARTFLOW_TEST_DATABASE_ROLE=app backend uv run pytest
```

### Frontend

```bash
docker compose exec frontend npm run format        # format with Prettier
docker compose exec frontend npm run format:check  # formatting check
docker compose exec frontend npm run lint          # ESLint
docker compose exec frontend npm run typecheck     # TypeScript (strict, no emit)
docker compose exec frontend npm run test          # Vitest + React Testing Library
docker compose exec frontend npm run build         # type check + production build + mock-boundary check
```

`npm run check` runs the format check, lint, type check, tests and build in one
command.

### Running directly on the host (optional, best effort)

The same commands can be run without the `docker compose exec …` prefix from
`backend/` (with uv) or `frontend/` (with Node 24), but the host is not the
canonical environment: toolchain versions and operating-system behavior may
differ from the Linux containers used by Docker and CI. Backend notes for host
runs:

- `DATABASE_URL` must be resolvable. `backend/tests/conftest.py` defaults it to
  the development database from `.env.example`; anything outside pytest (e.g.
  `uv run alembic upgrade head`) needs the variable set explicitly or a
  `backend/.env` file.
- The pytest integration tests need the Compose `db` service running (its port
  5432 is published to the host): the connectivity test performs a real database
  check through `GET /api/health`, and the tests create and drop temporary
  `partflow_test_*` databases with the configured role. If your `.env` uses custom
  credentials, set `DATABASE_URL` to match before running pytest on the host.

When host results disagree with container results, the container results win.

## Docker development notes

When using the Docker development environment, treat the Linux containers as the
canonical development environment.

### Frontend formatting

Run Prettier **inside the frontend container** before checking formatting:

```bash
docker compose exec frontend npm run format
```

Then verify:

```bash
docker compose exec frontend npm run format:check
```

Running Prettier directly on the host machine may produce different results from
the Linux container used by Docker and GitHub Actions.

### Complete frontend quality gate

```bash
docker compose exec frontend sh -lc "npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build"
```

### Complete backend quality gate

```bash
docker compose exec backend sh -lc "uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest"
```

## Production stack

Production runs a separate Compose stack, never `compose.yaml`. This section only
orients a developer; the contract of the stack (services, configuration file,
secret files, first installation, database roles, releases, backups, logging and
monitoring) is [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md) §3.1, and the
installation on the company Synology NAS is
[`docs/deployment/SYNOLOGY_NAS.md`](./docs/deployment/SYNOLOGY_NAS.md) §8. The
day-to-day procedures are in
[`docs/deployment/OPERATIONS_RUNBOOK.md`](./docs/deployment/OPERATIONS_RUNBOOK.md).
The production commands and scripts have been run on Docker Desktop and in a Linux
container, not yet on a Synology NAS or a VPS.

- **Files.** `compose.production.yaml` (Compose project `partflow-production`)
  defines `db`, `backend`, `web` (nginx, published on `127.0.0.1` only) and the
  one-shot `migrate`, `db-roles`, `backup-tools` and `status` jobs. Its
  configuration is `.env.production`, copied from `.env.production.example` and
  ignored by git, plus three secret files and an existing backup directory. Only
  the build uses `compose.production.build.yaml`: `compose.production.yaml` has no
  build section, so `up` or `run` with a release whose images are missing fails
  instead of building the checkout. The commands use the shorthand
  `PF="docker compose -f compose.production.yaml --env-file .env.production"`.
  Never run `$PF down -v`: it deletes the production database volume.
- **Images.** Both Dockerfiles end with the `development` stage that
  `compose.yaml` builds, and also contain a `production` stage that Compose does
  not build by default (`compose.production.build.yaml` selects it):

  ```bash
  docker build --target production backend
  docker build --target production frontend
  ```

  The backend `production` image runs as a non-root user, without a reload server
  and without migrations on start. The frontend `production` image is the pinned
  official nginx (`web`) serving the immutable build and proxying `/api`. Each
  production image carries its release (`PARTFLOW_RELEASE`, `PARTFLOW_COMMIT`),
  and both refuse a missing or malformed value.
- **Database connection.** For the production images the backend can read its
  database connection from `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` and
  `DATABASE_PASSWORD_FILE` (plus optional `DATABASE_PORT`) instead of
  `DATABASE_URL`; setting both forms is refused. The production backend connects
  as the least-privilege database role `partflow_app`, never as the owner.
- **Scripts.** `deploy/production/` holds `release.sh` (the release sequence),
  `smoke.sh` (loopback checks of a running release), `backup.sh` and
  `restore-test.sh` (verified backups and the isolated restore drill), and
  `check.sh` and `scheduled-reconcile.sh` (host checks and the daily
  reconciliation). Each prints its usage with `--help`.

### Production artifact checks

The production artifact checks need no running stack for their static part:

```bash
python3 -B -m unittest discover -s deploy/production/tests -p 'test*.py'
PARTFLOW_RELEASE=s2-check PARTFLOW_COMMIT=$(git rev-parse HEAD) PARTFLOW_SECRETS_DIR=<dir holding postgres_password> PARTFLOW_BACKUP_DIR=<any directory> PARTFLOW_SITE_TIMEZONE=UTC \
  docker compose -f compose.production.yaml -f compose.production.build.yaml --env-file .env.production.example build
```

The first command runs the static tests of the Compose model, the environment
example, the Dockerfiles and the nginx configuration, and the tests of the
scripts with fake `docker`, `git` and `curl` commands (`test_release_scripts.py`,
`test_backup_scripts.py`, `test_reconcile_regression.py`,
`test_check_script.py`, `test_scheduled_reconcile.py` and
`test_monitor_report.py`). The second builds both production images; the `web`
build runs the production-boundary check, and a build without `PARTFLOW_COMMIT`
fails.

Four longer checks start throwaway Compose projects on loopback ports, need a
Docker daemon and are not part of CI. Each writes an evidence file:

```bash
python3 deploy/production/tests/stack_smoke.py --evidence <path.json>             # stack smoke through web (project partflow-s2-smoke)
python3 deploy/production/tests/release_rehearsal.py --evidence <path.json>       # a release rehearsal (project partflow-s3-rehearsal)
python3 deploy/production/tests/backup_rehearsal.py --evidence <path.json>        # backups, restore drill and rollback path 3 (projects partflow-s5-*)
python3 deploy/production/tests/observability_rehearsal.py --evidence <path.json> # structured logs, status, check.sh and scheduled-reconcile.sh
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and on
every pull request:

- **backend** — against a PostgreSQL 16 service: `uv sync --frozen`, the format
  check, lint, mypy, `alembic upgrade head` and pytest, with
  `PARTFLOW_TEST_DATABASE_ROLE=app`;
- **frontend** — with Node.js 24: `npm ci`, the format check, lint, type check,
  tests and the production build;
- **docker** — verifies that the development images build
  (`docker compose build`), builds the production images
  (`compose.production.build.yaml`, with throwaway release, secret, backup
  directory and time-zone values) and runs the production artifact static tests
  (`deploy/production/tests`).

## Repository layout

```text
frontend/          Vite + React + TypeScript app (shell + real views for every approved GUI view)
  src/styles/      semantic design tokens and shared primitives
  src/app/         router, theme, connectivity, session, real view registry, dev state preview
  src/api/         typed API client layer of the real views (production-safe)
  src/mocks/       development-only mock datasets (excluded from production builds)
  src/views/       one folder per approved GUI view
  src/components/  shared presentation components
  scripts/         production-boundary check run by npm run build
  nginx/           `web` image configuration (nginx templates, proxy and header snippets, trusted-proxy entrypoint)
  Dockerfile       `production` and `development` (default) stages
backend/
  app/api/         HTTP routes and the route access registry (route_access.py)
  app/application/ application services (every business rule and transaction)
  app/core/        configuration (pydantic-settings) and logging
  app/domain/      framework-independent domain vocabulary (PN normalization, enums)
  app/infrastructure/  database engine, connectivity check, and canonical schema mappings
  app/cli.py       operator commands (reset-password, reconcile, migrate, status, …)
  tests/           pytest suite
  alembic/         migration environment and revisions (baseline up to the current head)
  Dockerfile       `production` and `development` (default) stages
compose.yaml       development stack (db, backend, frontend)
compose.production.yaml  production stack (db, backend, web, migrate, db-roles, backup-tools, status); not for development
compose.production.build.yaml  build-only companion: the production image builds of backend and web
.env.example       development configuration (copy to .env)
.env.production.example  production configuration inventory (copy to .env.production)
deploy/production/release.sh, smoke.sh  release flow and loopback smoke checks (reconcile_regression.py compares reconcile reports)
deploy/production/backup.sh, restore-test.sh  backup artifact and isolated restore drill
deploy/production/check.sh, scheduled-reconcile.sh, monitor_report.py  host checks, daily reconcile and their report helper
deploy/production/tests/  production artifact static tests, script tests, Compose stack smoke and rehearsals
docs/              canonical project documentation
```

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Rewritten as a newcomer introduction plus the developer guide; the phase-by-phase status narrative was removed, the production section shortened to an orientation, and stale commands and statements were corrected. |
| 2026-10-09 | `03bd90c` | Production monitoring: structured logs, request identifiers, the `status` command, host checks and the scheduled reconciliation. |
| 2026-10-08 | `a330383` | Production backups, the verified pre-release backup, the restore drill and rollback path 3. |
| 2026-10-08 | `1116b07` | Separate production database roles and their grants. |
| 2026-10-08 | `b820e7a` | Release flow, release identity, readiness and the write gate. |
| 2026-10-08 | `d13e303` to `25f63bf` | Production images, the production Compose stack, its configuration inventory and its static tests. |
| 2026-10-07 | `e1dee40`, `918f025` | Work Order import from CSV and Excel files, including changes to active Work Orders. |
| 2026-10-07 | `3fb66df` | Read-only reconciliation command. |
| 2026-10-06 to 2026-10-07 | `702ab1c` to `582baf0` | Sign-in, permission enforcement, enrolled Scan Station devices, Management allocation corrections, route adjustments, the PN audit trail and the user theme. |
| 2026-10-04 to 2026-10-06 | `c12728d` to `69b8ec1` | Workers, Worker sessions and badge confirmation, Part Numbers and Planned Routes management, display settings, the retention period, users and roles. |
| 2026-10-04 | `80f7925` to `9f466fa` | Priority management (the Hot list). |
| 2026-09-02 to 2026-09-13 | `5f006a0` to `f016d41` | Production Board, Area Board and PN Tracking. |
| 2026-09-03 to 2026-09-04 | `40fbcb5` to `f10d8bd` | `Receive Quantity` at the Scan Station. |
| 2026-09-02 | `d2f68de` | Vietnamese mirror and deployment guides added. |
| 2026-09-01 to 2026-09-02 | `100d59d` to `6fdf20e` | Stockroom, allocation and the completed Work Order history. |
| 2026-08-25 to 2026-09-01 | `a567b02` to `7e60dcb` | Scan Station transfer, Machine processing, direct processing, split and merge, Undo and corrections. |
| 2026-08-18 to 2026-08-24 | `bb5b37e` to `1fe73e9` | Environment setup and Machines, Work Order intake and release, and the database and IDE setup instructions. |
| 2026-07-23 to 2026-08-17 | `676e750` to `8c5ac60` | Repository foundation, development workflow, frontend design system and application shell, and the domain schema. |
