"""Phase 16: automatic history archival runs (design B, step 4; decisions D9-D11).

The fourth build step of the approved Movement-history archival design
(``_claude_outputs/phase16/ARCHIVAL_DESIGN.md`` §6.1, §6.2, §8, §9.1 and §13
step 4; IMPLEMENTATION_ROADMAP Phase 16): the host job's ``history-archive
evaluate`` creates a run when a measure passes its limit, and a run records
who started and who authorized it.

- ``history_archive_runs``: the states ``PROPOSED`` (open: an automatic run
  waiting for approval; the unique open index is rebuilt with it) and
  ``DISMISSED`` (final: a proposed run a person dismissed); the columns
  ``trigger`` (``MANUAL``, the default and every earlier run, or
  ``AUTOMATIC``), ``approved_by_user_id``, ``approved_at`` and
  ``approval_reason`` (the approval of a proposed run, set once together),
  ``dismissed_by_user_id`` (set exactly when ``DISMISSED``),
  ``authorization_id`` (the ``audit_events`` row of the standing
  authorization an automatic run was created under) and
  ``authorization_failed`` (the run failed because the User of its
  authority may no longer run it); ``requested_by_user_id`` becomes NULL
  exactly for an automatic run. CHECKs: the trigger, the requester, the
  approval's shape and the authority — a manual run is authorized by its
  request, a proposed or dismissed automatic run by nobody, any other
  automatic run by exactly one of an approval or the standing
  authorization.
- The run guard function (replaced): every insert keeps the cutoff floor
  and has no progress; an automatic run is inserted only by a role that may
  write archive records (the maintenance role or the owner), without a
  requesting User, ``PROPOSED`` without an authorization or ``APPROVED``
  with the standing authorization the approval setting holds while the
  approval step is off; the maintenance role inserts no manual run. The
  request columns (now with the trigger and the authorization) never
  change. The application role may also approve a ``PROPOSED`` run (state,
  approver, time and reason only) or dismiss it (state, end time and who
  dismissed it only); nothing else changes for it. The job never moves,
  approves or dismisses a ``PROPOSED`` run.
- ``application_policy``: ``archive_automatic_runs`` (off), ``archive_measure``
  (``LIVE_MOVEMENT_ROWS``, ``RETAINED_HISTORY_AGE`` or ``DATABASE_SIZE``),
  the limits ``archive_limit_movement_rows`` (5,000,000; 0-1,000,000,000),
  ``archive_limit_history_months`` (1; 0-1200) and
  ``archive_limit_database_gib`` (20; 0-100,000), ``archive_approval_required``
  (on), ``archive_interval_days`` (7; 1-365) and ``archive_authorization_id``
  (the ``audit_events`` row of the standing authorization, set exactly while
  the approval step is off).

The maintenance role's new INSERT on ``history_archive_runs`` is a grant,
derived by ``apply-grants`` in every ``migrate`` from
``app.infrastructure.database_privileges``.

The downgrade REFUSES while a run is ``PROPOSED`` or ``DISMISSED``, is
automatic, has an approval, a dismissal, an authorization or a failed
authority, or while a new setting differs from its default; otherwise it
restores ``0042`` exactly. ``alembic/env.py`` runs in one transaction. Run it
only against disposable development and test databases.

Revision ID: 0043_phase16_automatic_runs
Revises: 0042_phase16_deletion_requests
Create Date: 2026-11-18

"""

from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0043_phase16_automatic_runs"
down_revision: str | None = "0042_phase16_deletion_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py or enums
# counterpart, and each _PREVIOUS_ value equals migration 0041's.
_RUNS = "history_archive_runs"
_POLICY = "application_policy"
_AUDIT = "audit_events"

