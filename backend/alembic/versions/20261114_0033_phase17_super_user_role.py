"""Phase 17: the seeded Super User role holds every permission key.

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 3;
PROJECT_PROFILE §20): a fourth seeded role, ``Super User``, grants every
permission key. It is an ordinary role — named, editable, its grants
explicit rows like those of any other role — never an implicit superuser,
and nothing recognizes it by its name at run time.

- A role already named ``Super User`` in any letter case (an
  administrator may have made one before this release) is completed, not
  duplicated: the exactly cased name first, then the lowest id. It
  receives the keys it lacks, its ``updated_at`` moves, and one ``Role``
  ``UPDATED`` audit row records the change with no actor and the metadata
  ``{"source": "migration", "revision": <this revision>}``. A role that
  already holds every key is left as it is.
- Otherwise the role is inserted with every key. Like the 0028 seed it
  appends no audit row: the first edit's ``before_data`` is the seed.

A later revision that adds a permission key grants it, in the same
transaction, to every role that holds every other key: the Super User
role under whatever name it has by then. A role that holds every key thus
keeps holding every key, and a role an administrator has narrowed stays
narrowed. ``tests/test_phase17_schema.py`` fails at head while the seeded
role lacks a key of ``app.domain.enums.Permission``.

The downgrade changes nothing: the role and its grants are valid
configuration at the previous revision, and configuration is never
deleted.

Revision ID: 0033_phase17_super_user_role
Revises: 0032_phase14_route_adjusted
Create Date: 2026-11-14

"""

import json
from collections.abc import Iterable, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_phase17_super_user_role"
down_revision: str | None = "0032_phase14_route_adjusted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAME = "Super User"
# Every key `ck_role_permissions_permission_known` admits (0028), in its
# order (== app.domain.enums.Permission); the schema test asserts parity.
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
_AUDIT_METADATA = {"source": "migration", "revision": revision}


def upgrade() -> None:
    connection = op.get_bind()
    role = connection.execute(
        sa.text(
            "SELECT id, name FROM roles WHERE lower(name) = lower(:name)"
            " ORDER BY name = :name DESC, id LIMIT 1"
        ),
        {"name": _ROLE_NAME},
    ).one_or_none()
    if role is None:
        role_id = connection.execute(
            sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"), {"name": _ROLE_NAME}
        ).scalar_one()
        _grant(connection, role_id, _PERMISSIONS)
        return

    held = set(
        connection.execute(
            sa.text("SELECT permission FROM role_permissions WHERE role_id = :id"), {"id": role.id}
        ).scalars()
    )
    missing = [key for key in _PERMISSIONS if key not in held]
    if not missing:
        return
    _grant(connection, role.id, missing)
    connection.execute(
        sa.text("UPDATE roles SET updated_at = now() WHERE id = :id"), {"id": role.id}
    )
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data, metadata) VALUES ('UPDATED', 'Role', :entity_id, now(),"
            " CAST(:before AS jsonb), CAST(:after AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "entity_id": str(role.id),
            "before": json.dumps(_snapshot(role.name, held)),
            "after": json.dumps(_snapshot(role.name, held.union(missing))),
            "metadata": json.dumps(_AUDIT_METADATA),
        },
    )


def _grant(connection: sa.Connection, role_id: int, keys: Iterable[str]) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission)"
            " SELECT :role_id, unnest(CAST(:keys AS text[]))"
        ),
        {"role_id": role_id, "keys": list(keys)},
    )


def _snapshot(name: str, permissions: Iterable[str]) -> dict[str, object]:
    """The audited role facet (``app.application.roles.role_snapshot``)."""
    return {"name": name, "permissions": sorted(permissions)}


def downgrade() -> None:
    # The role and its grants stay: they are valid configuration at the
    # previous revision, and configuration is never deleted.
    pass
