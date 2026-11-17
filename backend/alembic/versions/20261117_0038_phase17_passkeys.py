"""Phase 17: passkeys (WebAuthn) for sign-in and two-step sign-in.

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 8,
optional multi-factor sign-in): each User may add passkeys — WebAuthn
credentials kept by a password manager, a phone, a computer or a security
key. A passkey signs its User in alone, or answers the second step after
the password; while one exists two-step sign-in is on.

- ``user_passkeys`` — one row per passkey (identity PK, ``user_id`` FK,
  indexed): ``credential_id`` (the id the authenticator chose, unique,
  1-1023 bytes), ``credential_data`` (the attested credential data that
  verifies its assertions: public material, 1-8192 bytes), ``rp_id`` (the
  host it was made at, the WebAuthn relying party id), ``name`` (1-80
  characters, given by the User), ``sign_count`` (``>= 0``),
  ``created_at`` and ``last_used_at``.
- ``user_passkey_setups`` — a User's pending registration (PK ``user_id``,
  FK): the 32-byte WebAuthn ``challenge``, the ``rp_id`` it was asked at
  and ``created_at``.
- ``user_sign_in_challenges`` gains ``purpose`` (``SECOND_STEP``, the
  default for every existing row, or ``PASSKEY_SIGN_IN``: a sign-in with a
  passkey alone, whose ``user_id`` — now nullable — is known only once the
  passkey answers) and ``passkey_challenge`` (32 bytes) with
  ``passkey_rp_id``, the latest passkey request (both or neither; a
  ``PASSKEY_SIGN_IN`` row always has one).

The new tables are ``ORDINARY`` for the application role and outside the
maintenance role's reads (``app.infrastructure.database_privileges``), so
``migrate`` grants them from the code head.

The downgrade REFUSES while a passkey, a pending registration, a
``PASSKEY_SIGN_IN`` challenge or a passkey request exists, so no credential
or history is silently dropped; ``alembic/env.py`` runs in one transaction,
so the database then stays at this revision. Run it only against
disposable development and test databases.

Revision ID: 0038_phase17_passkeys
Revises: 0037_phase17_two_step
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038_phase17_passkeys"
down_revision: str | None = "0037_phase17_two_step"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_PASSKEYS = "user_passkeys"
_SETUPS = "user_passkey_setups"
_CHALLENGES = "user_sign_in_challenges"
_CREDENTIAL_ID_SQL = "octet_length(credential_id) BETWEEN 1 AND 1023"
_CREDENTIAL_DATA_SQL = "octet_length(credential_data) BETWEEN 1 AND 8192"
_RP_ID_SQL = "char_length(rp_id) BETWEEN 1 AND 253"
_NAME_SQL = "char_length(name) BETWEEN 1 AND 80"
_SIGN_COUNT_SQL = "sign_count >= 0"
_SETUP_CHALLENGE_SQL = "octet_length(challenge) = 32"
_PURPOSES = ("SECOND_STEP", "PASSKEY_SIGN_IN")
_PURPOSE_SQL = "purpose IN (" + ", ".join(f"'{purpose}'" for purpose in _PURPOSES) + ")"
_USER_SQL = "purpose = 'PASSKEY_SIGN_IN' OR user_id IS NOT NULL"
_PASSKEY_CHALLENGE_SQL = "passkey_challenge IS NULL OR octet_length(passkey_challenge) = 32"
_PASSKEY_RP_ID_SQL = "passkey_rp_id IS NULL OR char_length(passkey_rp_id) BETWEEN 1 AND 253"
_PASSKEY_SHAPE_SQL = (
    "(passkey_challenge IS NULL) = (passkey_rp_id IS NULL)"
    " AND (purpose <> 'PASSKEY_SIGN_IN' OR passkey_challenge IS NOT NULL)"
)
# (check name, check SQL) added to user_sign_in_challenges.
_CHALLENGE_CHECKS = (
    ("ck_user_sign_in_challenges_purpose", _PURPOSE_SQL),
    ("ck_user_sign_in_challenges_user_shape", _USER_SQL),
    ("ck_user_sign_in_challenges_passkey_challenge_length", _PASSKEY_CHALLENGE_SQL),
    ("ck_user_sign_in_challenges_passkey_rp_id_length", _PASSKEY_RP_ID_SQL),
    ("ck_user_sign_in_challenges_passkey_shape", _PASSKEY_SHAPE_SQL),
)


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        _PASSKEYS,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("credential_data", sa.LargeBinary(), nullable=False),
        sa.Column("rp_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_passkeys"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_passkeys_user_id_users"),
        sa.UniqueConstraint("credential_id", name="uq_user_passkeys_credential_id"),
        sa.CheckConstraint(_CREDENTIAL_ID_SQL, name=op.f("ck_user_passkeys_credential_id_length")),
        sa.CheckConstraint(
            _CREDENTIAL_DATA_SQL, name=op.f("ck_user_passkeys_credential_data_length")
        ),
        sa.CheckConstraint(_RP_ID_SQL, name=op.f("ck_user_passkeys_rp_id_length")),
        sa.CheckConstraint(_NAME_SQL, name=op.f("ck_user_passkeys_name_length")),
        sa.CheckConstraint(_SIGN_COUNT_SQL, name=op.f("ck_user_passkeys_sign_count_non_negative")),
    )
    op.create_index("ix_user_passkeys_user_id", _PASSKEYS, ["user_id"])
    op.create_table(
        _SETUPS,
        sa.Column("user_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("rp_id", sa.Text(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_passkey_setups"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_passkey_setups_user_id_users"
        ),
        sa.CheckConstraint(
            _SETUP_CHALLENGE_SQL, name=op.f("ck_user_passkey_setups_challenge_length")
        ),
        sa.CheckConstraint(_RP_ID_SQL, name=op.f("ck_user_passkey_setups_rp_id_length")),
    )
    op.add_column(
        _CHALLENGES,
        sa.Column("purpose", sa.Text(), server_default=sa.text("'SECOND_STEP'"), nullable=False),
    )
    op.add_column(_CHALLENGES, sa.Column("passkey_challenge", sa.LargeBinary(), nullable=True))
    op.add_column(_CHALLENGES, sa.Column("passkey_rp_id", sa.Text(), nullable=True))
    op.alter_column(_CHALLENGES, "user_id", existing_type=sa.Integer(), nullable=True)
    for name, sql in _CHALLENGE_CHECKS:
        op.create_check_constraint(op.f(name), _CHALLENGES, sql)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_PASSKEYS})"
        f"  OR EXISTS (SELECT 1 FROM {_SETUPS})"
        f"  OR EXISTS (SELECT 1 FROM {_CHALLENGES}"
        "              WHERE purpose <> 'SECOND_STEP' OR passkey_challenge IS NOT NULL)"
        " THEN RAISE EXCEPTION 'Passkey data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for name, _ in reversed(_CHALLENGE_CHECKS):
        op.drop_constraint(op.f(name), _CHALLENGES, type_="check")
    op.alter_column(_CHALLENGES, "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column(_CHALLENGES, "passkey_rp_id")
    op.drop_column(_CHALLENGES, "passkey_challenge")
    op.drop_column(_CHALLENGES, "purpose")
    op.drop_table(_SETUPS)
    op.drop_index("ix_user_passkeys_user_id", table_name=_PASSKEYS)
    op.drop_table(_PASSKEYS)
