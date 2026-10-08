"""PartFlow database roles, grants and guard integrity (Phase 16 slice 4; CD5, OD-16-08/09).

"Role" here always means a PostgreSQL database role, never an application
Role. Three database roles exist on the production stack: the cluster
bootstrap superuser (``POSTGRES_USER``, owns every object, runs
``migrate``, ``provision-roles`` and ``apply-grants``), ``partflow_app``
(the backend) and ``partflow_maintenance`` (no service until P16-S8).

- ``provision_roles`` creates or repairs the two LOGIN roles and sets their
  passwords as SCRAM verifiers (the plaintext never reaches SQL text).
- ``apply_grants`` derives every table, sequence, schema, database and
  default privilege of the two roles and PUBLIC from
  ``app.infrastructure.database_privileges``; it runs inside every
  ``migrate`` transaction and on demand (after a restore). Privileges of
  database roles PartFlow does not manage are reported, never revoked.
- ``guard_integrity`` is reconcile check (h): guard triggers present and
  enabled, guard-function source hashes, privileges, role attributes and
  memberships, PUBLIC and default privileges, ``session_replication_role``.
  A superuser who disables a trigger (or sets ``session_replication_role``
  in its own session) and restores it is outside detection (OD-16-09).

Every function works on a caller-owned connection inside the caller's
transaction and never commits; a refusal raises ``DatabaseRolesRefusal``
and the caller rolls back.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Final, NamedTuple

import psycopg.errors
from psycopg import sql
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database_privileges import (
    APP_PRIVILEGES,
    GUARD_FUNCTION_SHA256,
    GUARD_TRIGGERS,
    MAINTENANCE_SELECT,
    TABLE_CLASSES,
)

APP_ROLE: Final = "partflow_app"
MAINTENANCE_ROLE: Final = "partflow_maintenance"


class DatabaseRoles(NamedTuple):
    app: str
    maintenance: str


PRODUCTION_ROLES: Final = DatabaseRoles(APP_ROLE, MAINTENANCE_ROLE)


def configured_roles() -> DatabaseRoles:
    """The role names, read at call time (tests replace ``APP_ROLE``/``MAINTENANCE_ROLE``)."""
    return DatabaseRoles(APP_ROLE, MAINTENANCE_ROLE)


class DatabaseRolesRefusal(Exception):
    """A defined refusal; nothing may be committed after it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GuardFinding(NamedTuple):
    code: str
    entity_type: str
    entity_id: str
    expected: object
    actual: object
    detail: dict[str, object]


class GuardIntegrity(NamedTuple):
    not_applicable_reason: str | None
    findings: list[GuardFinding]
    examined: dict[str, int]


_RELATION_KINDS: Final = ("r", "p", "v", "m", "f")
_PUBLIC: Final = "PUBLIC"
_FORBIDDEN_ATTRIBUTES: Final = (
    ("superuser", "SUPERUSER"),
    ("createdb", "CREATEDB"),
    ("createrole", "CREATEROLE"),
    ("replication", "REPLICATION"),
    ("bypassrls", "BYPASSRLS"),
)
_SAFE_ATTRIBUTES: Final = sql.SQL(
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
    " CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
)
#: ``ROLE_ATTRIBUTE``'s expected map (check (h)).
EXPECTED_ROLE_ATTRIBUTES: Final[Mapping[str, object]] = {
    "login": True,
    "superuser": False,
    "createdb": False,
    "createrole": False,
    "replication": False,
    "bypassrls": False,
    "connection_limit": -1,
    "valid_until": "infinity",
}
_OWNED_OBJECTS_LISTED: Final = 20
_PROVISION_LOCK: Final = "partflow:provision-roles"

NOT_PROVISIONED_DETAIL: Final = (
    "The database roles {app} and {maintenance} do not exist; grants were not applied"
    " (development or test database)."
)
NOT_APPLICABLE_REASON: Final = (
    "The database roles {app} and {maintenance} do not exist here (a development or test"
    " database with one owner role). Database-role hardening applies to the production stack."
)

