"""Phase 16: the purge of archived Movement history (design B, step 2).

The second build step of the approved Movement-history archival design
(``_claude_outputs/phase16/ARCHIVAL_DESIGN.md`` §13; IMPLEMENTATION_ROADMAP
Phase 16): after the export, the verification, the off-host copy and a
``pre-purge`` backup, the host job deletes the archived Movements of whole
flow components; the carry-forward records keep every figure.

- The Movement guard (design §8.2): ``trg_part_movements_forbid_mutation``
  now refuses ``UPDATE`` and ``TRUNCATE`` only (statement-level, its
  function unchanged), and the new row-level ``BEFORE DELETE`` trigger
  ``trg_part_movements_guard_delete`` raises for every row (it never skips
  one silently) unless ``partflow.archive_run_id`` names a run in
  ``PURGING``, the row's flow has an unpurged, unskipped record of that
  run, the row's id is not above the record's ``last_movement_id``, and the
  row's ``occurred_at`` and ``server_received_at`` are both before the
  run's cutoff. One primary-key join per row, so a batch of 50,000 rows
  stays fast. The application role still holds no DELETE on
  ``part_movements``; the maintenance role gains it (a grant, derived by
  ``apply-grants`` in every ``migrate``).
- ``history_archive_runs``: the states ``PURGING`` (open: the unique open
  index is rebuilt with it) and ``COMPLETED_WITH_SKIPS`` (final), the
  results ``PURGED`` and ``NOTHING_PURGED`` (``EXPORT_ONLY`` stays on the
  runs step 1 finished; no run gets it any more), and the columns
  ``pre_purge_backup_name``, ``pre_purge_backup_started_at``,
  ``purge_started_at``, ``purged_components``, ``purged_flows``,
  ``purged_movements`` and ``receipt_sha256``, with their CHECKs (a run
  purges only with its verified, copied archive and a ``pre-purge`` backup
  named after its archive whose dump started after the verification; a
  PURGED run deleted something; every PURGED or NOTHING_PURGED run has its
  receipt). The run guard function gains the job's new transitions:
  ``RUNNING`` keeps the archive copied (``WAITING_FOR_COPY`` → ``RUNNING``
  after a confirmation), ``RUNNING`` → ``PURGING`` (only with the backup
  evidence, recorded once), ``RUNNING`` → ``COMPLETED_WITH_SKIPS`` (every
  component skipped), ``PURGING`` → ``COMPLETED``,
  ``COMPLETED_WITH_SKIPS`` or ``FAILED``; ``RUNNING`` and
  ``WAITING_FOR_COPY`` no longer go to ``COMPLETED``; the copy time is set
  once; a ``PURGING`` run changes only its purged and skipped counts, and
  only upwards. The application role still only requests, cancels
  (``APPROVED`` or ``WAITING_FOR_COPY``) and confirms a copy.
- ``history_archived_flows``: the record guard lets the job mark a record
  skipped while its run is ``RUNNING`` or ``PURGING`` (the purge's re-check
  under the locks); a purged record still needs ``PURGING``. Two partial
  indexes serve the readers that add the purged records: by Part Number
  and by the released demand line.
- ``application_policy``: ``archive_purge_batch_movements`` (default
  50,000, 100-1,000,000) and ``archive_purge_batch_part_numbers`` (default
  20, 1-1000) of History archival & purge, and ``backup_keep_pre_purge``
  (default 3, 1-100) of Backups, each with its range CHECK.

The downgrade REFUSES while a run is ``PURGING`` or ``COMPLETED_WITH_SKIPS``
or has a purge result, a record is purged, a purge column is set or a new
setting differs from its default, so no purge evidence is silently
dropped; otherwise it restores 0040 exactly. ``alembic/env.py`` runs in one
transaction. Run it only against disposable development and test
databases.

Revision ID: 0041_phase16_history_purge
Revises: 0040_phase16_history_archive
Create Date: 2026-11-18

"""

from collections.abc import Iterable, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041_phase16_history_purge"
down_revision: str | None = "0040_phase16_history_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py or enums
# counterpart, and each _PREVIOUS_ value equals migration 0040's.
_RUNS = "history_archive_runs"
_FLOWS = "history_archived_flows"
_POLICY = "application_policy"
_MOVEMENTS = "part_movements"

