"""Phase 17: a User's email address and the outgoing mail server.

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 8,
optional multi-factor sign-in): a User who loses their other sign-in
methods may receive a one-time sign-in code by email, which needs an email
address on the User and an outgoing mail server set in Administration →
Settings.

- ``users.email`` (text, nullable) — the canonical address
  (``app.domain.email_address``): CHECK ``ck_users_email_canonical``
  admits a local part of 1-64 permitted ASCII characters, ``@`` and a
  lowercase domain with at least one dot, 254 characters at most. Not
  unique: two Users may share a mailbox.
- ``application_policy`` gains the outgoing mail server: ``mail_host``
  (nullable; NULL = no mail server; a lowercase host name or IP address of
  at most 253 characters), ``mail_port`` (default 587, 1-65535),
  ``mail_security`` (default ``STARTTLS``; ``STARTTLS``, ``TLS`` or
  ``NONE``), ``mail_username`` (nullable, 1-254 characters) and
  ``mail_sender`` (nullable; a canonical address). CHECK
  ``ck_application_policy_mail_server_shape`` keeps the server whole: a
  host exactly with a sender, a user name only with a host, and no sign-in
  over an unencrypted (``NONE``) connection.
- ``mail_server_credentials`` (new) holds the password of that sign-in:
  at most one row (CHECK ``ck_mail_server_credentials_singleton``, id = 1;
  no row = no password), 1-1024 characters. A credential never lives on
  the configuration row, which the maintenance role reads; this table is
  ``ORDINARY`` for the application role and outside the maintenance role's
  reads (``app.infrastructure.database_privileges``), so ``migrate`` grants
  it from the code head.

Every new column is nullable or added with a constant server default, so
the ADD COLUMNs are metadata-only and the seeded singleton holds no mail
server. No index and no audit CHECK change (``User`` and
``ApplicationPolicy`` are already admitted; the section's audit rows use
``entity_id`` ``mail-server``).

The downgrade REFUSES once either was written — a User with an email
address, a stored mail server, a password, a port or security other than
the defaults, or an ``ApplicationPolicy`` audit row of the ``mail-server``
section raises — so configuration and its history are never silently
lost; ``alembic/env.py`` runs in one transaction, so the database then
stays at this revision. Run it only against disposable development and
test databases.

Revision ID: 0036_phase17_outgoing_mail
Revises: 0035_phase16_backup_policy
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036_phase17_outgoing_mail"
down_revision: str | None = "0035_phase16_backup_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant and
# _SECTION equals policies.MAIL_SERVER_SECTION.
_USERS = "users"
_POLICY = "application_policy"
_CREDENTIALS = "mail_server_credentials"
_SECTION = "mail-server"
_EMAIL_PATTERN = "'^[A-Za-z0-9!#$%&''*+/=?^_`{|}~.-]{1,64}@[a-z0-9-]{1,63}(\\.[a-z0-9-]{1,63})+$'"
_USER_EMAIL_CHECK = (
    "ck_users_email_canonical",
    'email IS NULL OR (char_length(email) <= 254 AND email COLLATE "C" ~ ' + _EMAIL_PATTERN + ")",
)
# (column, type, server default or None)
_POLICY_COLUMNS = (
    ("mail_host", sa.Text(), None),
    ("mail_port", sa.Integer(), "587"),
    ("mail_security", sa.Text(), "'STARTTLS'"),
    ("mail_username", sa.Text(), None),
    ("mail_sender", sa.Text(), None),
)
# (check name, check SQL)
_POLICY_CHECKS = (
    (
        "ck_application_policy_mail_host_shape",
        "mail_host IS NULL OR (char_length(mail_host) <= 253"
        """ AND mail_host COLLATE "C" ~ '^[a-z0-9.:-]+$')""",
    ),
    ("ck_application_policy_mail_port_range", "mail_port BETWEEN 1 AND 65535"),
    (
        "ck_application_policy_mail_security_allowed",
        "mail_security IN ('STARTTLS', 'TLS', 'NONE')",
    ),
    (
        "ck_application_policy_mail_username_length",
        "mail_username IS NULL OR char_length(mail_username) BETWEEN 1 AND 254",
    ),
    (
        "ck_application_policy_mail_sender_canonical",
        "mail_sender IS NULL OR (char_length(mail_sender) <= 254"
        ' AND mail_sender COLLATE "C" ~ ' + _EMAIL_PATTERN + ")",
    ),
    (
        "ck_application_policy_mail_server_shape",
        "(mail_host IS NULL) = (mail_sender IS NULL)"
        " AND (mail_host IS NOT NULL OR mail_username IS NULL)"
        " AND (mail_username IS NULL OR mail_security <> 'NONE')",
    ),
)
_PASSWORD_CHECK = (
    "ck_mail_server_credentials_password_length",
    "char_length(password) BETWEEN 1 AND 1024",
)


def upgrade() -> None:
    op.add_column(_USERS, sa.Column("email", sa.Text(), nullable=True))
    name, sql = _USER_EMAIL_CHECK
    op.create_check_constraint(op.f(name), _USERS, sql)
    for column, column_type, default in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(
                column,
                column_type,
                nullable=default is None,
                server_default=None if default is None else sa.text(default),
            ),
        )
    for name, sql in _POLICY_CHECKS:
        op.create_check_constraint(op.f(name), _POLICY, sql)
    op.create_table(
        _CREDENTIALS,
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("password", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_mail_server_credentials_singleton")),
        sa.CheckConstraint(_PASSWORD_CHECK[1], name=op.f(_PASSWORD_CHECK[0])),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_server_credentials")),
    )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Once an address or the mail server was written, the
    # schema cannot return to one that cannot hold it.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_USERS} WHERE email IS NOT NULL)"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE mail_host IS NOT NULL"
        "              OR mail_port <> 587 OR mail_security <> 'STARTTLS')"
        f"  OR EXISTS (SELECT 1 FROM {_CREDENTIALS})"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        f"             WHERE entity_type = 'ApplicationPolicy' AND entity_id = '{_SECTION}')"
        " THEN RAISE EXCEPTION 'Email addresses or mail server configuration exist;"
        " refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_table(_CREDENTIALS)
    for name, _ in reversed(_POLICY_CHECKS):
        op.drop_constraint(op.f(name), _POLICY, type_="check")
    for column, _, _ in reversed(_POLICY_COLUMNS):
        op.drop_column(_POLICY, column)
    op.drop_constraint(op.f(_USER_EMAIL_CHECK[0]), _USERS, type_="check")
    op.drop_column(_USERS, "email")