#: The ``apply-grants`` copy (also a ``migrate`` refusal's message, §4.5).
GRANT_MESSAGES: Final[Mapping[str, str]] = {
    "not_superuser": (
        "apply-grants must run as the database owner role (POSTGRES_USER); the connected"
        " database role {role} is not a superuser. Nothing was changed."
    ),
    "roles_not_provisioned": (
        "The database roles {app} and {maintenance} do not exist. Run provision-roles first."
        " Nothing was changed."
    ),
    "roles_incomplete": (
        "The database role {missing} does not exist ({present} does). Run provision-roles."
        " Nothing was changed."
    ),
    "role_unsafe": (
        "The database role {role} {reason}. Run provision-roles to restore its safe"
        " attributes, then try again. Nothing was changed."
    ),
    "role_owns_objects": (
        "The database role {role} owns {n} database objects (for example {object}). Give"
        " them back to the owner role (REASSIGN OWNED BY {role} TO {owner}) after review,"
        " then run apply-grants (or the release) again. Nothing was changed."
    ),
    "foreign_grantor": (
        "The database role {grantee} holds {privileges} on {relation}, granted by {grantor},"
        " a database role PartFlow does not manage. Review it and revoke it as that role"
        " (SET ROLE {grantor}; REVOKE ALL ON {relation} FROM {grantee} CASCADE), then run"
        " apply-grants (or the release) again. Nothing was changed."
    ),
    "table_unclassified": (
        "Table {names} has no privilege class in this release, so it cannot be granted."
        " Nothing was changed."
    ),
    "table_missing": (
        "Table {names} is classified but does not exist in the database. Nothing was changed."
    ),
}

#: The ``provision-roles`` refusal copy.
PROVISION_MESSAGES: Final[Mapping[str, str]] = {
    "provision_running": (
        "Another provision-roles is running on this database. Nothing was changed."
    ),
    "not_superuser": (
        "provision-roles must run as the database owner role (POSTGRES_USER); the connected"
        " database role {role} is not a superuser. Nothing was changed."
    ),
    "role_name_conflict": (
        "The database owner role is named {role}, which is reserved for PartFlow. Use another"
        " POSTGRES_USER for this installation. Nothing was changed."
    ),
    "role_owns_objects": (
        "The database role {role} owns {n} database objects (for example {object}). Give"
        " them back to the owner role (REASSIGN OWNED BY {role} TO {owner}) after review,"
        " then run provision-roles again. Nothing was changed."
    ),
    "concurrent_change": (
        "Another session changed the database roles at the same time. Nothing was changed."
        " Run provision-roles again."
    ),
}


def _grant_refusal(code: str, **values: object) -> DatabaseRolesRefusal:
    return DatabaseRolesRefusal(code, GRANT_MESSAGES[code].format(**values))


def _provision_refusal(code: str, **values: object) -> DatabaseRolesRefusal:
    return DatabaseRolesRefusal(code, PROVISION_MESSAGES[code].format(**values))


# ---------------------------------------------------------------------------
# Catalog readers (plain SELECTs: any connected role may run them, F7)
# ---------------------------------------------------------------------------


class _Role(NamedTuple):
    oid: int
    name: str
    superuser: bool
    createdb: bool
    createrole: bool
    replication: bool
    bypassrls: bool
    login: bool
    inherit: bool
    connection_limit: int
    valid_until: str

    def attributes(self) -> dict[str, object]:
        return {
            "login": self.login,
            "superuser": self.superuser,
            "createdb": self.createdb,
            "createrole": self.createrole,
            "replication": self.replication,
            "bypassrls": self.bypassrls,
            "connection_limit": self.connection_limit,
            "valid_until": self.valid_until,
        }


class _Membership(NamedTuple):
    granted: str
    grantor: str


def _read_role(connection: Connection, name: str) -> _Role | None:
    # A NULL or 'infinity' expiry reads "infinity"; a finite one as ISO-8601 UTC.
    row = connection.execute(
        text(
            "SELECT oid, rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication,"
            " rolbypassrls, rolcanlogin, rolinherit, rolconnlimit,"
            " CASE WHEN rolvaliduntil IS NULL OR rolvaliduntil = 'infinity' THEN 'infinity'"
            " ELSE to_char(rolvaliduntil AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
            " END FROM pg_roles WHERE rolname = :name"
        ),
        {"name": name},
    ).one_or_none()
    return None if row is None else _Role(*row)


def _memberships(connection: Connection, role: _Role) -> list[_Membership]:
    rows = connection.execute(
        text(
            "SELECT g.rolname, r.rolname FROM pg_auth_members m"
            " JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.grantor"
            " WHERE m.member = :oid ORDER BY 1, 2"
        ),
        {"oid": role.oid},
    )
    return [_Membership(str(granted), str(grantor)) for granted, grantor in rows]


def _owned_objects(connection: Connection, role: _Role) -> list[str]:
    """The one ownership reader: objects of this database and shared objects."""
    rows = connection.execute(
        text(
            "SELECT pg_describe_object(classid, objid, objsubid) FROM pg_shdepend"
            " WHERE deptype = 'o' AND refclassid = 'pg_authid'::regclass AND refobjid = :oid"
            " AND dbid IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"
            " ORDER BY 1"
        ),
        {"oid": role.oid},
    ).scalars()
    return [str(name) for name in rows]