_PREVIOUS_STATES = (
    "APPROVED",
    "RUNNING",
    "WAITING_FOR_COPY",
    "COMPLETED",
    "NOTHING_ELIGIBLE",
    "CANCELLED",
    "FAILED",
)
_STATES = (
    "APPROVED",
    "RUNNING",
    "WAITING_FOR_COPY",
    "PURGING",
    "COMPLETED",
    "COMPLETED_WITH_SKIPS",
    "NOTHING_ELIGIBLE",
    "CANCELLED",
    "FAILED",
)
_PREVIOUS_RESULTS = ("EXPORT_ONLY",)
_RESULTS = ("EXPORT_ONLY", "PURGED", "NOTHING_PURGED")
_PREVIOUS_FINAL_STATES = ("COMPLETED", "NOTHING_ELIGIBLE", "CANCELLED", "FAILED")
_FINAL_STATES = ("COMPLETED", "COMPLETED_WITH_SKIPS", "NOTHING_ELIGIBLE", "CANCELLED", "FAILED")
_PREVIOUS_OPEN_STATES = ("APPROVED", "RUNNING", "WAITING_FOR_COPY")
_OPEN_STATES = ("APPROVED", "RUNNING", "WAITING_FOR_COPY", "PURGING")
_BACKUP_NAME_PATTERN = "^[0-9]{8}T[0-9]{6}Z-pre-purge-run-[0-9]+-[0-9]{8}T[0-9]{6}Z$"


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_OPEN_INDEX = "uq_history_archive_runs_open"

