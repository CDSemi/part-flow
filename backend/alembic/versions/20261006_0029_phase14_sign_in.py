"""Phase 14: sign-in for application Users.

Exactly the additive schema slice 1 needs (IMPLEMENTATION_ROADMAP
Phase 14; PROJECT_PROFILE §7 User; owner decisions OD-P1–OD-P5, OD-P17):

- the user sign-in policy on the `application_policy` singleton
  (Administration → Settings → User sign-in): `user_session_expires`
  (default true), `user_session_days` (1-365, default 30),
  `sign_in_lockout_attempts` (3-100, default 10),
  `sign_in_lockout_minutes` (1-1440, default 15) and
  `require_password_change` (default true). Constant defaults, so the
  seeded row receives them without a data statement;
- `user_credentials` — one password per User (the primary key is the
  User's id): the scrypt hash (its parameters carried by the value), the
  temporary flag, when it changed, the failure counter and the lock.
  Credentials never live on `users` and never in audit data;
- `user_sessions` — one row per sign-in: only the SHA-256 digest of the
  opaque session token is stored (UNIQUE, 32 bytes), the creation time
  and, once PartFlow closes it, when and why. No expiry column: expiry
  is derived from the current policy. Ended rows are kept as history;
- `actor_user_id` (nullable FK → `users`) on `audit_events`,
  `machine_lifecycle_events` and `work_order_allocations` — written only
  from the server-side principal, never from a client; the legacy text
  actors (`actor_reference`, `actor`) are kept and never backfilled. No
  index: users are never deleted. Metadata-only ADD COLUMNs; the
  append-only row triggers are untouched.

Workers are never Users: nothing here references the Worker registry,
and User sessions are never Worker Sessions.

The downgrade REFUSES — it never deletes credentials, sessions, actor
links or sign-in configuration: any credential, any session, any
`actor_user_id`, any `sign-in` policy audit row or any non-default
sign-in policy value raises; `alembic/env.py` runs in one transaction,
so the database then stays at this revision. Run it only against
disposable development and test databases.

Revision ID: 0029_phase14_sign_in
Revises: 0028_phase13_users_roles
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_phase14_sign_in"
down_revision: str | None = "0028_phase13_users_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant,
# _END_REASONS equals UserSessionEndReason and _SECTION equals
# policies.SIGN_IN_SECTION.
_POLICY, _CREDENTIALS, _SESSIONS = "application_policy", "user_credentials", "user_sessions"
_ACTOR_TABLES = ("audit_events", "machine_lifecycle_events", "work_order_allocations")
_SESSION_DAYS_SQL = "user_session_days BETWEEN 1 AND 365"
_LOCKOUT_ATTEMPTS_SQL = "sign_in_lockout_attempts BETWEEN 3 AND 100"
_LOCKOUT_MINUTES_SQL = "sign_in_lockout_minutes BETWEEN 1 AND 1440"
_HASH_FORMAT_SQL = "password_hash LIKE 'scrypt$%'"
_FAILED_ATTEMPTS_SQL = "failed_attempts >= 0"
_TOKEN_DIGEST_SQL = "octet_length(token_digest) = 32"
_END_REASONS = ("SIGNED_OUT", "REPLACED", "PASSWORD_CHANGED", "PASSWORD_RESET", "USER_DEACTIVATED")
_END_REASON_SQL = "end_reason IN (" + ", ".join(f"'{r}'" for r in _END_REASONS) + ")"
_END_SHAPE_SQL = "(ended_at IS NULL) = (end_reason IS NULL)"
_SECTION = "sign-in"

_POLICY_COLUMNS = (
    ("user_session_expires", sa.Boolean(), "true"),
    ("user_session_days", sa.Integer(), "30"),
    ("sign_in_lockout_attempts", sa.Integer(), "10"),
    ("sign_in_lockout_minutes", sa.Integer(), "15"),
    ("require_password_change", sa.Boolean(), "true"),
)
_POLICY_CHECKS = (
    ("ck_application_policy_user_session_days_range", _SESSION_DAYS_SQL),
    ("ck_application_policy_sign_in_lockout_attempts_range", _LOCKOUT_ATTEMPTS_SQL),
    ("ck_application_policy_sign_in_lockout_minutes_range", _LOCKOUT_MINUTES_SQL),
)


def _actor_fk(table: str) -> str:
    return f"fk_{table}_actor_user_id_users"


def upgrade() -> None:
    for column, type_, default in _POLICY_COLUMNS:
        op.add_column(
            _POLICY, sa.Column(column, type_, nullable=False, server_default=sa.text(default))
        )
    for name, sql in _POLICY_CHECKS:
        op.create_check_constraint(op.f(name), _POLICY, sql)

    op.create_table(
        _CREDENTIALS,
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("password_is_temporary", sa.Boolean(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_credentials"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_credentials_user_id_users"
        ),
        sa.CheckConstraint(_HASH_FORMAT_SQL, name=op.f("ck_user_credentials_password_hash_format")),
        sa.CheckConstraint(
            _FAILED_ATTEMPTS_SQL, name=op.f("ck_user_credentials_failed_attempts_non_negative")
        ),
    )

    op.create_table(
        _SESSIONS,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_sessions_user_id_users"),
        sa.UniqueConstraint("token_digest", name="uq_user_sessions_token_digest"),
        sa.CheckConstraint(_TOKEN_DIGEST_SQL, name=op.f("ck_user_sessions_token_digest_length")),
        sa.CheckConstraint(_END_REASON_SQL, name=op.f("ck_user_sessions_end_reason")),
        sa.CheckConstraint(_END_SHAPE_SQL, name=op.f("ck_user_sessions_end_shape")),
    )
    op.create_index(
        "ix_user_sessions_user_id_open",
        _SESSIONS,
        ["user_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    for table in _ACTOR_TABLES:
        op.add_column(table, sa.Column("actor_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(_actor_fk(table), table, "users", ["actor_user_id"], ["id"])


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Credentials, sessions, actor links and sign-in
    # configuration are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM user_credentials) OR EXISTS (SELECT 1 FROM user_sessions)"
        "  OR EXISTS (SELECT 1 FROM audit_events WHERE actor_user_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM machine_lifecycle_events WHERE actor_user_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM work_order_allocations WHERE actor_user_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
        f"   AND entity_id = '{_SECTION}')"
        "  OR EXISTS (SELECT 1 FROM application_policy WHERE NOT user_session_expires"
        "   OR user_session_days <> 30 OR sign_in_lockout_attempts <> 10"
        "   OR sign_in_lockout_minutes <> 15 OR NOT require_password_change)"
        " THEN RAISE EXCEPTION 'Sign-in data or configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for table in reversed(_ACTOR_TABLES):
        op.drop_constraint(_actor_fk(table), table, type_="foreignkey")
        op.drop_column(table, "actor_user_id")
    op.drop_index("ix_user_sessions_user_id_open", table_name=_SESSIONS)
    op.drop_table(_SESSIONS)
    op.drop_table(_CREDENTIALS)
    for name, _sql in reversed(_POLICY_CHECKS):
        op.drop_constraint(op.f(name), _POLICY, type_="check")
    for column, _type, _default in reversed(_POLICY_COLUMNS):
        op.drop_column(_POLICY, column)