def _role_settings(connection: Connection, role: _Role) -> list[str | None]:
    """The databases (None = every database) holding per-role settings of ``role``."""
    rows = connection.execute(
        text(
            "SELECT d.datname FROM pg_db_role_setting s"
            " LEFT JOIN pg_database d ON d.oid = s.setdatabase"
            " WHERE s.setrole = :oid ORDER BY s.setdatabase"
        ),
        {"oid": role.oid},
    ).scalars()
    return [None if name is None else str(name) for name in rows]


def _current_user(connection: Connection) -> tuple[str, bool]:
    row = connection.execute(
        text("SELECT rolname, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    return str(row[0]), bool(row[1])


def _relations(connection: Connection) -> dict[str, str]:
    """Relations (kinds r/p/v/m/f) and sequences of schema ``public``: name -> relkind."""
    rows = connection.execute(
        text(
            "SELECT c.relname, c.relkind FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')"
        )
    )
    return {str(name): str(kind) for name, kind in rows}


class _AclEntry(NamedTuple):
    relation: str
    relkind: str
    owner: str
    grantee: str
    grantor: str
    privilege: str
    grantable: bool


class _ColumnEntry(NamedTuple):
    relation: str
    column: str
    owner: str
    grantee: str
    grantor: str
    privilege: str


class _DefaultEntry(NamedTuple):
    role: str
    in_schema: bool
    objtype: str
    grantee: str
    privilege: str
    grantable: bool


class _Acls(NamedTuple):
    relations: dict[str, str]
    entries: list[_AclEntry]
    columns: list[_ColumnEntry]
    schema_create: list[str]
    database: str
    database_create: list[str]
    defaults: list[_DefaultEntry]


_GRANTEE: Final = "CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END"
_GRANTOR: Final = "CASE WHEN a.grantor = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantor) END"


def _read_acls(connection: Connection) -> _Acls:
    """Every ACL entry (h) group P and S compare, from the snapshot (``aclexplode``)."""
    entries = [
        _AclEntry(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5]), bool(r[6]))
        for r in connection.execute(
            text(
                f"SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner), {_GRANTEE},"
                f" {_GRANTOR}, a.privilege_type, a.is_grantable FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault("
                "(CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END)::\"char\", c.relowner))) a"
                " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')"
            )
        )
    ]
    columns = [
        _ColumnEntry(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5]))
        for r in connection.execute(
            text(
                f"SELECT c.relname, att.attname, pg_get_userbyid(c.relowner), {_GRANTEE},"
                f" {_GRANTOR}, a.privilege_type FROM pg_attribute att"
                " JOIN pg_class c ON c.oid = att.attrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " CROSS JOIN LATERAL aclexplode(att.attacl) a"
                " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
                " AND att.attacl IS NOT NULL AND att.attnum > 0 AND NOT att.attisdropped"
            )
        )
    ]
    schema_create = [
        str(grantee)
        for grantee in connection.execute(
            text(
                f"SELECT {_GRANTEE} FROM pg_namespace n CROSS JOIN LATERAL"
                " aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a"
                " WHERE n.nspname = 'public' AND a.privilege_type = 'CREATE'"
                " AND a.grantee <> n.nspowner"
            )
        ).scalars()
    ]
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    database_create = [
        str(grantee)
        for grantee in connection.execute(
            text(
                f"SELECT {_GRANTEE} FROM pg_database d CROSS JOIN LATERAL"
                " aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a"
                " WHERE d.datname = current_database() AND a.privilege_type = 'CREATE'"
                " AND a.grantee <> d.datdba"
            )
        ).scalars()
    ]
    defaults = [
        _DefaultEntry(str(r[0]), bool(r[1]), str(r[2]), str(r[3]), str(r[4]), bool(r[5]))
        for r in connection.execute(
            text(
                f"SELECT pg_get_userbyid(d.defaclrole), d.defaclnamespace <> 0,"
                f" d.defaclobjtype, {_GRANTEE}, a.privilege_type, a.is_grantable"
                " FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a"
                " WHERE d.defaclobjtype IN ('r', 'S') AND (d.defaclnamespace = 0"
                " OR d.defaclnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public'))"
                " AND a.grantee <> d.defaclrole"
            )
        )
    ]
    return _Acls(
        _relations(connection),
        entries,
        columns,
        schema_create,
        database,
        database_create,
        defaults,
    )


# ---------------------------------------------------------------------------
# Findings (check (h); the managed part also verifies apply_grants)
# ---------------------------------------------------------------------------


def _privilege_text(privilege: str, grantable: bool) -> str:
    return f"{privilege}*" if grantable else privilege


def _entity_type(relkind: str) -> str:
    return "Sequence" if relkind == "S" else "Table"


