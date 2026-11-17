"""Phase 17: two-step sign-in (an authenticator app, recovery codes, an emailed code).

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 8,
optional multi-factor sign-in): each User may add an authenticator app;
with it on, signing in takes the password and then a second step — a code
from the app, a recovery code, or a code emailed to the User. Owner
decisions of 2026-11-17: every number of it is set in Administration →
Settings, a wrong code counts apart from a wrong password, and turning a
User's two-step sign-in off may sign them out everywhere.

- ``user_totp_factors`` — the authenticator app of one User (PK ``user_id``,
  FK to ``users``): ``secret`` (the 160-bit TOTP key, CHECK
  ``ck_user_totp_factors_secret_length``: 20 bytes), ``confirmed_at``
  (NULL = a setup waiting for its first code; two-step sign-in is on exactly
  while a confirmed row exists), ``last_used_step`` (the newest 30-second
  step accepted, so a code works once; ``>= 0``) and ``created_at``.
- ``user_recovery_codes`` — one row per recovery code (identity PK,
  ``user_id`` FK): only the SHA-256 ``code_digest`` (32 bytes, unique per
  User), ``used_at`` (NULL = unused) and ``created_at``.
- ``user_sign_in_challenges`` — the open second step of one sign-in
  (identity PK, ``user_id`` FK): the SHA-256 ``token_digest`` of the opaque
  token in the browser's HttpOnly cookie (unique, 32 bytes),
  ``failed_attempts``, the digest of the last emailed code with the time it
  was sent (both or neither), ``email_codes_sent``, ``created_at``, and
  ``ended_at`` exactly with ``end_reason`` (``COMPLETED``, ``FAILED``,
  ``REPLACED`` or ``RESET``); a partial index serves "the open challenges of
  this User". Expiry is never recorded: it is derived from ``created_at``
  and the current policy.
- ``user_credentials.failed_codes`` (integer, default 0, ``>= 0``) — the
  wrong codes since the User last signed in fully; a correct password resets
  ``failed_attempts`` but never this counter, so knowing the password buys
  no fresh guesses at a code.
- ``user_sessions.end_reason`` admits ``TWO_STEP_RESET`` (an administrator
  or the recovery command turned the User's two-step sign-in off and signed
  them out): CHECK ``ck_user_sessions_end_reason`` is recreated.
- ``application_policy`` gains the two-step policy (Administration →
  Settings → Two-step sign-in; audited as ``two-step``) and the mail
  server's timeout (``mail_timeout_seconds``, the ``mail-server`` section),
  each with its default and range CHECK (``_POLICY_COLUMNS``).

The new tables are ``ORDINARY`` for the application role and outside the
maintenance role's reads (``app.infrastructure.database_privileges``), so
``migrate`` grants them from the code head.

The downgrade REFUSES while any of the three tables holds a row, a session
ended ``TWO_STEP_RESET``, a policy value differs from its default or an
``ApplicationPolicy`` audit row of the ``two-step`` section exists, so no
second factor, configuration or history is silently dropped;
``alembic/env.py`` runs in one transaction, so the database then stays at
this revision. Run it only against disposable development and test
databases.

Revision ID: 0037_phase17_two_step
Revises: 0036_phase17_outgoing_mail
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037_phase17_two_step"
down_revision: str | None = "0036_phase17_outgoing_mail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_FACTORS = "user_totp_factors"
_CODES = "user_recovery_codes"
_CHALLENGES = "user_sign_in_challenges"
_CREDENTIALS = "user_credentials"
_SESSIONS = "user_sessions"
_POLICY = "application_policy"
_SECTION = "two-step"
_TOTP_SECRET_SQL = "octet_length(secret) = 20"
_LAST_USED_STEP_SQL = "last_used_step IS NULL OR last_used_step >= 0"
_CODE_DIGEST_SQL = "octet_length(code_digest) = 32"
_TOKEN_DIGEST_SQL = "octet_length(token_digest) = 32"
_FAILED_ATTEMPTS_SQL = "failed_attempts >= 0"
_EMAIL_CODE_SQL = "email_code_digest IS NULL OR octet_length(email_code_digest) = 32"
_EMAIL_CODE_SHAPE_SQL = "(email_code_digest IS NULL) = (email_code_sent_at IS NULL)"
_EMAIL_CODES_SENT_SQL = "email_codes_sent >= 0"
_END_REASON_SQL = "end_reason IN ('COMPLETED', 'FAILED', 'REPLACED', 'RESET')"
_END_SHAPE_SQL = "(ended_at IS NULL) = (end_reason IS NULL)"
_FAILED_CODES_SQL = "failed_codes >= 0"
_SESSION_END_REASON_CHECK = "ck_user_sessions_end_reason"
_SESSION_END_REASONS_BEFORE = (
    "SIGNED_OUT",
    "REPLACED",
    "PASSWORD_CHANGED",
    "PASSWORD_RESET",
    "USER_DEACTIVATED",
)
_SESSION_END_REASONS = (*_SESSION_END_REASONS_BEFORE, "TWO_STEP_RESET")
# (column, server default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_POLICY_COLUMNS = (
    ("mail_timeout_seconds", 20, 5, 120),
    ("two_step_window_minutes", 15, 1, 60),
    ("two_step_max_codes", 5, 1, 20),
    ("two_step_lockout_codes", 10, 3, 100),
    ("two_step_email_codes", 3, 1, 10),
    ("two_step_email_interval_seconds", 60, 0, 3600),
    ("two_step_email_code_digits", 8, 6, 10),
    ("two_step_recovery_codes", 10, 5, 20),
    ("two_step_recovery_code_length", 12, 8, 20),
    ("two_step_setup_minutes", 15, 1, 60),
    ("two_step_clock_drift_steps", 1, 0, 2),
)


def _reasons_sql(reasons: tuple[str, ...]) -> str:
    return "end_reason IN (" + ", ".join(f"'{reason}'" for reason in reasons) + ")"


def _range_check(column: str) -> str:
    return f"ck_application_policy_{column}_range"


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        _FACTORS,
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_totp_factors"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_totp_factors_user_id_users"
        ),
        sa.CheckConstraint(_TOTP_SECRET_SQL, name=op.f("ck_user_totp_factors_secret_length")),
        sa.CheckConstraint(
            _LAST_USED_STEP_SQL, name=op.f("ck_user_totp_factors_last_used_step_non_negative")
        ),
    )
    op.create_table(
        _CODES,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.LargeBinary(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_user_recovery_codes"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_recovery_codes_user_id_users"
        ),
        sa.UniqueConstraint(
            "user_id", "code_digest", name="uq_user_recovery_codes_user_id_code_digest"
        ),
        sa.CheckConstraint(
            _CODE_DIGEST_SQL, name=op.f("ck_user_recovery_codes_code_digest_length")
        ),
    )
    op.create_table(
        _CHALLENGES,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("email_code_digest", sa.LargeBinary(), nullable=True),
        sa.Column("email_code_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_codes_sent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _created_at(),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_sign_in_challenges"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sign_in_challenges_user_id_users"
        ),
        sa.UniqueConstraint("token_digest", name="uq_user_sign_in_challenges_token_digest"),
        sa.CheckConstraint(
            _TOKEN_DIGEST_SQL, name=op.f("ck_user_sign_in_challenges_token_digest_length")
        ),
        sa.CheckConstraint(
            _FAILED_ATTEMPTS_SQL,
            name=op.f("ck_user_sign_in_challenges_failed_attempts_non_negative"),
        ),
        sa.CheckConstraint(
            _EMAIL_CODE_SQL, name=op.f("ck_user_sign_in_challenges_email_code_digest_length")
        ),
        sa.CheckConstraint(
            _EMAIL_CODE_SHAPE_SQL, name=op.f("ck_user_sign_in_challenges_email_code_shape")
        ),
        sa.CheckConstraint(
            _EMAIL_CODES_SENT_SQL,
            name=op.f("ck_user_sign_in_challenges_email_codes_sent_non_negative"),
        ),
        sa.CheckConstraint(_END_REASON_SQL, name=op.f("ck_user_sign_in_challenges_end_reason")),
        sa.CheckConstraint(_END_SHAPE_SQL, name=op.f("ck_user_sign_in_challenges_end_shape")),
    )
    op.create_index(
        "ix_user_sign_in_challenges_user_id_open",
        _CHALLENGES,
        ["user_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.add_column(
        _CREDENTIALS,
        sa.Column("failed_codes", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_user_credentials_failed_codes_non_negative"), _CREDENTIALS, _FAILED_CODES_SQL
    )
    op.drop_constraint(op.f(_SESSION_END_REASON_CHECK), _SESSIONS, type_="check")
    op.create_check_constraint(
        op.f(_SESSION_END_REASON_CHECK), _SESSIONS, _reasons_sql(_SESSION_END_REASONS)
    )
    for column, default, minimum, maximum in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
        op.create_check_constraint(
            op.f(_range_check(column)), _POLICY, f"{column} BETWEEN {minimum} AND {maximum}"
        )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    changed = " OR ".join(f"{column} <> {default}" for column, default, *_ in _POLICY_COLUMNS)
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_FACTORS})"
        f"  OR EXISTS (SELECT 1 FROM {_CODES})"
        f"  OR EXISTS (SELECT 1 FROM {_CHALLENGES})"
        f"  OR EXISTS (SELECT 1 FROM {_SESSIONS} WHERE end_reason = 'TWO_STEP_RESET')"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        f"             WHERE entity_type = 'ApplicationPolicy' AND entity_id = '{_SECTION}')"
        " THEN RAISE EXCEPTION 'Two-step sign-in data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for column, *_ in reversed(_POLICY_COLUMNS):
        op.drop_constraint(op.f(_range_check(column)), _POLICY, type_="check")
        op.drop_column(_POLICY, column)
    op.drop_constraint(op.f(_SESSION_END_REASON_CHECK), _SESSIONS, type_="check")
    op.create_check_constraint(
        op.f(_SESSION_END_REASON_CHECK), _SESSIONS, _reasons_sql(_SESSION_END_REASONS_BEFORE)
    )
    op.drop_constraint(
        op.f("ck_user_credentials_failed_codes_non_negative"), _CREDENTIALS, type_="check"
    )
    op.drop_column(_CREDENTIALS, "failed_codes")
    op.drop_index("ix_user_sign_in_challenges_user_id_open", table_name=_CHALLENGES)
    op.drop_table(_CHALLENGES)
    op.drop_table(_CODES)
    op.drop_table(_FACTORS)
