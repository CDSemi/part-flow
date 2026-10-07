"""Phase 13: users, roles and permissions — configuration only.

Exactly the additive schema slice 12 needs (IMPLEMENTATION_ROADMAP
Phase 13 "Users and roles / authorization management — configuration is
created here; enforcement is Phase 14"; PROJECT_PROFILE §7 User, §20,
§21; GUI_DESIGN §9; owner decisions OD-8, OD-19):

- `roles` — editable named roles (integer identity, a name unique by the
  plain `uq_roles_name`, timestamps; no active flag: roles are renamed,
  never deleted or deactivated);
- `role_permissions` — one row per grant, PK `(role_id, permission)`;
  the CHECK `ck_role_permissions_permission_known` admits exactly the
  35-key permission vocabulary (one key per PROJECT_PROFILE §20
  capability, `app.domain.enums.Permission`);
- `users` — application accounts: a unique canonical login name (trimmed,
  lowercase ASCII, checked under the "C" collation), a display name,
  exactly one role, the optional avatar on the row (the S1 avatar
  CHECKs), `theme_preference` (the User tier of GUI_DESIGN §2.1, stored
  only — no Phase 13 writer or reader, OD-19) and the active flag. No
  credential column (Phase 14 chooses the credential model). Users are
  never Workers: no foreign key links the two tables in either
  direction;
- the seed: the three PROJECT_PROFILE §20 roles Administrator, Manager
  and Operator with exactly the grants §20 states — nothing inferred;
  editable afterwards. The seed appends no audit row: the first edit's
  `before_data` is the seed;
- `ck_audit_events_entity_type` gains `User` and `Role`.

Configuration only — nothing reads these tables to allow or refuse
anything until Phase 14.

The downgrade REFUSES — it never deletes configuration or strands its
history: any `users` row or any `User` / `Role` audit row raises (every
role or grant change appends a `Role` audit row, so their absence proves
the seeded roles are untouched); `alembic/env.py` runs in one
transaction, so the database then stays at this revision. Run it only
against disposable development and test databases.

Revision ID: 0028_phase13_users_roles
Revises: 0027_phase13_retention_period
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_phase13_users_roles"
down_revision: str | None = "0027_phase13_retention_period"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its source.
_ROLES, _ROLE_PERMISSIONS, _USERS, _AUDIT = "roles", "role_permissions", "users", "audit_events"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0024 left (the downgrade restores it); the second appends
# User and Role.
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate')"
)
_USER_ROLE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role')"
)

# PROJECT_PROFILE §20, one key per capability bullet, in group order
# (== app.domain.enums.Permission).
_PERMISSIONS = (
    "MANAGE_DEPARTMENTS",
    "MANAGE_AREAS",
    "MANAGE_OPERATIONS",
    "MANAGE_WORKERS",
    "MANAGE_USERS_AND_ROLES",
    "MANAGE_SCAN_STATIONS",
    "MANAGE_BARCODE_CONFIGURATION",
    "MANAGE_SCAN_BEHAVIOR",
    "MANAGE_WORKER_SESSION_POLICIES",
    "MANAGE_CORRECTION_PERMISSIONS",
    "CONFIGURE_SYSTEM_SETTINGS",
    "MANAGE_MACHINES",
    "MANAGE_ROUTE_TEMPLATES",
    "MANAGE_PART_NUMBER_MASTER",
    "VIEW_PRODUCTION_DATA",
    "MANAGE_WORK_ORDERS",
    "EDIT_WORK_ORDER_DEMAND",
    "SET_DEMAND_PRIORITY",
    "REORDER_HOT_ITEMS",
    "ASSIGN_ROUTES",
    "RESOLVE_EXCEPTIONAL_SITUATIONS",
    "EXPORT_REPORTS",
    "SCAN_PN_BARCODES",
    "SCAN_MACHINE_BARCODES",
    "SCAN_WORKER_BARCODES",
    "RECEIVE_QUANTITY",
    "ASSIGN_QUANTITY_TO_MACHINE",
    "CONFIRM_QUANTITY",
    "COMPLETE_INTO_STOCKROOM",
    "CONFIRM_SUGGESTED_ALLOCATION",
    "ADJUST_SUGGESTED_ALLOCATION",
    "UNDO_RECENT_SCANS",
    "PERFORM_QUANTITY_CORRECTIONS",
    "EDIT_WORK_ORDER_ALLOCATION",
    "PERFORM_HISTORICAL_CORRECTIONS",
)
_PERMISSION_SQL = "permission IN (" + ", ".join(f"'{p}'" for p in _PERMISSIONS) + ")"
_LOGIN_NAME_SQL = """login_name COLLATE "C" ~ '^[a-z0-9._@+-]{1,128}$'"""
_THEME_SQL = "theme_preference IN ('DARK', 'LIGHT')"
_AVATAR_SHAPE_SQL = (
    "(avatar_image IS NULL) = (avatar_image_type IS NULL)"
    " AND (avatar_image IS NULL) = (avatar_image_updated_at IS NULL)"
)
_AVATAR_TYPE_SQL = "avatar_image_type IN ('image/png', 'image/jpeg', 'image/webp')"
_AVATAR_SIZE_SQL = "avatar_image IS NULL OR octet_length(avatar_image) BETWEEN 1 AND 2097152"

# Exactly the grants PROJECT_PROFILE §20 states (OD-8); nothing inferred.
_SEED_GRANTS = {
    "Administrator": (
        "MANAGE_DEPARTMENTS",
        "MANAGE_AREAS",
        "MANAGE_OPERATIONS",
        "MANAGE_MACHINES",
        "MANAGE_WORKERS",
        "MANAGE_USERS_AND_ROLES",
        "MANAGE_SCAN_STATIONS",
        "MANAGE_BARCODE_CONFIGURATION",
        "MANAGE_ROUTE_TEMPLATES",
        "MANAGE_PART_NUMBER_MASTER",
        "MANAGE_SCAN_BEHAVIOR",
        "MANAGE_WORKER_SESSION_POLICIES",
        "MANAGE_CORRECTION_PERMISSIONS",
        "EDIT_WORK_ORDER_DEMAND",
        "EDIT_WORK_ORDER_ALLOCATION",
        "PERFORM_HISTORICAL_CORRECTIONS",
        "CONFIGURE_SYSTEM_SETTINGS",
    ),
    "Manager": (
        "VIEW_PRODUCTION_DATA",
        "MANAGE_WORK_ORDERS",
        "EDIT_WORK_ORDER_DEMAND",
        "SET_DEMAND_PRIORITY",
        "REORDER_HOT_ITEMS",
        "ASSIGN_ROUTES",
        "PERFORM_QUANTITY_CORRECTIONS",
        "EDIT_WORK_ORDER_ALLOCATION",
        "RESOLVE_EXCEPTIONAL_SITUATIONS",
        "EXPORT_REPORTS",
    ),
    "Operator": (
        "SCAN_PN_BARCODES",
        "SCAN_MACHINE_BARCODES",
        "SCAN_WORKER_BARCODES",
        "RECEIVE_QUANTITY",
        "ASSIGN_QUANTITY_TO_MACHINE",
        "CONFIRM_QUANTITY",
        "COMPLETE_INTO_STOCKROOM",
        "CONFIRM_SUGGESTED_ALLOCATION",
        "ADJUST_SUGGESTED_ALLOCATION",
        "UNDO_RECENT_SCANS",
    ),
}


def upgrade() -> None:
    op.create_table(
        _ROLES,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_table(
        _ROLE_PERMISSIONS,
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission", name="pk_role_permissions"),
        sa.ForeignKeyConstraint(
            ["role_id"], [f"{_ROLES}.id"], name="fk_role_permissions_role_id_roles"
        ),
        sa.CheckConstraint(_PERMISSION_SQL, name=op.f("ck_role_permissions_permission_known")),
    )
    op.create_table(
        _USERS,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("login_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("avatar_image", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_image_type", sa.Text(), nullable=True),
        sa.Column("avatar_image_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("theme_preference", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("login_name", name="uq_users_login_name"),
        sa.ForeignKeyConstraint(["role_id"], [f"{_ROLES}.id"], name="fk_users_role_id_roles"),
        sa.CheckConstraint(_LOGIN_NAME_SQL, name=op.f("ck_users_login_name_canonical")),
        sa.CheckConstraint(_AVATAR_SHAPE_SQL, name=op.f("ck_users_avatar_image_shape")),
        sa.CheckConstraint(_AVATAR_TYPE_SQL, name=op.f("ck_users_avatar_image_type")),
        sa.CheckConstraint(_AVATAR_SIZE_SQL, name=op.f("ck_users_avatar_image_size")),
        sa.CheckConstraint(_THEME_SQL, name=op.f("ck_users_theme_preference")),
    )

    op.execute(
        sa.text("INSERT INTO roles (name) VALUES (:a), (:m), (:o)").bindparams(
            a="Administrator", m="Manager", o="Operator"
        )
    )
    for name, grants in _SEED_GRANTS.items():
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission)"
                " SELECT id, unnest(CAST(:grants AS text[])) FROM roles WHERE name = :name"
            ).bindparams(sa.bindparam("grants", list(grants)), name=name)
        )

    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _USER_ROLE_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Users and the role configuration history are never
    # dropped silently.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM users)"
        "  OR EXISTS (SELECT 1 FROM audit_events WHERE entity_type IN ('User', 'Role'))"
        " THEN RAISE EXCEPTION 'Users or role configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    op.drop_table(_USERS)
    op.drop_table(_ROLE_PERMISSIONS)
    op.drop_table(_ROLES)