def _expected_privileges(
    relation: str, relkind: str, grantee: str, roles: DatabaseRoles
) -> set[str]:
    if relkind == "S" or relation not in TABLE_CLASSES:
        return set()
    if grantee == roles.app:
        return set(APP_PRIVILEGES[TABLE_CLASSES[relation]])
    if grantee == roles.maintenance and relation in MAINTENANCE_SELECT:
        return {"SELECT"}
    return set()


def _relation_findings(acls: _Acls) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    for name, kind in sorted(acls.relations.items()):
        if kind != "S" and name not in TABLE_CLASSES:
            findings.append(
                GuardFinding(
                    "TABLE_UNCLASSIFIED", "Table", name, None, "present", {"relkind": kind}
                )
            )
    for name in sorted(set(TABLE_CLASSES) - set(acls.relations)):
        findings.append(
            GuardFinding("TABLE_MISSING", "Table", name, "present", None, {"relkind": None})
        )
    return findings


def _privilege_findings(
    acls: _Acls, roles: DatabaseRoles, present: Iterable[str], *, managed_only: bool
) -> list[GuardFinding]:
    """Groups P and S. ``managed_only``: PUBLIC and the two roles only (apply_grants)."""
    managed = {_PUBLIC, roles.app, roles.maintenance}
    findings: list[GuardFinding] = []
    grouped: dict[tuple[str, str], list[_AclEntry]] = defaultdict(list)
    for entry in acls.entries:
        if entry.grantee != entry.owner:
            grouped[(entry.relation, entry.grantee)].append(entry)
    for (relation, grantee), entries in sorted(grouped.items()):
        if managed_only and grantee not in managed:
            continue
        relkind = entries[0].relkind
        actual = sorted(_privilege_text(e.privilege, e.grantable) for e in entries)
        if grantee == _PUBLIC:
            findings.append(
                GuardFinding(
                    "PUBLIC_PRIVILEGE", _entity_type(relkind), f"{relation}/PUBLIC", [], actual, {}
                )
            )
            continue
        expected = _expected_privileges(relation, relkind, grantee, roles)
        held = {e.privilege for e in entries}
        if (
            held - expected
            or any(e.grantable for e in entries)
            or any(e.grantor != e.owner for e in entries)
        ):
            findings.append(
                GuardFinding(
                    "PRIVILEGE_EXCESS",
                    _entity_type(relkind),
                    f"{relation}/{grantee}",
                    sorted(expected),
                    actual,
                    {"grantee": grantee, "grantors": sorted({e.grantor for e in entries})},
                )
            )
    for relation, relkind in sorted(acls.relations.items()):
        if relkind == "S" or relation not in TABLE_CLASSES:
            continue
        for grantee in present:
            expected = _expected_privileges(relation, relkind, grantee, roles)
            held_entries = grouped.get((relation, grantee), [])
            held = {e.privilege for e in held_entries}
            if expected - held:
                findings.append(
                    GuardFinding(
                        "PRIVILEGE_MISSING",
                        "Table",
                        f"{relation}/{grantee}",
                        sorted(expected),
                        sorted(held),
                        {"grantee": grantee},
                    )
                )
    columns: dict[tuple[str, str], set[str]] = defaultdict(set)
    for column in acls.columns:
        if column.grantee != column.owner and (not managed_only or column.grantee in managed):
            columns[(column.relation, column.grantee)].add(f"{column.privilege}({column.column})")
    for (relation, grantee), privileges in sorted(columns.items()):
        findings.append(
            GuardFinding(
                "COLUMN_PRIVILEGE",
                "Table",
                f"{relation}/{grantee}",
                [],
                sorted(privileges),
                {"grantee": grantee},
            )
        )
    for grantee in sorted(set(acls.schema_create)):
        if not managed_only or grantee in managed:
            findings.append(
                GuardFinding("SCHEMA_PRIVILEGE", "Schema", f"public/{grantee}", [], ["CREATE"], {})
            )
    for grantee in sorted(set(acls.database_create)):
        if not managed_only or grantee in managed:
            findings.append(
                GuardFinding(
                    "DATABASE_PRIVILEGE",
                    "Database",
                    f"{acls.database}/{grantee}",
                    [],
                    ["CREATE"],
                    {},
                )
            )
    defaults: dict[str, list[str]] = defaultdict(list)
    for default in acls.defaults:
        if managed_only and default.grantee not in managed:
            continue
        objects = "TABLES" if default.objtype == "r" else "SEQUENCES"
        schema = "public" if default.in_schema else "*"
        key = f"{default.role}/{schema}/{objects}/{default.grantee}"
        defaults[key].append(_privilege_text(default.privilege, default.grantable))
    for key, listed in sorted(defaults.items()):
        findings.append(
            GuardFinding("DEFAULT_PRIVILEGE", "DefaultPrivilege", key, [], sorted(listed), {})
        )
    return findings