# The run CHECKs this revision replaces: (suffix, 0040 SQL, this revision's SQL).
_CHANGED_RUN_CHECKS = (
    ("state", _in("state", _PREVIOUS_STATES), _in("state", _STATES)),
    (
        "progress_shape",
        "(state <> 'APPROVED' OR started_at IS NULL)"
        " AND (state NOT IN ('RUNNING', 'WAITING_FOR_COPY', 'COMPLETED', 'NOTHING_ELIGIBLE')"
        " OR started_at IS NOT NULL)"
        " AND (state NOT IN ('WAITING_FOR_COPY', 'COMPLETED') OR verified_at IS NOT NULL)",
        "(state <> 'APPROVED' OR started_at IS NULL)"
        " AND (state NOT IN ('RUNNING', 'WAITING_FOR_COPY', 'PURGING', 'COMPLETED',"
        " 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE') OR started_at IS NOT NULL)"
        " AND (state NOT IN ('WAITING_FOR_COPY', 'PURGING', 'COMPLETED', 'COMPLETED_WITH_SKIPS')"
        " OR verified_at IS NOT NULL)",
    ),
    (
        "completed_copy",
        "state <> 'COMPLETED' OR copied_at IS NOT NULL OR copy_confirmed_at IS NOT NULL",
        "copied_at IS NOT NULL OR copy_confirmed_at IS NOT NULL"
        " OR (state <> 'PURGING' AND result IS NULL) OR result = 'NOTHING_PURGED'",
    ),
    (
        "result_shape",
        "(result IS NULL OR "
        + _in("result", _PREVIOUS_RESULTS)
        + ") AND (state = 'COMPLETED') = (result IS NOT NULL)",
        "(result IS NULL OR "
        + _in("result", _RESULTS)
        + ") AND (state IN ('COMPLETED', 'COMPLETED_WITH_SKIPS')) = (result IS NOT NULL)"
        " AND (result IS DISTINCT FROM 'EXPORT_ONLY' OR state = 'COMPLETED')",
    ),
    (
        "finish_shape",
        "(" + _in("state", _PREVIOUS_FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)",
        "(" + _in("state", _FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)",
    ),
)
# The run CHECKs this revision adds: (suffix, SQL).
_NEW_RUN_CHECKS = (
    (
        "skips_shape",
        "(state <> 'COMPLETED' OR result = 'EXPORT_ONLY' OR skipped_components = 0)"
        " AND (state <> 'COMPLETED_WITH_SKIPS' OR skipped_components > 0)",
    ),
    (
        "backup_shape",
        "(pre_purge_backup_name IS NULL) = (pre_purge_backup_started_at IS NULL)"
        " AND (pre_purge_backup_name IS NULL OR (pre_purge_backup_name ~ '"
        + _BACKUP_NAME_PATTERN
        + "' AND substr(pre_purge_backup_name, 28) = archive_name))"
        " AND (pre_purge_backup_started_at IS NULL OR pre_purge_backup_started_at > verified_at)",
    ),
    (
        "purge_shape",
        "(purge_started_at IS NULL) = (pre_purge_backup_name IS NULL)"
        " AND (purge_started_at IS NULL) = (purged_components IS NULL)"
        " AND (purge_started_at IS NULL) = (purged_flows IS NULL)"
        " AND (purge_started_at IS NULL) = (purged_movements IS NULL)"
        " AND (purged_components IS NULL OR (purged_components >= 0"
        " AND purged_flows >= purged_components AND purged_movements >= purged_flows))"
        " AND (purge_started_at IS NULL OR purge_started_at >= pre_purge_backup_started_at)"
        " AND (state <> 'PURGING' OR purge_started_at IS NOT NULL)"
        " AND (result IS DISTINCT FROM 'PURGED' OR purged_components > 0)"
        " AND (result IS DISTINCT FROM 'NOTHING_PURGED' OR coalesce(purged_components, 0) = 0)",
    ),
    (
        "receipt_shape",
        "(receipt_sha256 IS NULL OR receipt_sha256 ~ '^[0-9a-f]{64}$')"
        " AND (receipt_sha256 IS NULL OR coalesce(result, '') IN ('PURGED', 'NOTHING_PURGED')"
        " OR (state = 'FAILED' AND purge_started_at IS NOT NULL))"
        " AND (coalesce(result, '') NOT IN ('PURGED', 'NOTHING_PURGED')"
        " OR receipt_sha256 IS NOT NULL)",
    ),
)
# (column, type) appended to history_archive_runs, in the model's order.
_RUN_COLUMNS = (
    ("pre_purge_backup_name", sa.Text()),
    ("pre_purge_backup_started_at", sa.DateTime(timezone=True)),
    ("purge_started_at", sa.DateTime(timezone=True)),
    ("purged_components", sa.Integer()),
    ("purged_flows", sa.Integer()),
    ("purged_movements", sa.BigInteger()),
    ("receipt_sha256", sa.Text()),
)
# (column, server default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_POLICY_COLUMNS = (
    ("archive_purge_batch_movements", 50000, 100, 1000000),
    ("archive_purge_batch_part_numbers", 20, 1, 1000),
    ("backup_keep_pre_purge", 3, 1, 100),
)
# (name, columns, partial WHERE) of the record indexes the readers use.
_RECORD_INDEXES = (
    (
        "ix_history_archived_flows_part_number_purged",
        ("part_number", "last_occurred_at", "last_movement_id"),
        "purged_at IS NOT NULL",
    ),
    (
        "ix_history_archived_flows_received_demand_id_purged",
        ("received_demand_id",),
        "purged_at IS NOT NULL AND received_demand_id IS NOT NULL",
    ),
)

# The guard functions and triggers (pinned by app.infrastructure.database_privileges:
# GUARD_TRIGGERS and the SHA-256 of each function's source).
_RUNS_FUNCTION = "partflow_history_archive_runs_guard_mutation"
_FLOWS_FUNCTION = "partflow_history_archived_flows_guard_mutation"
_MOVEMENTS_FUNCTION = "partflow_part_movements_forbid_mutation"
_DELETE_FUNCTION = "partflow_part_movements_guard_delete"
_MOVEMENTS_TRIGGER = "trg_part_movements_forbid_mutation"
_DELETE_TRIGGER = "trg_part_movements_guard_delete"
# The constraint name the cutoff refusal reports (SQLSTATE 23514), unchanged.
_CUTOFF_FLOOR = "trg_history_archive_runs_guard_mutation"