_PREVIOUS_STATES = (
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
_STATES = (
    "PROPOSED",
    "APPROVED",
    "RUNNING",
    "WAITING_FOR_COPY",
    "PURGING",
    "COMPLETED",
    "COMPLETED_WITH_SKIPS",
    "NOTHING_ELIGIBLE",
    "DISMISSED",
    "CANCELLED",
    "FAILED",
)
_PREVIOUS_FINAL_STATES = (
    "COMPLETED",
    "COMPLETED_WITH_SKIPS",
    "NOTHING_ELIGIBLE",
    "CANCELLED",
    "FAILED",
)
_FINAL_STATES = (
    "COMPLETED",
    "COMPLETED_WITH_SKIPS",
    "NOTHING_ELIGIBLE",
    "DISMISSED",
    "CANCELLED",
    "FAILED",
)
_PREVIOUS_OPEN_STATES = ("APPROVED", "RUNNING", "WAITING_FOR_COPY", "PURGING")
_OPEN_STATES = ("PROPOSED", "APPROVED", "RUNNING", "WAITING_FOR_COPY", "PURGING")
_RUN_TRIGGERS = ("MANUAL", "AUTOMATIC")
_MEASURES = ("LIVE_MOVEMENT_ROWS", "RETAINED_HISTORY_AGE", "DATABASE_SIZE")


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_OPEN_INDEX = "uq_history_archive_runs_open"

# The run CHECKs this revision replaces: (suffix, 0041 SQL, this revision's SQL).
_CHANGED_RUN_CHECKS = (
    ("state", _in("state", _PREVIOUS_STATES), _in("state", _STATES)),
    (
        "progress_shape",
        "(state <> 'APPROVED' OR started_at IS NULL)"
        " AND (state NOT IN ('RUNNING', 'WAITING_FOR_COPY', 'PURGING', 'COMPLETED',"
        " 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE') OR started_at IS NOT NULL)"
        " AND (state NOT IN ('WAITING_FOR_COPY', 'PURGING', 'COMPLETED', 'COMPLETED_WITH_SKIPS')"
        " OR verified_at IS NOT NULL)",
        "(state NOT IN ('PROPOSED', 'APPROVED', 'DISMISSED') OR started_at IS NULL)"
        " AND (state NOT IN ('RUNNING', 'WAITING_FOR_COPY', 'PURGING', 'COMPLETED',"
        " 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE') OR started_at IS NOT NULL)"
        " AND (state NOT IN ('WAITING_FOR_COPY', 'PURGING', 'COMPLETED', 'COMPLETED_WITH_SKIPS')"
        " OR verified_at IS NOT NULL)",
    ),
    (
        "finish_shape",
        "(" + _in("state", _PREVIOUS_FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)",
        "(" + _in("state", _FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)"
        " AND (state = 'DISMISSED') = (dismissed_by_user_id IS NOT NULL)",
    ),
)
# The run CHECKs this revision adds: (suffix, SQL).
_NEW_RUN_CHECKS = (
    ("trigger", _in("trigger", _RUN_TRIGGERS)),
    ("requester_shape", "(trigger = 'MANUAL') = (requested_by_user_id IS NOT NULL)"),
    (
        "approval_shape",
        "(approved_at IS NULL) = (approved_by_user_id IS NULL)"
        " AND (approved_at IS NULL) = (approval_reason IS NULL)"
        " AND (approval_reason IS NULL OR char_length(approval_reason) BETWEEN 1 AND 500)"
        " AND (approved_at IS NULL OR approved_at >= requested_at)",
    ),
    (
        "authority_shape",
        "(trigger = 'MANUAL' AND approved_at IS NULL AND authorization_id IS NULL)"
        " OR (trigger = 'AUTOMATIC' AND state IN ('PROPOSED', 'DISMISSED')"
        " AND approved_at IS NULL AND authorization_id IS NULL)"
        " OR (trigger = 'AUTOMATIC' AND state NOT IN ('PROPOSED', 'DISMISSED')"
        " AND (approved_at IS NULL) <> (authorization_id IS NULL))",
    ),
    ("authorization_failed_shape", "NOT authorization_failed OR state = 'FAILED'"),
)
# (column, type, server default or None) appended to history_archive_runs, in
# the model's order; every one is nullable unless it has a server default.
_RUN_COLUMNS: tuple[tuple[str, Any, str | None], ...] = (
    ("trigger", sa.Text(), "'MANUAL'"),
    ("approved_by_user_id", sa.Integer(), None),
    ("approved_at", sa.DateTime(timezone=True), None),
    ("approval_reason", sa.Text(), None),
    ("dismissed_by_user_id", sa.Integer(), None),
    ("authorization_id", sa.BigInteger(), None),
    ("authorization_failed", sa.Boolean(), "false"),
)
# (name, table, column, referenced table) of the foreign keys this revision adds.
_FOREIGN_KEYS = (
    ("fk_history_archive_runs_approved_by_user_id_users", _RUNS, "approved_by_user_id", "users"),
    ("fk_history_archive_runs_dismissed_by_user_id_users", _RUNS, "dismissed_by_user_id", "users"),
    ("fk_history_archive_runs_authorization_id_audit_events", _RUNS, "authorization_id", _AUDIT),
    (
        "fk_application_policy_archive_authorization_id_audit_events",
        _POLICY,
        "archive_authorization_id",
        _AUDIT,
    ),
)
# (column, type, server default) appended to application_policy, in the
# model's order; NOT NULL unless the default is None.
_POLICY_COLUMNS: tuple[tuple[str, Any, str | None], ...] = (
    ("archive_automatic_runs", sa.Boolean(), "false"),
    ("archive_measure", sa.Text(), "'LIVE_MOVEMENT_ROWS'"),
    ("archive_limit_movement_rows", sa.Integer(), "5000000"),
    ("archive_limit_history_months", sa.Integer(), "1"),
    ("archive_limit_database_gib", sa.Integer(), "20"),
    ("archive_approval_required", sa.Boolean(), "true"),
    ("archive_interval_days", sa.Integer(), "7"),
    ("archive_authorization_id", sa.BigInteger(), None),
)
# (column, default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_POLICY_RANGES = (
    ("archive_limit_movement_rows", 5000000, 0, 1000000000),
    ("archive_limit_history_months", 1, 0, 1200),
    ("archive_limit_database_gib", 20, 0, 100000),
    ("archive_interval_days", 7, 1, 365),
)
_POLICY_CHECKS = (
    ("ck_application_policy_archive_measure_allowed", _in("archive_measure", _MEASURES)),
    (
        "ck_application_policy_archive_authorization_shape",
        "archive_approval_required = (archive_authorization_id IS NULL)",
    ),
)