def _foreign_grantor(acls: _Acls, roles: DatabaseRoles) -> DatabaseRolesRefusal | None:
    """A managed grantee's privilege granted by a role PartFlow does not manage."""
    managed = {_PUBLIC, roles.app, roles.maintenance}
    granted: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for entry in acls.entries:
        if entry.grantee in managed and entry.grantor != entry.owner:
            granted[(entry.relation, entry.grantee, entry.grantor)].add(entry.privilege)
    for column in acls.columns:
        if column.grantee in managed and column.grantor != column.owner:
            granted[(column.relation, column.grantee, column.grantor)].add(
                f"{column.privilege}({column.column})"
            )
    if not granted:
        return None
    (relation, grantee, grantor), privileges = min(granted.items())
    return _grant_refusal(
        "foreign_grantor",
        grantee=grantee,
        privileges=", ".join(sorted(privileges)),
        relation=relation,
        grantor=grantor,
    )


def _foreign_grantees(acls: _Acls, roles: DatabaseRoles) -> list[str]:
    managed = {_PUBLIC, roles.app, roles.maintenance}
    return sorted(
        {
            f"{entry.relation}/{entry.grantee}"
            for entry in acls.entries
            if entry.grantee != entry.owner and entry.grantee not in managed
        }
    )


def _role_findings(connection: Connection, role: _Role) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    attributes = role.attributes()
    if attributes != dict(EXPECTED_ROLE_ATTRIBUTES):
        findings.append(
            GuardFinding(
                "ROLE_ATTRIBUTE",
                "DatabaseRole",
                role.name,
                dict(EXPECTED_ROLE_ATTRIBUTES),
                attributes,
                {},
            )
        )
    granted = sorted({membership.granted for membership in _memberships(connection, role)})
    if granted:
        findings.append(GuardFinding("ROLE_MEMBERSHIP", "DatabaseRole", role.name, [], granted, {}))
    owned = _owned_objects(connection, role)
    if owned:
        findings.append(
            GuardFinding(
                "ROLE_OWNS_OBJECTS",
                "DatabaseRole",
                role.name,
                0,
                len(owned),
                {"objects": owned[:_OWNED_OBJECTS_LISTED]},
            )
        )
    return findings


def _trigger_findings(connection: Connection) -> list[GuardFinding]:
    """Group T; the caller pins ``search_path`` (``pg_get_triggerdef`` qualifies by it)."""
    rows = connection.execute(
        text(
            "SELECT t.tgname, pg_get_triggerdef(t.oid), t.tgenabled FROM pg_trigger t"
            " JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE NOT t.tgisinternal AND n.nspname = 'public'"
        )
    )
    actual = {str(name): (str(definition), str(enabled)) for name, definition, enabled in rows}
    findings: list[GuardFinding] = []
    for name, guard in sorted(GUARD_TRIGGERS.items()):
        detail: dict[str, object] = {"table": guard.table}
        if name not in actual:
            findings.append(
                GuardFinding("TRIGGER_MISSING", "Trigger", name, guard.definition, None, detail)
            )
            continue
        definition, enabled = actual[name]
        if definition != guard.definition:
            findings.append(
                GuardFinding(
                    "TRIGGER_CHANGED", "Trigger", name, guard.definition, definition, detail
                )
            )
        if enabled in ("D", "R"):
            findings.append(GuardFinding("TRIGGER_DISABLED", "Trigger", name, "O", enabled, detail))
        elif enabled == "A":
            findings.append(
                GuardFinding("TRIGGER_ENABLE_MODE", "Trigger", name, "O", enabled, detail)
            )
    sources = {
        str(name): str(source)
        for name, source in connection.execute(
            text(
                "SELECT p.proname, p.prosrc FROM pg_proc p"
                " JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = 'public' AND p.pronargs = 0 AND p.proname = ANY(:names)"
            ),
            {"names": sorted(GUARD_FUNCTION_SHA256)},
        )
    }
    for name, expected in sorted(GUARD_FUNCTION_SHA256.items()):
        if name not in sources:
            findings.append(
                GuardFinding("GUARD_FUNCTION_MISSING", "Function", name, expected, None, {})
            )
            continue
        digest = hashlib.sha256(sources[name].encode("utf-8")).hexdigest()
        if digest != expected:
            findings.append(
                GuardFinding("GUARD_FUNCTION_CHANGED", "Function", name, expected, digest, {})
            )
    return findings