_RUNS_BODY = f"""
DECLARE
    shortest integer;
    newest_cutoff timestamptz;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archive_runs rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT p.shortest_retention_months INTO shortest
        FROM public.application_policy p WHERE p.id = 1;
        -- Four days' allowance: the exact rule counts the months back from the site's
        -- date, which can be a day ahead of UTC at the end of a month.
        newest_cutoff := ((now() AT TIME ZONE 'UTC') - make_interval(months => shortest)
            + interval '4 days') AT TIME ZONE 'UTC';
        IF shortest IS NULL OR NEW.cutoff > newest_cutoff THEN
            RAISE EXCEPTION
                'archive run cutoff % is newer than the shortest allowed period (% months)',
                NEW.cutoff, shortest
                USING ERRCODE = 'check_violation', CONSTRAINT = '{_CUTOFF_FLOOR}';
        END IF;
        IF NEW.state <> 'APPROVED' OR NEW.started_at IS NOT NULL OR NEW.exported_at IS NOT NULL
            OR NEW.verified_at IS NOT NULL OR NEW.copied_at IS NOT NULL
            OR NEW.copy_confirmed_at IS NOT NULL OR NEW.finished_at IS NOT NULL
            OR NEW.pre_purge_backup_name IS NOT NULL OR NEW.purge_started_at IS NOT NULL
            OR NEW.receipt_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'a history archive run is requested APPROVED, with no progress';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('COMPLETED', 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE', 'CANCELLED',
        'FAILED') THEN
        RAISE EXCEPTION 'history archive run % is finished (%) and never changes',
            OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.cutoff, NEW.reason, NEW.requested_by_user_id, NEW.requested_at,
        NEW.preview_components, NEW.preview_flows, NEW.preview_movements,
        NEW.preview_first_occurred_at, NEW.preview_last_occurred_at, NEW.preview_archive_bytes)
        IS DISTINCT FROM
        (OLD.id, OLD.cutoff, OLD.reason, OLD.requested_by_user_id, OLD.requested_at,
        OLD.preview_components, OLD.preview_flows, OLD.preview_movements,
        OLD.preview_first_occurred_at, OLD.preview_last_occurred_at, OLD.preview_archive_bytes)
    THEN
        RAISE EXCEPTION 'the request of history archive run % never changes', OLD.id;
    END IF;
    -- The maintenance job: a role that may write archive records, working on this run.
    -- Only a person confirms the off-host copy or cancels a run.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND current_setting('partflow.archive_run_id', true) = OLD.id::text THEN
        IF (NEW.copy_confirmed_at, NEW.copy_confirmed_by_user_id, NEW.cancelled_by_user_id)
            IS DISTINCT FROM
            (OLD.copy_confirmed_at, OLD.copy_confirmed_by_user_id, OLD.cancelled_by_user_id)
        THEN
            RAISE EXCEPTION
                'history archive run %: the maintenance job never confirms a copy or cancels',
                OLD.id;
        END IF;
        IF OLD.copied_at IS NOT NULL AND NEW.copied_at IS DISTINCT FROM OLD.copied_at THEN
            RAISE EXCEPTION 'history archive run %: the off-host copy is recorded once', OLD.id;
        END IF;
        -- The pre-purge backup is recorded once, when the purge starts.
        IF (NEW.pre_purge_backup_name, NEW.pre_purge_backup_started_at, NEW.purge_started_at)
            IS DISTINCT FROM
            (OLD.pre_purge_backup_name, OLD.pre_purge_backup_started_at, OLD.purge_started_at)
            AND NOT (OLD.state = 'RUNNING' AND NEW.state = 'PURGING') THEN
            RAISE EXCEPTION
                'history archive run %: the pre-purge backup is recorded when the purge starts',
                OLD.id;
        END IF;
        IF OLD.state = 'RUNNING' AND NEW.state = 'PURGING' AND (
            NEW.verified_at IS NULL OR (NEW.copied_at IS NULL AND NEW.copy_confirmed_at IS NULL)
            OR NEW.pre_purge_backup_name IS NULL OR NEW.pre_purge_backup_started_at IS NULL
            OR NEW.purge_started_at IS NULL) THEN
            RAISE EXCEPTION
                'history archive run %: PURGING needs the verified archive, its off-host copy'
                ' and its pre-purge backup', OLD.id;
        END IF;
        IF OLD.state = 'WAITING_FOR_COPY' AND NEW.state = 'RUNNING'
            AND NEW.copied_at IS NULL AND NEW.copy_confirmed_at IS NULL THEN
            RAISE EXCEPTION
                'history archive run %: a run waits for its copy until it is copied or confirmed',
                OLD.id;
        END IF;
        IF NEW.result = 'EXPORT_ONLY' THEN
            RAISE EXCEPTION 'history archive run %: a run no longer ends as EXPORT_ONLY', OLD.id;
        END IF;
        -- A purging run changes only the counts its committed batches raise.
        IF OLD.state = 'PURGING' AND NEW.state = 'PURGING' AND (
            to_jsonb(NEW) - ARRAY['purged_components', 'purged_flows', 'purged_movements',
                'skipped_components', 'skipped_flows']
            <> to_jsonb(OLD) - ARRAY['purged_components', 'purged_flows', 'purged_movements',
                'skipped_components', 'skipped_flows']
            OR NEW.purged_components < OLD.purged_components
            OR NEW.purged_flows < OLD.purged_flows
            OR NEW.purged_movements < OLD.purged_movements
            OR NEW.skipped_components < OLD.skipped_components
            OR NEW.skipped_flows < OLD.skipped_flows) THEN
            RAISE EXCEPTION
                'history archive run %: a purging run changes only its purged and skipped'
                ' counts, and only upwards', OLD.id;
        END IF;
        IF NEW.state = OLD.state OR (OLD.state, NEW.state) IN (
            ('APPROVED', 'RUNNING'), ('APPROVED', 'FAILED'),
            ('RUNNING', 'WAITING_FOR_COPY'), ('RUNNING', 'NOTHING_ELIGIBLE'),
            ('RUNNING', 'PURGING'), ('RUNNING', 'COMPLETED_WITH_SKIPS'), ('RUNNING', 'FAILED'),
            ('WAITING_FOR_COPY', 'RUNNING'), ('WAITING_FOR_COPY', 'FAILED'),
            ('PURGING', 'COMPLETED'), ('PURGING', 'COMPLETED_WITH_SKIPS'),
            ('PURGING', 'FAILED')) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'history archive run %: the maintenance job cannot change % to %',
            OLD.id, OLD.state, NEW.state;
    END IF;
    -- The maintenance role (it may write archive records and does not own this table)
    -- changes a run only as its job.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION 'history archive run %: the maintenance role changes a run only as its job',
            OLD.id;
    END IF;
    -- The application: cancel an approved or waiting run.
    IF NEW.state = 'CANCELLED' AND OLD.state IN ('APPROVED', 'WAITING_FOR_COPY')
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'cancelled_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'cancelled_by_user_id'] THEN
        RETURN NEW;
    END IF;
    -- The application: confirm the off-host copy of a waiting run, once.
    IF NEW.state = 'WAITING_FOR_COPY' AND OLD.state = 'WAITING_FOR_COPY'
        AND OLD.copy_confirmed_at IS NULL
        AND to_jsonb(NEW) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id']
            = to_jsonb(OLD) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id'] THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'history archive run %: only the maintenance job makes this change', OLD.id;
END;
"""
_FLOWS_BODY = """
DECLARE
    run_state text;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archived_flows rows are never deleted';
    END IF;
    IF current_setting('partflow.archive_run_id', true) IS DISTINCT FROM NEW.run_id::text THEN
        RAISE EXCEPTION
            'archive records of run % are written only by the maintenance job of that run',
            NEW.run_id;
    END IF;
    SELECT r.state INTO run_state FROM public.history_archive_runs r WHERE r.id = NEW.run_id;
    IF TG_OP = 'INSERT' THEN
        IF run_state IS DISTINCT FROM 'RUNNING' OR NEW.skipped_at IS NOT NULL
            OR NEW.purged_at IS NOT NULL THEN
            RAISE EXCEPTION
                'an archive record is written only while its run is RUNNING, unskipped, unpurged';
        END IF;
        RETURN NEW;
    END IF;
    IF to_jsonb(NEW) - ARRAY['skipped_at', 'purged_at']
        <> to_jsonb(OLD) - ARRAY['skipped_at', 'purged_at'] THEN
        RAISE EXCEPTION 'an archive record never changes except when it is skipped or purged';
    END IF;
    IF NEW.skipped_at IS DISTINCT FROM OLD.skipped_at
        AND (OLD.skipped_at IS NOT NULL OR run_state IS NULL
            OR run_state NOT IN ('RUNNING', 'PURGING')) THEN
        RAISE EXCEPTION
            'an archive record is marked skipped once, while its run is RUNNING or PURGING';
    END IF;
    IF NEW.purged_at IS DISTINCT FROM OLD.purged_at
        AND (OLD.purged_at IS NOT NULL OR run_state IS DISTINCT FROM 'PURGING') THEN
        RAISE EXCEPTION 'an archive record is marked purged once, while its run is PURGING';
    END IF;
    RETURN NEW;
END;
"""
_DELETE_BODY = """
DECLARE
    marker text := current_setting('partflow.archive_run_id', true);
    allowed boolean;
BEGIN
    -- Only the purge of the archival run that recorded the row's flow deletes it:
    -- every condition is checked for every row, and a row that fails one raises.
    IF marker ~ '^[1-9][0-9]{0,8}$' THEN
        SELECT true INTO allowed
        FROM public.history_archive_runs r
        JOIN public.history_archived_flows f ON f.run_id = r.id
        WHERE r.id = marker::integer AND r.state = 'PURGING'
            AND f.flow_id = OLD.quantity_flow_id
            AND f.purged_at IS NULL AND f.skipped_at IS NULL
            AND OLD.id <= f.last_movement_id
            AND OLD.occurred_at < r.cutoff AND OLD.server_received_at < r.cutoff;
    END IF;
    IF allowed IS NULL THEN
        RAISE EXCEPTION
            'part_movements is append-only: Movement % may be deleted only by the purge of'
            ' the archival run that archived its flow', OLD.id;
    END IF;
    RETURN OLD;
END;
"""