# The guard function (pinned by app.infrastructure.database_privileges:
# GUARD_FUNCTION_SHA256); its triggers are unchanged.
_RUNS_FUNCTION = "partflow_history_archive_runs_guard_mutation"
# The constraint name the cutoff refusal reports (SQLSTATE 23514), unchanged.
_CUTOFF_FLOOR = "trg_history_archive_runs_guard_mutation"

_RUNS_BODY = f"""
DECLARE
    shortest integer;
    newest_cutoff timestamptz;
    approval_required boolean;
    standing_id bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archive_runs rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT p.shortest_retention_months, p.archive_approval_required,
            p.archive_authorization_id
            INTO shortest, approval_required, standing_id
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
        IF NEW.started_at IS NOT NULL OR NEW.exported_at IS NOT NULL
            OR NEW.verified_at IS NOT NULL OR NEW.copied_at IS NOT NULL
            OR NEW.copy_confirmed_at IS NOT NULL OR NEW.finished_at IS NOT NULL
            OR NEW.pre_purge_backup_name IS NOT NULL OR NEW.purge_started_at IS NOT NULL
            OR NEW.receipt_sha256 IS NOT NULL OR NEW.approved_at IS NOT NULL
            OR NEW.dismissed_by_user_id IS NOT NULL OR NEW.authorization_failed THEN
            RAISE EXCEPTION 'a history archive run is created with no progress';
        END IF;
        -- An automatic run: only the host job (a role that may write archive records)
        -- creates one, without a requesting User, PROPOSED for approval, or APPROVED
        -- under the standing authorization the approval setting holds now.
        IF NEW.trigger = 'AUTOMATIC' THEN
            IF NOT has_table_privilege('public.history_archived_flows', 'INSERT') THEN
                RAISE EXCEPTION 'only the host job creates an automatic history archive run';
            END IF;
            IF NEW.requested_by_user_id IS NOT NULL THEN
                RAISE EXCEPTION 'an automatic history archive run has no requesting User';
            END IF;
            IF NEW.state = 'PROPOSED' AND NEW.authorization_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NEW.state = 'APPROVED' AND approval_required IS FALSE
                AND NEW.authorization_id = standing_id THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'an automatic history archive run is created PROPOSED, or APPROVED'
                ' under the standing authorization of the approval setting (%), not % with %',
                standing_id, NEW.state, NEW.authorization_id;
        END IF;
        -- A manual run: requested by a person in Administration, never by the
        -- maintenance role (it may write archive records and does not own this table).
        IF has_table_privilege('public.history_archived_flows', 'INSERT')
            AND NOT pg_has_role(
                (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
            RAISE EXCEPTION 'the maintenance role creates automatic history archive runs only';
        END IF;
        IF NEW.state <> 'APPROVED' OR NEW.authorization_id IS NOT NULL THEN
            RAISE EXCEPTION 'a history archive run is requested APPROVED, with no progress';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('COMPLETED', 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE', 'DISMISSED',
        'CANCELLED', 'FAILED') THEN
        RAISE EXCEPTION 'history archive run % is finished (%) and never changes',
            OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.trigger, NEW.cutoff, NEW.reason, NEW.requested_by_user_id, NEW.requested_at,
        NEW.authorization_id, NEW.preview_components, NEW.preview_flows, NEW.preview_movements,
        NEW.preview_first_occurred_at, NEW.preview_last_occurred_at, NEW.preview_archive_bytes)
        IS DISTINCT FROM
        (OLD.id, OLD.trigger, OLD.cutoff, OLD.reason, OLD.requested_by_user_id, OLD.requested_at,
        OLD.authorization_id, OLD.preview_components, OLD.preview_flows, OLD.preview_movements,
        OLD.preview_first_occurred_at, OLD.preview_last_occurred_at, OLD.preview_archive_bytes)
    THEN
        RAISE EXCEPTION 'the request of history archive run % never changes', OLD.id;
    END IF;
    -- The maintenance job: a role that may write archive records, working on this run.
    -- Only a person confirms the off-host copy, cancels a run, or approves or dismisses
    -- a proposed one.
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
        IF OLD.state = 'PROPOSED' OR (NEW.approved_by_user_id, NEW.approved_at,
            NEW.approval_reason, NEW.dismissed_by_user_id)
            IS DISTINCT FROM
            (OLD.approved_by_user_id, OLD.approved_at, OLD.approval_reason,
            OLD.dismissed_by_user_id) THEN
            RAISE EXCEPTION
                'history archive run %: only a person approves or dismisses a proposed run',
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
    -- The application: approve a proposed run with a reason of the approver's own.
    IF NEW.state = 'APPROVED' AND OLD.state = 'PROPOSED'
        AND to_jsonb(NEW) - ARRAY['state', 'approved_by_user_id', 'approved_at', 'approval_reason']
            = to_jsonb(OLD) - ARRAY['state', 'approved_by_user_id', 'approved_at',
                'approval_reason'] THEN
        RETURN NEW;
    END IF;
    -- The application: dismiss a proposed run.
    IF NEW.state = 'DISMISSED' AND OLD.state = 'PROPOSED'
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'dismissed_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'dismissed_by_user_id'] THEN
        RETURN NEW;
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


def _function(name: str, body: str) -> str:
    return f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger\nLANGUAGE plpgsql AS $${body}$$;\n"


def _run_check(suffix: str) -> str:
    return f"ck_history_archive_runs_{suffix}"


def _range_check(column: str) -> str:
    return f"ck_application_policy_{column}_range"


def _open_index(states: Iterable[str]) -> None:
    op.create_index(
        _OPEN_INDEX,
        _RUNS,
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(_in("state", states)),
    )


def _column(name: str, column_type: Any, default: str | None) -> sa.Column[Any]:
    if default is None:
        return sa.Column(name, column_type, nullable=True)
    return sa.Column(name, column_type, nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.alter_column(_RUNS, "requested_by_user_id", existing_type=sa.Integer(), nullable=True)
    for name, column_type, default in _RUN_COLUMNS:
        op.add_column(_RUNS, _column(name, column_type, default))
    for name, column_type, default in _POLICY_COLUMNS:
        op.add_column(_POLICY, _column(name, column_type, default))
    for name, table, column, referenced in _FOREIGN_KEYS:
        op.create_foreign_key(name, table, referenced, [column], ["id"])
    for suffix, _previous, sql in _CHANGED_RUN_CHECKS:
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, sql)
    for suffix, sql in _NEW_RUN_CHECKS:
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, sql)
    op.drop_index(_OPEN_INDEX, table_name=_RUNS)
    _open_index(_OPEN_STATES)
    for column, _default, minimum, maximum in _POLICY_RANGES:
        op.create_check_constraint(
            op.f(_range_check(column)), _POLICY, f"{column} BETWEEN {minimum} AND {maximum}"
        )
    for name, sql in _POLICY_CHECKS:
        op.create_check_constraint(op.f(name), _POLICY, sql)
    op.execute(_function(_RUNS_FUNCTION, _RUNS_BODY))


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    changed = " OR ".join(
        [
            "archive_automatic_runs",
            "archive_measure <> 'LIVE_MOVEMENT_ROWS'",
            *(f"{column} <> {default}" for column, default, *_ in _POLICY_RANGES),
            "NOT archive_approval_required",
            "archive_authorization_id IS NOT NULL",
        ]
    )
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_RUNS} WHERE state IN ('PROPOSED', 'DISMISSED')"
        "   OR trigger <> 'MANUAL' OR approved_at IS NOT NULL OR dismissed_by_user_id IS NOT NULL"
        "   OR authorization_id IS NOT NULL OR authorization_failed)"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        " THEN RAISE EXCEPTION 'Automatic archival run data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.execute(_function(_RUNS_FUNCTION, _PREVIOUS_RUNS_BODY))
    for name, _sql in reversed(_POLICY_CHECKS):
        op.drop_constraint(op.f(name), _POLICY, type_="check")
    for column, *_ in reversed(_POLICY_RANGES):
        op.drop_constraint(op.f(_range_check(column)), _POLICY, type_="check")
    op.drop_index(_OPEN_INDEX, table_name=_RUNS)
    _open_index(_PREVIOUS_OPEN_STATES)
    for suffix, _sql in reversed(_NEW_RUN_CHECKS):
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
    for suffix, previous, _sql in reversed(_CHANGED_RUN_CHECKS):
        op.drop_constraint(op.f(_run_check(suffix)), _RUNS, type_="check")
        op.create_check_constraint(op.f(_run_check(suffix)), _RUNS, previous)
    for name, table, _column_name, _referenced in reversed(_FOREIGN_KEYS):
        op.drop_constraint(name, table, type_="foreignkey")
    for name, *_ in reversed(_POLICY_COLUMNS):
        op.drop_column(_POLICY, name)
    for name, *_ in reversed(_RUN_COLUMNS):
        op.drop_column(_RUNS, name)
    op.alter_column(_RUNS, "requested_by_user_id", existing_type=sa.Integer(), nullable=False)


# The 0041 function body the downgrade restores (the schema test asserts that
# it is exactly 0041's).
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