def _replication_findings(connection: Connection, roles: DatabaseRoles) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    active, connected = connection.execute(
        text("SELECT current_setting('session_replication_role'), current_user")
    ).one()
    if active != "origin":
        findings.append(
            GuardFinding(
                "REPLICATION_ROLE_ACTIVE",
                "Setting",
                "session_replication_role",
                "origin",
                str(active),
                {"connected_role": str(connected)},
            )
        )
    rows = connection.execute(
        text(
            "SELECT d.datname, r.rolname, e.entry FROM pg_db_role_setting s"
            " LEFT JOIN pg_database d ON d.oid = s.setdatabase"
            " LEFT JOIN pg_roles r ON r.oid = s.setrole"
            " CROSS JOIN LATERAL unnest(s.setconfig) AS e(entry)"
            " WHERE (s.setdatabase = 0 OR d.datname = current_database())"
            " AND (s.setrole = 0 OR r.rolname IN (:app, :maintenance))"
            " AND e.entry LIKE 'session_replication_role=%'"
            " ORDER BY 1 NULLS FIRST, 2 NULLS FIRST"
        ),
        {"app": roles.app, "maintenance": roles.maintenance},
    )
    for database, role, entry in rows:
        findings.append(
            GuardFinding(
                "REPLICATION_ROLE_SETTING",
                "Setting",
                f"{database or '*'}/{role or '*'}",
                None,
                str(entry),
                {},
            )
        )
    return findings


def guard_integrity(
    connection: Connection, *, roles: DatabaseRoles = PRODUCTION_ROLES, required: bool
) -> GuardIntegrity:
    """Reconcile check (h): reads catalogs only, never writes, never repairs."""
    app = _read_role(connection, roles.app)
    maintenance = _read_role(connection, roles.maintenance)
    examined = {
        "roles": 2,
        "tables": 0,
        "sequences": 0,
        "triggers": len(GUARD_TRIGGERS),
        "guard_functions": len(GUARD_FUNCTION_SHA256),
    }
    if app is None and maintenance is None and not required:
        reason = NOT_APPLICABLE_REASON.format(app=roles.app, maintenance=roles.maintenance)
        return GuardIntegrity(reason, [], examined)
    findings: list[GuardFinding] = []
    present: list[str] = []
    for name, role in ((roles.app, app), (roles.maintenance, maintenance)):
        if role is None:
            findings.append(GuardFinding("ROLE_MISSING", "DatabaseRole", name, "exists", None, {}))
        else:
            present.append(name)
            findings.extend(_role_findings(connection, role))
    # pg_get_triggerdef names the function by the search_path: pinned for
    # the trigger reader only, then the transaction's own path is restored
    # (a failed read rolls the savepoint and the setting back).
    previous = connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    connection.execute(text("SELECT set_config('search_path', 'pg_catalog, public', true)"))
    findings.extend(_trigger_findings(connection))
    connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": previous})
    acls = _read_acls(connection)
    findings.extend(_relation_findings(acls))
    findings.extend(_privilege_findings(acls, roles, present, managed_only=False))
    findings.extend(_replication_findings(connection, roles))
    examined["tables"] = sum(1 for name in acls.relations if name in TABLE_CLASSES)
    examined["sequences"] = sum(1 for kind in acls.relations.values() if kind == "S")
    return GuardIntegrity(None, findings, examined)


# ---------------------------------------------------------------------------
# Statements (identifiers quoted by psycopg, never formatted by hand)
# ---------------------------------------------------------------------------


def _execute(connection: Connection, statement: sql.Composable) -> None:
    raw: Any = connection.connection.driver_connection
    # psycopg parses "%" placeholders even without parameters: escape them.
    connection.exec_driver_sql(statement.as_string(raw).replace("%", "%%"))


def _table(name: str) -> sql.Composable:
    return sql.Identifier("public", name)


def _privilege_list(privileges: Iterable[str]) -> sql.Composable:
    return sql.SQL(", ").join(sql.SQL(privilege) for privilege in sorted(privileges))


# ---------------------------------------------------------------------------
# apply-grants
# ---------------------------------------------------------------------------


def _require_superuser(connection: Connection, messages: Mapping[str, str]) -> str:
    user, superuser = _current_user(connection)
    if not superuser:
        raise DatabaseRolesRefusal("not_superuser", messages["not_superuser"].format(role=user))
    return user


def _check_role_safe(connection: Connection, role: _Role, owner: str) -> None:
    for attribute, keyword in _FORBIDDEN_ATTRIBUTES:
        if getattr(role, attribute):
            raise _grant_refusal(
                "role_unsafe", role=role.name, reason=f"has the {keyword} attribute"
            )
    granted = sorted({membership.granted for membership in _memberships(connection, role)})
    if granted:
        raise _grant_refusal(
            "role_unsafe", role=role.name, reason=f"is a member of {', '.join(granted)}"
        )
    owned = _owned_objects(connection, role)
    if owned:
        raise _grant_refusal(
            "role_owns_objects", role=role.name, n=len(owned), object=owned[0], owner=owner
        )