def _function(name: str, body: str, *, replace: bool) -> str:
    verb = "CREATE OR REPLACE" if replace else "CREATE"
    return f"{verb} FUNCTION {name}() RETURNS trigger\nLANGUAGE plpgsql AS $${body}$$;\n"


_PREVIOUS_MOVEMENTS_TRIGGER = (
    f"CREATE TRIGGER {_MOVEMENTS_TRIGGER} BEFORE UPDATE OR DELETE OR TRUNCATE ON {_MOVEMENTS}"
    f" FOR EACH STATEMENT EXECUTE FUNCTION {_MOVEMENTS_FUNCTION}()"
)
_MOVEMENTS_TRIGGER_SQL = (
    f"CREATE TRIGGER {_MOVEMENTS_TRIGGER} BEFORE UPDATE OR TRUNCATE ON {_MOVEMENTS}"
    f" FOR EACH STATEMENT EXECUTE FUNCTION {_MOVEMENTS_FUNCTION}()"
)
_DELETE_TRIGGER_SQL = (
    f"CREATE TRIGGER {_DELETE_TRIGGER} BEFORE DELETE ON {_MOVEMENTS}"
    f" FOR EACH ROW EXECUTE FUNCTION {_DELETE_FUNCTION}()"
)


def _range_check(column: str) -> str:
    return f"ck_application_policy_{column}_range"


