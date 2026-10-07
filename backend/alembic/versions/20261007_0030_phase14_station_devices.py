"""Phase 14: enrolled Scan Station devices.

Exactly the additive schema slice 4 needs (IMPLEMENTATION_ROADMAP
Phase 14; owner decisions OD-P6, OD-S4-1, OD-S4-9):

- `scan_station_devices` — one row per enrollment of a station device:
  a PENDING row holds only the SHA-256 digest of its one-time enrollment
  code (valid until `enrollment_expires_at`); activation clears it and
  stores the SHA-256 digest of the device token instead (the two never
  coexist, so a used code can never match again); revocation is terminal
  (`revoked_at` with `revoked_reason` REVOKED or REPLACED — the latter
  only for an activated device replaced by the re-enrolled device naming
  it in `replaces_device_id`); `last_seen_at` is the last request with a
  valid token. Only SHA-256 digests of enrollment codes and device
  tokens are stored. The state (PENDING / ACTIVE / EXPIRED / REVOKED) is
  derived, never stored;
- `application_policy.scan_station_role_id` (NOT NULL, FK → `roles`) —
  the role whose permissions every enrolled station device has. The
  role applied at Scan Stations is resolved by name only by this
  migration, once, to the seeded `Operator` role; runtime code reads the
  id only. The upgrade REFUSES when no role is named `Operator`;
- the `ScanStationDevice` audit entity type (enrollment, activation,
  replacement and revocation). Metadata-only on the append-only table;
  the trigger is untouched.

A device token authenticates a terminal for one Scan Station, never a
person; Workers are never Users and a badge never authorizes.

The downgrade REFUSES — it never deletes devices or their history: any
device row or any `ScanStationDevice` audit row raises; `alembic/env.py`
runs in one transaction, so the database then stays at this revision.
The pointer carries no data of its own beyond the set-once value. Run
it only against disposable development and test databases.

Revision ID: 0030_phase14_station_devices
Revises: 0029_phase14_sign_in
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_phase14_station_devices"
down_revision: str | None = "0029_phase14_sign_in"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant and
# _REVOKED_REASONS equals StationDeviceRevokedReason.
_DEVICES, _POLICY, _AUDIT = "scan_station_devices", "application_policy", "audit_events"
_DIGEST_LEN_SQL = "octet_length({col}) = 32"
_CODE_OR_TOKEN_SQL = "(enrollment_code_digest IS NULL) <> (token_digest IS NULL)"
_ACTIVATION_SHAPE_SQL = "(token_digest IS NULL) = (activated_at IS NULL)"
_REVOCATION_SHAPE_SQL = "(revoked_at IS NULL) = (revoked_reason IS NULL)"
_REVOKED_REASONS = ("REVOKED", "REPLACED")
_REVOKED_REASON_SQL = "revoked_reason IN ('REVOKED', 'REPLACED')"
_REPLACED_SHAPE_SQL = "revoked_reason IS DISTINCT FROM 'REPLACED' OR activated_at IS NOT NULL"
_LAST_SEEN_SHAPE_SQL = "last_seen_at IS NULL OR activated_at IS NOT NULL"
_NO_SELF_REPLACE_SQL = "replaces_device_id IS NULL OR replaces_device_id <> id"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0028 left (0029 did not touch it; the downgrade restores it).
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role')"
)
_DEVICE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice')"
)
# Migration-time lookup only (OD-P8/P9: never read by runtime code).
_STATION_ROLE_NAME = "Operator"
_STATION_ROLE_COLUMN = "scan_station_role_id"
_STATION_ROLE_FK = "fk_application_policy_scan_station_role_id_roles"
_NO_STATION_ROLE_MESSAGE = (
    "No role is named Operator. Rename the role whose permissions Scan Stations should use"
    " to Operator, run the upgrade, then rename it back."
)


def upgrade() -> None:
    op.create_table(
        _DEVICES,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("enrollment_code_digest", sa.LargeBinary(), nullable=True),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=True),
        sa.Column("replaces_device_id", sa.Integer(), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_scan_station_devices"),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["scan_stations.station_id"],
            name="fk_scan_station_devices_station_id_scan_stations",
        ),
        sa.ForeignKeyConstraint(
            ["replaces_device_id"],
            ["scan_station_devices.id"],
            name="fk_scan_station_devices_replaces_device_id_scan_station_devices",
        ),
        sa.UniqueConstraint(
            "enrollment_code_digest", name="uq_scan_station_devices_enrollment_code_digest"
        ),
        sa.UniqueConstraint("token_digest", name="uq_scan_station_devices_token_digest"),
        sa.CheckConstraint(
            _DIGEST_LEN_SQL.format(col="enrollment_code_digest"),
            name=op.f("ck_scan_station_devices_enrollment_code_digest_length"),
        ),
        sa.CheckConstraint(
            _DIGEST_LEN_SQL.format(col="token_digest"),
            name=op.f("ck_scan_station_devices_token_digest_length"),
        ),
        sa.CheckConstraint(_CODE_OR_TOKEN_SQL, name=op.f("ck_scan_station_devices_code_or_token")),
        sa.CheckConstraint(
            _ACTIVATION_SHAPE_SQL, name=op.f("ck_scan_station_devices_activation_shape")
        ),
        sa.CheckConstraint(
            _REVOCATION_SHAPE_SQL, name=op.f("ck_scan_station_devices_revocation_shape")
        ),
        sa.CheckConstraint(
            _REVOKED_REASON_SQL, name=op.f("ck_scan_station_devices_revoked_reason")
        ),
        sa.CheckConstraint(
            _REPLACED_SHAPE_SQL, name=op.f("ck_scan_station_devices_replaced_shape")
        ),
        sa.CheckConstraint(
            _LAST_SEEN_SHAPE_SQL, name=op.f("ck_scan_station_devices_last_seen_shape")
        ),
        sa.CheckConstraint(
            _NO_SELF_REPLACE_SQL, name=op.f("ck_scan_station_devices_no_self_replace")
        ),
    )

    # The role applied at Scan Stations: the seeded Operator role, by name,
    # once (the slice 12 seed precedent); runtime reads the id only.
    op.add_column(_POLICY, sa.Column(_STATION_ROLE_COLUMN, sa.Integer(), nullable=True))
    op.create_foreign_key(_STATION_ROLE_FK, _POLICY, "roles", [_STATION_ROLE_COLUMN], ["id"])
    op.execute(
        sa.text(
            f"UPDATE {_POLICY} SET {_STATION_ROLE_COLUMN} ="
            " (SELECT id FROM roles WHERE name = :name)"
        ).bindparams(name=_STATION_ROLE_NAME)
    )
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_POLICY} WHERE {_STATION_ROLE_COLUMN} IS NULL)"
        f" THEN RAISE EXCEPTION '{_NO_STATION_ROLE_MESSAGE}';"
        " END IF; END $$;"
    )
    op.alter_column(_POLICY, _STATION_ROLE_COLUMN, nullable=False)

    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _DEVICE_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Devices and their history are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_DEVICES})"
        f"  OR EXISTS (SELECT 1 FROM {_AUDIT} WHERE entity_type = 'ScanStationDevice')"
        " THEN RAISE EXCEPTION 'Scan Station device data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    op.drop_constraint(_STATION_ROLE_FK, _POLICY, type_="foreignkey")
    op.drop_column(_POLICY, _STATION_ROLE_COLUMN)
    op.drop_table(_DEVICES)