def apply_grants(
    connection: Connection, *, roles: DatabaseRoles = PRODUCTION_ROLES, required: bool
) -> dict[str, object]:
    """Derive every privilege of the two roles and PUBLIC from the code head (§3.3).

    Idempotent; runs in the caller's transaction (the ``migrate``
    transaction, or the standalone command's own) and never commits.
    """
    app = _read_role(connection, roles.app)
    maintenance = _read_role(connection, roles.maintenance)
    if app is None and maintenance is None:
        if not required:
            detail = NOT_PROVISIONED_DETAIL.format(app=roles.app, maintenance=roles.maintenance)
            return {"status": "not_provisioned", "detail": detail}
        raise _grant_refusal("roles_not_provisioned", app=roles.app, maintenance=roles.maintenance)
    if app is None or maintenance is None:
        missing, present = (
            (roles.app, roles.maintenance) if app is None else (roles.maintenance, roles.app)
        )
        raise _grant_refusal("roles_incomplete", missing=missing, present=present)
    owner = _require_superuser(connection, GRANT_MESSAGES)
    for role in (app, maintenance):
        _check_role_safe(connection, role, owner)
    relations = _relations(connection)
    unclassified = sorted(
        name for name, kind in relations.items() if kind != "S" and name not in TABLE_CLASSES
    )
    if unclassified:
        raise _grant_refusal("table_unclassified", names=", ".join(unclassified))
    missing_tables = sorted(set(TABLE_CLASSES) - set(relations))
    if missing_tables:
        raise _grant_refusal("table_missing", names=", ".join(missing_tables))

    app_role = sql.Identifier(roles.app)
    maintenance_role = sql.Identifier(roles.maintenance)
    managed = sql.SQL("PUBLIC, {}, {}").format(app_role, maintenance_role)
    for table, table_class in sorted(TABLE_CLASSES.items()):
        _execute(
            connection,
            sql.SQL("REVOKE ALL ON TABLE {} FROM {} CASCADE").format(_table(table), managed),
        )
        _execute(
            connection,
            sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                _privilege_list(APP_PRIVILEGES[table_class]), _table(table), app_role
            ),
        )
        if table in MAINTENANCE_SELECT:
            _execute(
                connection,
                sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(_table(table), maintenance_role),
            )
    _execute(
        connection,
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {} CASCADE").format(managed),
    )
    _execute(connection, sql.SQL("REVOKE CREATE ON SCHEMA public FROM {} CASCADE").format(managed))
    _execute(
        connection,
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(app_role, maintenance_role),
    )
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    _execute(
        connection,
        sql.SQL("REVOKE CREATE ON DATABASE {} FROM {} CASCADE").format(
            sql.Identifier(database), managed
        ),
    )
    removed = _remove_default_privileges(connection, roles)

    acls = _read_acls(connection)
    refusal = _foreign_grantor(acls, roles)
    if refusal is not None:
        raise refusal
    findings = _privilege_findings(
        acls, roles, (roles.app, roles.maintenance), managed_only=True
    ) + _relation_findings(acls)
    if findings:
        codes = sorted({f"{finding.code} {finding.entity_id}" for finding in findings})
        raise RuntimeError(f"apply-grants verification failed: {', '.join(codes)}")
    return {
        "status": "applied",
        "roles": {"application": roles.app, "maintenance": roles.maintenance},
        "tables": len(TABLE_CLASSES),
        "sequences": sum(1 for kind in relations.values() if kind == "S"),
        "default_privileges_removed": removed,
        "foreign_grantees": _foreign_grantees(acls, roles),
    }


def _remove_default_privileges(connection: Connection, roles: DatabaseRoles) -> int:
    """Step 8: table/sequence default privileges of PUBLIC and the two roles."""
    rows = connection.execute(
        text(
            f"SELECT DISTINCT pg_get_userbyid(d.defaclrole), d.defaclnamespace <> 0,"
            f" d.defaclobjtype, {_GRANTEE} FROM pg_default_acl d"
            " CROSS JOIN LATERAL aclexplode(d.defaclacl) a"
            " WHERE d.defaclobjtype IN ('r', 'S') AND (d.defaclnamespace = 0"
            " OR d.defaclnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public'))"
            " AND a.grantee <> d.defaclrole"
        )
    ).all()
    managed = {_PUBLIC, roles.app, roles.maintenance}
    removed = 0
    for owner, in_schema, objtype, grantee in sorted(rows):
        if grantee not in managed:
            continue
        _execute(
            connection,
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} {} REVOKE ALL ON {} FROM {}").format(
                sql.Identifier(str(owner)),
                sql.SQL("IN SCHEMA public") if in_schema else sql.SQL(""),
                sql.SQL("TABLES" if objtype == "r" else "SEQUENCES"),
                sql.SQL("PUBLIC") if grantee == _PUBLIC else sql.Identifier(str(grantee)),
            ),
        )
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# provision-roles
# ---------------------------------------------------------------------------