def _run_check(suffix: str) -> str:
    return f"ck_history_archive_runs_{suffix}"


def _open_index(states: Iterable[str]) -> None:
    op.create_index(
        _OPEN_INDEX,
        _RUNS,
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(_in("state", states)),
    )


def upgrade() -> None:
    for column, column_type in _RUN_COLUMNS:
        op.add_column(_RUNS, sa.Column(column, column_type, nullable=True))
    for suffix, _previous, sql in _CHANGED_RUN_CHECKS:
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, sql)
    for suffix, sql in _NEW_RUN_CHECKS:
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, sql)
    op.drop_index(_OPEN_INDEX, table_name=_RUNS)
    _open_index(_OPEN_STATES)
    for name, columns, where in _RECORD_INDEXES:
        op.create_index(name, _FLOWS, list(columns), postgresql_where=sa.text(where))
    for column, default, minimum, maximum in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
        op.create_check_constraint(
            op.f(_range_check(column)), _POLICY, f"{column} BETWEEN {minimum} AND {maximum}"
        )
    op.execute(_function(_RUNS_FUNCTION, _RUNS_BODY, replace=True))
    op.execute(_function(_FLOWS_FUNCTION, _FLOWS_BODY, replace=True))
    op.execute(_function(_DELETE_FUNCTION, _DELETE_BODY, replace=False))
    # One transaction: the table is never without its guard.
    op.execute(f"DROP TRIGGER {_MOVEMENTS_TRIGGER} ON {_MOVEMENTS}")
    op.execute(_MOVEMENTS_TRIGGER_SQL)
    op.execute(_DELETE_TRIGGER_SQL)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    changed = " OR ".join(f"{column} <> {default}" for column, default, *_ in _POLICY_COLUMNS)
    purge_columns = " OR ".join(f"{column} IS NOT NULL" for column, _ in _RUN_COLUMNS)
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_RUNS} WHERE state IN ('PURGING', 'COMPLETED_WITH_SKIPS')"
        f"   OR result IN ('PURGED', 'NOTHING_PURGED') OR {purge_columns})"
        f"  OR EXISTS (SELECT 1 FROM {_FLOWS} WHERE purged_at IS NOT NULL)"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        " THEN RAISE EXCEPTION 'History purge data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.execute(f"DROP TRIGGER {_DELETE_TRIGGER} ON {_MOVEMENTS}")
    op.execute(f"DROP TRIGGER {_MOVEMENTS_TRIGGER} ON {_MOVEMENTS}")
    op.execute(_PREVIOUS_MOVEMENTS_TRIGGER)
    op.execute(f"DROP FUNCTION {_DELETE_FUNCTION}()")
    op.execute(_function(_FLOWS_FUNCTION, _PREVIOUS_FLOWS_BODY, replace=True))
    op.execute(_function(_RUNS_FUNCTION, _PREVIOUS_RUNS_BODY, replace=True))
    for column, *_ in reversed(_POLICY_COLUMNS):
        op.drop_constraint(op.f(_range_check(column)), _POLICY, type_="check")
        op.drop_column(_POLICY, column)
    for name, _columns, _where in reversed(_RECORD_INDEXES):
        op.drop_index(name, table_name=_FLOWS)
    op.drop_index(_OPEN_INDEX, table_name=_RUNS)
    _open_index(_PREVIOUS_OPEN_STATES)
    for suffix, _sql in reversed(_NEW_RUN_CHECKS):
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
    for suffix, previous, _sql in reversed(_CHANGED_RUN_CHECKS):
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, previous)
    for column, _type in reversed(_RUN_COLUMNS):
        op.drop_column(_RUNS, column)


