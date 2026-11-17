"""Administration → Security: password rules and Remember me.

Owner request of 2026-11-17: Administration gains a Security section that
gathers every sign-in setting, with two new groups of settings.

- ``application_policy`` gains the password rules every new password must
  satisfy — ``password_min_length`` (default 6) and ``password_max_length``
  (default 256), each CHECK ``ck_application_policy_<column>_range``
  (``BETWEEN 4 AND 256``) with ``ck_application_policy_password_length_order``
  (the minimum never above the maximum), and ``password_require_digit``,
  ``password_require_special``, ``password_require_uppercase`` and
  ``password_require_lowercase`` (default false) — and Remember me:
  ``remember_me_offered`` (default false: the sign-in dialog does not offer
  it), ``remember_me_expires`` (default true; false = a remembered sign-in
  lasts until the user signs out) and ``remember_me_days`` (default 30, CHECK
  ``ck_application_policy_remember_me_days_range``: ``BETWEEN 1 AND 365``).
  The defaults are the behavior before this revision: 6 to 256 characters,
  no composition rule and no Remember me. Audited as the ``password-rules``
  and ``remember-me`` sections.
- ``user_sessions.remembered`` (boolean, default false): the user chose
  Remember me when signing in; every existing session is unremembered.

No table is added, so no grant changes.

The downgrade REFUSES while a policy value differs from its default, a
session is remembered or an ``ApplicationPolicy`` audit row of either
section exists, so no configuration or history is silently dropped;
``alembic/env.py`` runs in one transaction, so the database then stays at
this revision. Run it only against disposable development and test
databases.

Revision ID: 0039_security_settings
Revises: 0038_phase17_passkeys
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039_security_settings"
down_revision: str | None = "0038_phase17_passkeys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_POLICY = "application_policy"
_SESSIONS = "user_sessions"
_SECTIONS = ("password-rules", "remember-me")
# (column, server default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_RANGE_COLUMNS = (
    ("password_min_length", 6, 4, 256),
    ("password_max_length", 256, 4, 256),
    ("remember_me_days", 30, 1, 365),
)
# (column, server default) of the switches.
_SWITCH_COLUMNS = (
    ("password_require_digit", False),
    ("password_require_special", False),
    ("password_require_uppercase", False),
    ("password_require_lowercase", False),
    ("remember_me_offered", False),
    ("remember_me_expires", True),
)
_LENGTH_ORDER_CHECK = "ck_application_policy_password_length_order"
_LENGTH_ORDER_SQL = "password_min_length <= password_max_length"


def _range_check(column: str) -> str:
    return f"ck_application_policy_{column}_range"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def upgrade() -> None:
    for column, default, minimum, maximum in _RANGE_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
        op.create_check_constraint(
            op.f(_range_check(column)), _POLICY, f"{column} BETWEEN {minimum} AND {maximum}"
        )
    op.create_check_constraint(op.f(_LENGTH_ORDER_CHECK), _POLICY, _LENGTH_ORDER_SQL)
    for column, switch in _SWITCH_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.text(_bool(switch))),
        )
    op.add_column(
        _SESSIONS,
        sa.Column("remembered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    changed = " OR ".join(
        [f"{column} <> {default}" for column, default, *_ in _RANGE_COLUMNS]
        + [f"{column} <> {_bool(switch)}" for column, switch in _SWITCH_COLUMNS]
    )
    sections = ", ".join(f"'{section}'" for section in _SECTIONS)
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        f"  OR EXISTS (SELECT 1 FROM {_SESSIONS} WHERE remembered)"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        f"             WHERE entity_type = 'ApplicationPolicy' AND entity_id IN ({sections}))"
        " THEN RAISE EXCEPTION 'Security settings data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_column(_SESSIONS, "remembered")
    for column, _switch in reversed(_SWITCH_COLUMNS):
        op.drop_column(_POLICY, column)
    op.drop_constraint(op.f(_LENGTH_ORDER_CHECK), _POLICY, type_="check")
    for column, *_ in reversed(_RANGE_COLUMNS):
        op.drop_constraint(op.f(_range_check(column)), _POLICY, type_="check")
        op.drop_column(_POLICY, column)