def _differing_attributes(role: _Role) -> list[str]:
    differing = [
        keyword for attribute, keyword in _FORBIDDEN_ATTRIBUTES if getattr(role, attribute)
    ]
    if not role.inherit:
        differing.append("INHERIT")
    if not role.login:
        differing.append("LOGIN")
    if role.connection_limit != -1:
        differing.append("CONNECTION LIMIT")
    if role.valid_until != "infinity":
        differing.append("VALID UNTIL")
    return sorted(differing)


def _is_concurrent_change(exc: DBAPIError) -> bool:
    original = exc.orig
    if isinstance(original, psycopg.errors.DuplicateObject | psycopg.errors.UniqueViolation):
        return True
    return "tuple concurrently updated" in str(original)


def _provision_one(
    connection: Connection, name: str, password: str, purpose: str, owner: str
) -> dict[str, object]:
    role_name = sql.Identifier(name)
    role = _read_role(connection, name)
    fixed: list[str] = []
    revoked: list[str] = []
    settings_reset = False
    if role is None:
        _execute(connection, sql.SQL("CREATE ROLE {} {}").format(role_name, _SAFE_ATTRIBUTES))
        action = "created"
    else:
        owned = _owned_objects(connection, role)
        if owned:
            raise _provision_refusal(
                "role_owns_objects", role=name, n=len(owned), object=owned[0], owner=owner
            )
        fixed = _differing_attributes(role)
        _execute(connection, sql.SQL("ALTER ROLE {} {}").format(role_name, _SAFE_ATTRIBUTES))
        for membership in _memberships(connection, role):
            _execute(
                connection,
                sql.SQL("REVOKE {} FROM {} GRANTED BY {}").format(
                    sql.Identifier(membership.granted),
                    role_name,
                    sql.Identifier(membership.grantor),
                ),
            )
            revoked.append(membership.granted)
        for database in _role_settings(connection, role):
            if database is None:
                _execute(connection, sql.SQL("ALTER ROLE {} RESET ALL").format(role_name))
            else:
                _execute(
                    connection,
                    sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(
                        role_name, sql.Identifier(database)
                    ),
                )
            settings_reset = True
        action = "updated" if fixed or revoked or settings_reset else "unchanged"
    raw: Any = connection.connection.driver_connection
    # libpq computes the SCRAM verifier locally (PQencryptPasswordConn):
    # only the verifier is ever sent, never the password.
    verifier = raw.pgconn.encrypt_password(
        password.encode("utf-8"), name.encode("utf-8"), b"scram-sha-256"
    ).decode("ascii")
    _execute(
        connection,
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(role_name, sql.Literal(verifier)),
    )
    return {
        "name": name,
        "purpose": purpose,
        "action": action,
        "attributes_fixed": fixed,
        "memberships_revoked": sorted(set(revoked)),
        "settings_reset": settings_reset,
        "password": "set",
    }


def _verify_provisioned(connection: Connection, name: str) -> None:
    role = _read_role(connection, name)
    if role is None:
        raise RuntimeError(f"provision-roles verification failed: {name} does not exist")
    if _differing_attributes(role) or _memberships(connection, role):
        raise RuntimeError(f"provision-roles verification failed: {name} is not safe")
    if _role_settings(connection, role):
        raise RuntimeError(f"provision-roles verification failed: {name} keeps settings")


def provision_roles(
    connection: Connection,
    *,
    roles: DatabaseRoles,
    passwords: DatabaseRoles,
    lock_timeout_seconds: int,
) -> list[dict[str, object]]:
    """Create or repair both database roles and set their passwords (§3.4).

    Runs in the caller's transaction and never commits; idempotent and
    convergent. Never drops a role, never touches the owner, never grants
    a table privilege (that is ``apply_grants``).
    """
    connection.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{lock_timeout_seconds}s"},
    )
    granted = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _PROVISION_LOCK},
    ).scalar_one()
    if not granted:
        raise _provision_refusal("provision_running")
    owner = _require_superuser(connection, PROVISION_MESSAGES)
    if owner in roles:
        raise _provision_refusal("role_name_conflict", role=owner)
    try:
        entries = [
            _provision_one(connection, roles.app, passwords.app, "application", owner),
            _provision_one(
                connection, roles.maintenance, passwords.maintenance, "maintenance", owner
            ),
        ]
    except DBAPIError as exc:
        if _is_concurrent_change(exc):
            raise _provision_refusal("concurrent_change") from exc
        raise
    for name in roles:
        _verify_provisioned(connection, name)
    return entries
