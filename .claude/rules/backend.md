---
paths:
  - "backend/**/*"
---

# Backend and Database Rules

Apply these rules when working in `backend/`.

## Boundaries and Writes

- Keep FastAPI handlers thin: parse/validate transport data, invoke Application use cases, and translate expected errors. Handlers NEVER own production workflow rules.
- Application owns workflow orchestration and transaction boundaries. Domain/Application own business rules; Infrastructure owns persistence/integration.
- Validate before every production-data write.
- Use one transaction when a command must atomically preserve movement, quantity, status, allocation, Machine lifecycle, or audit consistency.
- Make retryable commands idempotent where practical; duplicate transport retries must not create duplicate Movement/events.
- Prefer PostgreSQL constraints/triggers for critical referential, uniqueness, canonical-form, quantity, and immutability guarantees when they can enforce the invariant reliably.
- Preserve immutable production history. NEVER overwrite Movement history as a correction shortcut.
- Keep ERP response formats and availability outside Domain/Application contracts.

## Errors, Logging, and Data Integrity

- Return actionable user-facing errors without exposing raw internal failures.
- Preserve original exceptions when adding operational context.
- NEVER silently swallow exceptions or use broad catches without an intentional recovery path.
- Logs should identify relevant PN, QuantityFlow/quantity, WorkOrderDemand, Area, Operation, Machine, Worker, Scan Station, action, and failure reason without becoming noisy or exposing secrets.
- Tests and tooling must never modify production or user databases.

## Schema and Migrations

- Keep SQLAlchemy mappings, Alembic migrations, and database constraints consistent.
- Give persistent constraints/indexes deterministic names consistent with existing migrations.
- NEVER edit a migration that has already been committed/shared; create a new revision. Editing an unpublished disposable migration is acceptable only when that is clearly the repository's current workflow.
- Migration reset/downgrade operations are destructive; run them only against disposable development/test databases and only when the task requires them.
- Preserve the canonical PN identity model: production tables keep their own canonical PN value and do not acquire a surrogate `part_number_id` dependency.

## Python Quality

- Python target: 3.12+.
- Keep Ruff formatting/lint clean and MyPy strict compatibility.
- Add/update focused tests for changed business logic and regression tests for defects whenever practical.

Canonical Docker quality commands:

```bash
docker compose exec backend uv run ruff format --check .
docker compose exec backend uv run ruff check .
docker compose exec backend uv run mypy app tests
docker compose exec backend uv run pytest
docker compose exec backend uv run alembic upgrade head
```

Run the narrowest relevant subset first; broader database/integration gates require the configured PostgreSQL test environment described in `README.md`.
NEVER claim any gate passed unless it actually completed successfully.