# The 0040 function bodies the downgrade restores (the schema test asserts
# that they are exactly 0040's).
_PREVIOUS_RUNS_BODY = f"""
DECLARE
    shortest integer;
    newest_cutoff timestamptz;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archive_runs rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT p.shortest_retention_months INTO shortest
        FROM public.application_policy p WHERE p.id = 1;
        -- Four days' allowance: the exact rule counts the months back from the site's
        -- date, which can be a day ahead of UTC at the end of a month.
        newest_cutoff := ((now() AT TIME ZONE 'UTC') - make_interval(months => shortest)
            + interval '4 days') AT TIME ZONE 'UTC';
        IF shortest IS NULL OR NEW.cutoff > newest_cutoff THEN
            RAISE EXCEPTION
                'archive run cutoff % is newer than the shortest allowed period (% months)',
                NEW.cutoff, shortest
                USING ERRCODE = 'check_violation', CONSTRAINT = '{_CUTOFF_FLOOR}';
        END IF;
        IF NEW.state <> 'APPROVED' OR NEW.started_at IS NOT NULL OR NEW.exported_at IS NOT NULL
            OR NEW.verified_at IS NOT NULL OR NEW.copied_at IS NOT NULL
            OR NEW.copy_confirmed_at IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
            RAISE EXCEPTION 'a history archive run is requested APPROVED, with no progress';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('COMPLETED', 'NOTHING_ELIGIBLE', 'CANCELLED', 'FAILED') THEN
        RAISE EXCEPTION 'history archive run % is finished (%) and never changes',
            OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.cutoff, NEW.reason, NEW.requested_by_user_id, NEW.requested_at,
        NEW.preview_components, NEW.preview_flows, NEW.preview_movements,
        NEW.preview_first_occurred_at, NEW.preview_last_occurred_at, NEW.preview_archive_bytes)
        IS DISTINCT FROM
        (OLD.id, OLD.cutoff, OLD.reason, OLD.requested_by_user_id, OLD.requested_at,
        OLD.preview_components, OLD.preview_flows, OLD.preview_movements,
        OLD.preview_first_occurred_at, OLD.preview_last_occurred_at, OLD.preview_archive_bytes)
    THEN
        RAISE EXCEPTION 'the request of history archive run % never changes', OLD.id;
    END IF;
    -- The maintenance job: a role that may write archive records, working on this run.
    -- Only a person confirms the off-host copy or cancels a run.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND current_setting('partflow.archive_run_id', true) = OLD.id::text THEN
        IF (NEW.copy_confirmed_at, NEW.copy_confirmed_by_user_id, NEW.cancelled_by_user_id)
            IS DISTINCT FROM
            (OLD.copy_confirmed_at, OLD.copy_confirmed_by_user_id, OLD.cancelled_by_user_id)
        THEN
            RAISE EXCEPTION
                'history archive run %: the maintenance job never confirms a copy or cancels',
                OLD.id;
        END IF;
        IF NEW.state = OLD.state OR (OLD.state, NEW.state) IN (
            ('APPROVED', 'RUNNING'), ('APPROVED', 'FAILED'),
            ('RUNNING', 'WAITING_FOR_COPY'), ('RUNNING', 'COMPLETED'),
            ('RUNNING', 'NOTHING_ELIGIBLE'), ('RUNNING', 'FAILED'),
            ('WAITING_FOR_COPY', 'COMPLETED'), ('WAITING_FOR_COPY', 'FAILED')) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'history archive run %: the maintenance job cannot change % to %',
            OLD.id, OLD.state, NEW.state;
    END IF;
    -- The maintenance role (it may write archive records and does not own this table)
    -- changes a run only as its job.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION 'history archive run %: the maintenance role changes a run only as its job',
            OLD.id;
    END IF;
    -- The application: cancel an approved or waiting run.
    IF NEW.state = 'CANCELLED' AND OLD.state IN ('APPROVED', 'WAITING_FOR_COPY')
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'cancelled_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'cancelled_by_user_id'] THEN
        RETURN NEW;
    END IF;
    -- The application: confirm the off-host copy of a waiting run, once.
    IF NEW.state = 'WAITING_FOR_COPY' AND OLD.state = 'WAITING_FOR_COPY'
        AND OLD.copy_confirmed_at IS NULL
        AND to_jsonb(NEW) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id']
            = to_jsonb(OLD) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id'] THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'history archive run %: only the maintenance job makes this change', OLD.id;
END;
"""
_PREVIOUS_FLOWS_BODY = """
DECLARE
    run_state text;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archived_flows rows are never deleted';
    END IF;
    IF current_setting('partflow.archive_run_id', true) IS DISTINCT FROM NEW.run_id::text THEN
        RAISE EXCEPTION
            'archive records of run % are written only by the maintenance job of that run',
            NEW.run_id;
    END IF;
    SELECT r.state INTO run_state FROM public.history_archive_runs r WHERE r.id = NEW.run_id;
    IF TG_OP = 'INSERT' THEN
        IF run_state IS DISTINCT FROM 'RUNNING' OR NEW.skipped_at IS NOT NULL
            OR NEW.purged_at IS NOT NULL THEN
            RAISE EXCEPTION
                'an archive record is written only while its run is RUNNING, unskipped, unpurged';
        END IF;
        RETURN NEW;
    END IF;
    IF to_jsonb(NEW) - ARRAY['skipped_at', 'purged_at']
        <> to_jsonb(OLD) - ARRAY['skipped_at', 'purged_at'] THEN
        RAISE EXCEPTION 'an archive record never changes except when it is skipped or purged';
    END IF;
    IF NEW.skipped_at IS DISTINCT FROM OLD.skipped_at
        AND (OLD.skipped_at IS NOT NULL OR run_state IS DISTINCT FROM 'RUNNING') THEN
        RAISE EXCEPTION 'an archive record is marked skipped once, while its run is RUNNING';
    END IF;
    IF NEW.purged_at IS DISTINCT FROM OLD.purged_at
        AND (OLD.purged_at IS NOT NULL OR run_state IS DISTINCT FROM 'PURGING') THEN
        RAISE EXCEPTION 'an archive record is marked purged once, while its run is PURGING';
    END IF;
    RETURN NEW;
END;
"""
