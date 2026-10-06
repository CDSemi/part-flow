"""Environment configuration services (Phase 3.5 minimum environment setup).

Application-layer read/write operations behind the Administration
sections that become real in Phase 3.5: Departments, Areas, Operations,
Scan Stations, and the Machine Asset Tag format (Administration →
Barcode configuration). Machines and every production workflow stay
outside this module.

Rules owned here (PROJECT_PROFILE §8.4/§8.5/§8.6/§10/§15,
IMPLEMENTATION_ROADMAP Phase 3.5, GUI_DESIGN §9):

- The Area barcode is derived, never entered: it is assigned exactly
  once at creation as ``PF:AREA:<id>`` (the database id is the stable
  id) and afterwards protected by the assign-once trigger. No other
  environment entity owns a barcode: Departments have none, Operations
  have no barcode field, and Scan Stations are identified by their
  stable Station ID — no ``PF:STATION`` namespace exists. The
  Station ID travels verbatim as one URL path segment
  (``/scan-station/<station-id>`` and
  ``/api/scan-stations/{station_id}``), so its canonical form is a
  simple URL-safe identifier: ASCII letters, digits, ``.``, ``_``
  and ``-`` only.
- Deactivating an Area that still holds active quantity is blocked
  with an explanation (GUI_DESIGN §9); deactivation is the lifecycle
  end state — no configuration service hard-deletes anything.
- Deactivating a Department that still has active Areas is blocked,
  and an Area can only be activated under an active Department, so the
  organizational tree never carries an active child below an inactive
  parent. (Safest-minimal policy — the canonical documents define no
  other Department deactivation semantics.)
- New configuration only references active entities: creating an Area
  requires an active Department, creating an Operation requires an
  active Area, and creating or rebinding a Scan Station requires an
  active Area. Rebinding a Scan Station is deliberately allowed — the
  binding is Application-controlled configuration, not frozen in the
  database. Each of these judgments, and the Department check of an
  Area activation, is made on a parent row locked FOR SHARE and
  re-read under it, held until COMMIT, so a concurrent deactivation of
  the parent and the child write have one serial outcome (Phase 13
  slice 2c).
- An Area's Worker ID mode (Phase 13 slice 3, PROJECT_PROFILE §8.13,
  §19) is Disabled, Fixed Worker or Scanned session (badge sign-in,
  Worker Sessions and the badge-confirmation gates — selectable since
  Phase 13 slice 5). A Fixed Worker exists
  exactly in Fixed Worker mode — leaving the mode clears it — and must
  be an active Worker whenever a request makes or changes it or
  activates the Area, judged on the Worker row locked FOR SHARE (taken
  after the Area row), so a concurrent Worker deactivation and the save
  have one serial outcome. The mode and the Fixed Worker join the Area
  audit snapshot.
- An Area's Worker Session timeout override (Phase 13 slice 4,
  PROJECT_PROFILE §19) is a whole number of minutes from 1 to 720, or
  empty to use the global default (`app.application.policies`); it joins
  the Area audit snapshot and never touches open sessions.
- Configuration that ends a scanned Worker Session closes it in the SAME
  transaction (`worker_sessions.close_open_sessions`, PLAN CD5): an Area
  leaving Scanned session mode closes the sessions of its stations
  (``AREA_MODE_CHANGED``), and a Scan Station rebind or deactivation
  closes the station's session (``STATION_CHANGED``). An already-expired
  session closes ``EXPIRED`` at its expiry. Closes append no
  ``audit_events`` row — the session row is its own audit record.
- The Machine Asset Tag format is a single prefix + zero-padded
  numeric sequence (never a template engine). ``next_sequence`` is the
  persisted never-reuse counter owned by Machine creation (Phase 3.5
  Machines backend): configuration updates never write it.

Each mutating service commits its own transaction: a 2xx response
always reflects committed state, and a concurrent uniqueness race lost
at COMMIT surfaces as the same ``ConflictError`` as a pre-checked
duplicate.

Every effective write appends exactly one ``audit_events`` row in the
SAME transaction (Phase 13, PROJECT_PROFILE §28 "administrative
configuration changes"): ``CREATED`` or ``UPDATED``, entity
``Department``/``Area``/``Operation`` (``entity_id`` the internal id),
``ScanStation`` (the Station ID) or ``MachineAssetTagConfig`` (``"1"``,
the singleton), with the explicit-field snapshots below and
``actor_reference`` NULL until Phase 14. An update locks its row first,
in the mode its own UPDATE takes, so every ``before_data`` is the
committed predecessor. Rejected writes, lost races and no-ops append
nothing, and ``next_sequence`` is never audited.
"""

import datetime
import re
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application import audit, policies, worker_sessions
from app.application.common import (
    UNSET,
    UnsetType,
    commit,
    flush,
    optional_text,
    required_flag,
    required_text,
)
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    QuantityFlowStatus,
    WorkerIdentificationMode,
    WorkerSessionEndReason,
)
from app.infrastructure.models import (
    Area,
    Department,
    MachineAssetTagConfig,
    Operation,
    QuantityFlow,
    ScanStation,
    Worker,
)

# PF:AREA namespace (PROJECT_PROFILE §10): the barcode is derived from
# the stable database id at creation and never entered or edited.
AREA_BARCODE_PREFIX: Final = "PF:AREA:"

_MACHINE_ASSET_TAG_CONFIG_ID: Final = 1
_ASSET_TAG_DIGITS_MIN: Final = 1
_ASSET_TAG_DIGITS_MAX: Final = 8

# A concurrent first configuration wins the singleton primary key at
# COMMIT; the loser writes nothing. The loaded Barcode configuration
# panel has no reload control and the Administration section is not part
# of the route, so the recovery step is a page refresh followed by
# reopening Barcode configuration, which re-runs the section load.
_ASSET_TAG_FORMAT_CONFLICTS: Final = {
    "pk_machine_asset_tag_config": (
        "The Machine Asset Tag format was just saved by someone else."
        " Refresh the page and open Barcode configuration again to see the saved format,"
        " then apply your change again."
    ),
}

# Lock-first mode of every update: FOR NO KEY UPDATE, the lock the
# edit's own UPDATE takes anyway — acquired before the snapshot, so
# concurrent edits serialize and each audit row's before_data is the
# committed predecessor, while FK checks and the allocation path's
# FOR KEY SHARE on these rows are never blocked by it. (An UPDATE that
# changes a unique-key column, such as a Department name or an Operation
# code, still takes FOR UPDATE itself, as it always did.)
_EDIT_LOCK: Final = {"key_share": True}

# Parent-activity lock: FOR SHARE on a parent row (Department of an
# Area; Area of an Operation, Scan Station or Machine), taken and
# re-read BEFORE its active flag is judged and held until COMMIT. It
# conflicts with every UPDATE of that row — the implicit FOR NO KEY
# UPDATE of a plain UPDATE included, so any deactivation path
# serializes with it whatever lock it takes — while FK checks (FOR KEY
# SHARE) and concurrent child writes under the same parent (FOR SHARE)
# never wait on it.
_PARENT_LOCK: Final = {"read": True}


# ---------------------------------------------------------------------------
# Audit snapshots: explicit field lists. A column a later slice adds is
# audited only if that slice adds it here; timestamps, the id (it is the
# audit entity_id) and the Asset Tag ``next_sequence`` never belong.
# ---------------------------------------------------------------------------


def _department_snapshot(department: Department) -> dict[str, Any]:
    return {"name": department.name, "is_active": department.is_active}


def _area_snapshot(area: Area) -> dict[str, Any]:
    return {
        "department_id": area.department_id,
        "name": area.name,
        "barcode_value": area.barcode_value,
        "description": area.description,
        "color": area.color,
        "icon_url": area.icon_url,
        "is_terminal": area.is_terminal,
        "is_active": area.is_active,
        "worker_identification_mode": area.worker_identification_mode,
        "fixed_worker_id": area.fixed_worker_id,
        "worker_session_timeout_minutes": area.worker_session_timeout_minutes,
    }


def _operation_snapshot(operation: Operation) -> dict[str, Any]:
    duration = operation.default_expected_duration
    return {
        "area_id": operation.area_id,
        "code": operation.code,
        "name": operation.name,
        "description": operation.description,
        # Seconds as a JSON number (timedelta is not JSON); None = not set.
        "default_expected_duration_seconds": (
            duration.total_seconds() if duration is not None else None
        ),
        "is_external": operation.is_external,
        "is_active": operation.is_active,
    }


def _scan_station_snapshot(station: ScanStation) -> dict[str, Any]:
    return {"area_id": station.area_id, "is_active": station.is_active}


def _asset_tag_format_snapshot(config: MachineAssetTagConfig) -> dict[str, Any]:
    # next_sequence is Machine creation's never-reuse counter, not configuration.
    return {"prefix": config.prefix, "digits": config.digits}


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def list_departments(session: Session) -> list[Department]:
    return list(session.scalars(select(Department).order_by(Department.name, Department.id)))


def _get_department(session: Session, department_id: int) -> Department:
    """The parent-activity read of an Area activation: the Department
    under the parent-activity lock (FOR SHARE until COMMIT), re-read
    under it."""
    department = session.get(
        Department, department_id, with_for_update=_PARENT_LOCK, populate_existing=True
    )
    if department is None:
        raise NotFoundError(f"Department {department_id} does not exist.")
    return department


def _reject_duplicate_department_name(
    session: Session, name: str, exclude_id: int | None = None
) -> None:
    query = select(Department.id).where(Department.name == name).limit(1)
    if exclude_id is not None:
        query = query.where(Department.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"A Department named '{name}' already exists.")


_DEPARTMENT_CONFLICTS: Final = {
    "uq_departments_name": "A Department with this name already exists.",
}


def create_department(session: Session, *, name: object) -> Department:
    clean_name = required_text(name, "Department name")
    _reject_duplicate_department_name(session, clean_name)
    department = Department(name=clean_name)
    session.add(department)
    # The id is the audit entity id; a name race lost here surfaces as
    # the same conflict as one lost at COMMIT.
    flush(session, _DEPARTMENT_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.DEPARTMENT,
        entity_id=str(department.id),
        before_data=None,
        after_data=_department_snapshot(department),
    )
    commit(session, _DEPARTMENT_CONFLICTS)
    return department


def update_department(
    session: Session,
    department_id: int,
    *,
    name: object = UNSET,
    is_active: object = UNSET,
) -> Department:
    department = session.get(
        Department, department_id, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if department is None:
        raise NotFoundError(f"Department {department_id} does not exist.")
    before = _department_snapshot(department)

    new_name: str | None = None
    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Department name")
        if clean_name != department.name:
            _reject_duplicate_department_name(session, clean_name, exclude_id=department.id)
            new_name = clean_name

    new_active: bool | None = None
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Department active status")
        if active != department.is_active:
            if not active:
                has_active_area = session.scalar(
                    select(Area.id)
                    .where(Area.department_id == department.id, Area.is_active.is_(True))
                    .limit(1)
                )
                if has_active_area is not None:
                    raise ConflictError(
                        "This Department still has active Areas."
                        " Deactivate its Areas first, then deactivate the Department."
                    )
            new_active = active

    # Every read is done before the first assignment: assigning the name
    # first would let the active-Area query autoflush the rename UPDATE
    # outside commit(), so a uq_departments_name race lost there would
    # escape as a raw IntegrityError instead of the duplicate-name 409.
    # Assigned last, the UPDATE is emitted inside commit(), which
    # translates it.
    if new_name is not None:
        department.name = new_name
    if new_active is not None:
        department.is_active = new_active
    changed = new_name is not None or new_active is not None

    if changed:
        department.updated_at = func.now()
        after = _department_snapshot(department)
        if after != before:
            audit.append_audit_event(
                session,
                event_type=AuditEventType.UPDATED,
                entity_type=AuditEntityType.DEPARTMENT,
                entity_id=str(department.id),
                before_data=before,
                after_data=after,
            )
        commit(session, _DEPARTMENT_CONFLICTS)
    return department


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def list_areas(session: Session) -> list[Area]:
    return list(session.scalars(select(Area).order_by(Area.name, Area.id)))


def require_active_area(session: Session, area_id: int, purpose: str) -> Area:
    """The Area under the parent-activity lock (FOR SHARE until COMMIT),
    re-read under it; for write paths only."""
    area = session.get(Area, area_id, with_for_update=_PARENT_LOCK, populate_existing=True)
    if area is None:
        raise InvalidInputError(f"Area {area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(f"Area '{area.name}' is inactive and cannot {purpose}.")
    return area


_AREA_CONFLICTS: Final = {
    "uq_areas_barcode_value": "The derived Area barcode is already assigned.",
}


def _session_timeout_override(value: object) -> int | None:
    """An Area's Worker Session timeout override: whole minutes or None (the default)."""
    if value is None:
        return None
    if not policies.is_timeout_minutes(value):
        raise InvalidInputError(
            "An Area's Worker session timeout must be a whole number of minutes from 1 to"
            " 720, or empty to use the default."
        )
    return value


def _worker_identification(
    session: Session,
    *,
    current_mode: str,
    current_fixed_worker_id: int | None,
    mode: object,
    fixed_worker_id: int | None | UnsetType,
    is_new: bool,
    activating: bool,
) -> tuple[WorkerIdentificationMode, int | None]:
    """The Area's target Worker ID mode and Fixed Worker, validated.

    Reads only — the caller assigns. Leaving Fixed Worker mode clears
    the Fixed Worker without the client sending ``null``. Scanned
    session mode (selectable since Phase 13 slice 5) takes no Fixed
    Worker, like Disabled. The Fixed Worker is judged — locked FOR SHARE and
    re-read, so a concurrent deactivation and this save have one serial
    outcome — only when this request makes or changes it, or activates
    the Area: an unrelated edit never re-judges a Fixed Worker.
    """
    current = WorkerIdentificationMode(current_mode)
    requested: WorkerIdentificationMode | None = None
    if not isinstance(mode, UnsetType):
        modes = {item.value for item in WorkerIdentificationMode}
        if not isinstance(mode, str) or mode not in modes:
            raise InvalidInputError("Worker ID mode must be DISABLED, FIXED or SCANNED.")
        requested = WorkerIdentificationMode(mode)
    if isinstance(fixed_worker_id, bool):
        raise InvalidInputError("Fixed Worker reference must be a Worker id.")

    target_mode = requested if requested is not None else current
    target_fixed: int | None
    if not isinstance(fixed_worker_id, UnsetType):
        target_fixed = fixed_worker_id
    elif target_mode is WorkerIdentificationMode.FIXED:
        target_fixed = current_fixed_worker_id
    else:
        target_fixed = None

    if target_mode is WorkerIdentificationMode.FIXED and target_fixed is None:
        raise InvalidInputError("Choose the Fixed Worker for Fixed Worker mode.")
    if target_mode is not WorkerIdentificationMode.FIXED and target_fixed is not None:
        raise InvalidInputError("A Fixed Worker can be set only in Fixed Worker mode.")

    if target_mode is WorkerIdentificationMode.FIXED and target_fixed is not None:
        configuration_changes = (
            is_new or target_mode is not current or target_fixed != current_fixed_worker_id
        )
        if configuration_changes or activating:
            worker = session.get(
                Worker, target_fixed, with_for_update=_PARENT_LOCK, populate_existing=True
            )
            if worker is None:
                raise InvalidInputError(f"Worker {target_fixed} does not exist.")
            if not worker.is_active:
                if configuration_changes:
                    raise ConflictError(
                        f"Worker '{worker.name}' is inactive and cannot be the Fixed Worker"
                        " of an Area. Choose an active Worker."
                    )
                raise ConflictError(
                    f"The Fixed Worker '{worker.name}' is inactive. Choose an active Fixed"
                    " Worker before activating this Area."
                )
    return target_mode, target_fixed


def create_area(
    session: Session,
    *,
    department_id: int,
    name: object,
    description: str | None = None,
    color: str | None = None,
    icon_url: str | None = None,
    is_terminal: bool = False,
    worker_identification_mode: object = UNSET,
    fixed_worker_id: int | None | UnsetType = UNSET,
    worker_session_timeout_minutes: object = UNSET,
) -> Area:
    clean_name = required_text(name, "Area name")
    session_timeout = (
        None
        if isinstance(worker_session_timeout_minutes, UnsetType)
        else _session_timeout_override(worker_session_timeout_minutes)
    )
    # Parent-activity read: the Department is judged under FOR SHARE.
    department = session.get(
        Department, department_id, with_for_update=_PARENT_LOCK, populate_existing=True
    )
    if department is None:
        raise InvalidInputError(f"Department {department_id} does not exist.")
    if not department.is_active:
        raise ConflictError(
            f"Department '{department.name}' is inactive and cannot receive new Areas."
        )
    # A new Area starts Disabled unless the request configures it; a
    # Fixed Worker is locked FOR SHARE after the Department.
    mode, fixed_worker = _worker_identification(
        session,
        current_mode=WorkerIdentificationMode.DISABLED,
        current_fixed_worker_id=None,
        mode=worker_identification_mode,
        fixed_worker_id=fixed_worker_id,
        is_new=True,
        activating=False,
    )

    area = Area(
        department_id=department.id,
        name=clean_name,
        description=optional_text(description),
        color=optional_text(color),
        icon_url=optional_text(icon_url),
        is_terminal=is_terminal,
        worker_identification_mode=mode.value,
        fixed_worker_id=fixed_worker,
        worker_session_timeout_minutes=session_timeout,
    )
    session.add(area)
    # Two steps, one transaction: the INSERT assigns the stable id, the
    # UPDATE assigns the derived barcode from it — the assign-once
    # trigger permits exactly this NULL → value transition.
    flush(session, _AREA_CONFLICTS)
    area.barcode_value = f"{AREA_BARCODE_PREFIX}{area.id}"
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.AREA,
        entity_id=str(area.id),
        before_data=None,
        after_data=_area_snapshot(area),
    )
    commit(session, _AREA_CONFLICTS)
    return area


def update_area(
    session: Session,
    area_id: int,
    *,
    name: object = UNSET,
    description: str | None | UnsetType = UNSET,
    color: str | None | UnsetType = UNSET,
    icon_url: str | None | UnsetType = UNSET,
    is_terminal: object = UNSET,
    is_active: object = UNSET,
    worker_identification_mode: object = UNSET,
    fixed_worker_id: int | None | UnsetType = UNSET,
    worker_session_timeout_minutes: object = UNSET,
) -> Area:
    # Every edit loads the Area row under its lock FIRST — before the
    # audit snapshot and any field mutation — and re-reads the latest
    # committed state (populate_existing), so every edit applies on the
    # latest row and its before_data is the committed predecessor. A
    # requested deactivation takes FOR UPDATE (it also excludes the FK
    # KEY SHARE of concurrent child inserts); any other edit takes the
    # FOR NO KEY UPDATE its own UPDATE takes. Both conflict with the
    # FOR UPDATE production release holds on the Area, which is the
    # serialization the deactivation check below relies on, and with
    # the parent-activity FOR SHARE that child writes (Operation and
    # Scan Station create, Scan Station rebind, Machine create and
    # reactivation) take on this Area before they judge it, so an
    # in-flight child write and this edit serialize. The mode
    # follows the raw value (only a bool passes required_flag), so an
    # unknown Area is still a 404 before a malformed flag's 422.
    area = session.get(
        Area,
        area_id,
        with_for_update=True if is_active is False else _EDIT_LOCK,
        populate_existing=True,
    )
    if area is None:
        raise NotFoundError(f"Area {area_id} does not exist.")
    before = _area_snapshot(area)
    changed = False

    requested_active: bool | None = None
    if not isinstance(is_active, UnsetType):
        requested_active = required_flag(is_active, "Area active status")
    # The Worker Session timeout override (Phase 13): shape only, no read.
    session_timeout: int | None | UnsetType = UNSET
    if not isinstance(worker_session_timeout_minutes, UnsetType):
        session_timeout = _session_timeout_override(worker_session_timeout_minutes)
    leaves_scanned = area.worker_identification_mode == WorkerIdentificationMode.SCANNED

    # Worker ID mode and Fixed Worker (Phase 13): judged before the
    # first assignment — the Fixed Worker lock (FOR SHARE) is the second
    # lock, after this Area row, and no read follows a write.
    mode, fixed_worker = _worker_identification(
        session,
        current_mode=area.worker_identification_mode,
        current_fixed_worker_id=area.fixed_worker_id,
        mode=worker_identification_mode,
        fixed_worker_id=fixed_worker_id,
        is_new=False,
        activating=requested_active is True and not area.is_active,
    )

    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Area name")
        if clean_name != area.name:
            area.name = clean_name
            changed = True
    if not isinstance(description, UnsetType):
        value = optional_text(description)
        if value != area.description:
            area.description = value
            changed = True
    if not isinstance(color, UnsetType):
        value = optional_text(color)
        if value != area.color:
            area.color = value
            changed = True
    if not isinstance(icon_url, UnsetType):
        value = optional_text(icon_url)
        if value != area.icon_url:
            area.icon_url = value
            changed = True
    if not isinstance(is_terminal, UnsetType):
        terminal = required_flag(is_terminal, "Area terminal flag")
        if terminal != area.is_terminal:
            area.is_terminal = terminal
            changed = True
    if mode.value != area.worker_identification_mode:
        area.worker_identification_mode = mode.value
        changed = True
    if fixed_worker != area.fixed_worker_id:
        area.fixed_worker_id = fixed_worker
        changed = True
    leaves_scanned = leaves_scanned and mode is not WorkerIdentificationMode.SCANNED
    if (
        not isinstance(session_timeout, UnsetType)
        and session_timeout != area.worker_session_timeout_minutes
    ):
        # Never rewrites open sessions: it applies from each session's
        # next refresh or sign-in.
        area.worker_session_timeout_minutes = session_timeout
        changed = True

    # requested_active was validated, and the row locked and re-read,
    # before any mutation above, so area.is_active is the latest
    # committed state here.
    if requested_active is not None and requested_active != area.is_active:
        if requested_active:
            department = _get_department(session, area.department_id)
            if not department.is_active:
                raise ConflictError(
                    f"Department '{department.name}' is inactive."
                    " Activate the Department before activating this Area."
                )
        else:
            # Serialize with production release: the release
            # transaction holds this Area row lock from before its
            # own active check until COMMIT, so exactly one serial
            # outcome exists — either the release commits first and
            # the check below blocks deactivation, or the
            # deactivation commits first and the release sees the
            # inactive Area. An inactive Area can never end up
            # holding a freshly released ACTIVE flow.
            holds_quantity = session.scalar(
                select(QuantityFlow.id)
                .where(
                    QuantityFlow.current_area_id == area.id,
                    QuantityFlow.status == QuantityFlowStatus.ACTIVE,
                )
                .limit(1)
            )
            if holds_quantity is not None:
                raise ConflictError(
                    "This Area still holds active quantity."
                    " Move or complete the quantity through the normal production"
                    " workflow before deactivating the Area."
                )
        area.is_active = requested_active
        changed = True
    # A concurrent writer already deactivated the locked row: the
    # deactivation stage is a no-op, while the metadata edits above
    # still apply and commit normally.

    if changed:
        area.updated_at = func.now()
        if leaves_scanned:
            # Leaving Scanned session mode ends the sessions of the
            # Area's stations in this transaction — after every read and
            # refusal above, under the Area lock taken first.
            worker_sessions.close_open_sessions(
                session,
                reason=WorkerSessionEndReason.AREA_MODE_CHANGED,
                station_ids=list(
                    session.scalars(
                        select(ScanStation.station_id).where(ScanStation.area_id == area.id)
                    )
                ),
            )
        after = _area_snapshot(area)
        if after != before:
            audit.append_audit_event(
                session,
                event_type=AuditEventType.UPDATED,
                entity_type=AuditEntityType.AREA,
                entity_id=str(area.id),
                before_data=before,
                after_data=after,
            )
        commit(session, _AREA_CONFLICTS)
    return area


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def list_operations(session: Session) -> list[Operation]:
    return list(session.scalars(select(Operation).order_by(Operation.area_id, Operation.code)))


def _require_positive_duration(value: datetime.timedelta | None) -> datetime.timedelta | None:
    if value is not None and value <= datetime.timedelta(0):
        raise InvalidInputError("Default expected duration must be positive.")
    return value


def _reject_duplicate_operation_code(
    session: Session, area_id: int, code: str, exclude_id: int | None = None
) -> None:
    query = (
        select(Operation.id).where(Operation.area_id == area_id, Operation.code == code).limit(1)
    )
    if exclude_id is not None:
        query = query.where(Operation.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"The Area already has an Operation with code '{code}'.")


_OPERATION_CONFLICTS: Final = {
    "uq_operations_area_id_code": "The Area already has an Operation with this code.",
}


def create_operation(
    session: Session,
    *,
    area_id: int,
    code: object,
    name: str | None = None,
    description: str | None = None,
    default_expected_duration: datetime.timedelta | None = None,
    is_external: bool = False,
) -> Operation:
    clean_code = required_text(code, "Operation code")
    area = require_active_area(session, area_id, "receive new Operations")
    _reject_duplicate_operation_code(session, area.id, clean_code)
    operation = Operation(
        area_id=area.id,
        code=clean_code,
        name=optional_text(name),
        description=optional_text(description),
        default_expected_duration=_require_positive_duration(default_expected_duration),
        is_external=is_external,
    )
    session.add(operation)
    # The id is the audit entity id; a code race lost here surfaces as
    # the same conflict as one lost at COMMIT.
    flush(session, _OPERATION_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.OPERATION,
        entity_id=str(operation.id),
        before_data=None,
        after_data=_operation_snapshot(operation),
    )
    commit(session, _OPERATION_CONFLICTS)
    return operation


def update_operation(
    session: Session,
    operation_id: int,
    *,
    code: object = UNSET,
    name: str | None | UnsetType = UNSET,
    description: str | None | UnsetType = UNSET,
    default_expected_duration: datetime.timedelta | None | UnsetType = UNSET,
    is_external: object = UNSET,
    is_active: object = UNSET,
) -> Operation:
    # The Area binding is deliberately not updatable: Movement history
    # will reference Operations in their Area context, and moving an
    # Operation between Areas would make that history ambiguous.
    operation = session.get(
        Operation, operation_id, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if operation is None:
        raise NotFoundError(f"Operation {operation_id} does not exist.")
    before = _operation_snapshot(operation)
    changed = False

    if not isinstance(code, UnsetType):
        clean_code = required_text(code, "Operation code")
        if clean_code != operation.code:
            _reject_duplicate_operation_code(
                session, operation.area_id, clean_code, exclude_id=operation.id
            )
            operation.code = clean_code
            changed = True
    if not isinstance(name, UnsetType):
        value = optional_text(name)
        if value != operation.name:
            operation.name = value
            changed = True
    if not isinstance(description, UnsetType):
        value = optional_text(description)
        if value != operation.description:
            operation.description = value
            changed = True
    if not isinstance(default_expected_duration, UnsetType):
        duration = _require_positive_duration(default_expected_duration)
        if duration != operation.default_expected_duration:
            operation.default_expected_duration = duration
            changed = True
    if not isinstance(is_external, UnsetType):
        external = required_flag(is_external, "Operation external flag")
        if external != operation.is_external:
            operation.is_external = external
            changed = True
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Operation active status")
        if active != operation.is_active:
            operation.is_active = active
            changed = True

    if changed:
        operation.updated_at = func.now()
        after = _operation_snapshot(operation)
        if after != before:
            audit.append_audit_event(
                session,
                event_type=AuditEventType.UPDATED,
                entity_type=AuditEntityType.OPERATION,
                entity_id=str(operation.id),
                before_data=before,
                after_data=after,
            )
        commit(session, _OPERATION_CONFLICTS)
    return operation


# ---------------------------------------------------------------------------
# Scan Stations
# ---------------------------------------------------------------------------


def list_scan_stations(session: Session) -> list[ScanStation]:
    return list(session.scalars(select(ScanStation).order_by(ScanStation.station_id)))


def get_scan_station(session: Session, station_id: str) -> ScanStation:
    station = session.get(ScanStation, station_id)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    return station


# Station ID canonical form: one URL-safe path segment (matches the
# ck_scan_stations_station_id_canonical CHECK in migration 0003).
_STATION_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]+\Z")


def _canonical_station_id(value: object) -> str:
    station_id = required_text(value, "Station ID")
    if not _STATION_ID_PATTERN.fullmatch(station_id):
        raise InvalidInputError("Station ID may only contain letters, digits, '.', '_' and '-'.")
    return station_id


_SCAN_STATION_CONFLICTS: Final = {
    "pk_scan_stations": "A Scan Station with this Station ID already exists.",
}


def create_scan_station(
    session: Session,
    *,
    station_id: object,
    area_id: int,
    is_active: bool = True,
) -> ScanStation:
    clean_station_id = _canonical_station_id(station_id)
    if session.get(ScanStation, clean_station_id) is not None:
        raise ConflictError(f"A Scan Station with Station ID '{clean_station_id}' already exists.")
    area = require_active_area(session, area_id, "receive new Scan Stations")
    station = ScanStation(station_id=clean_station_id, area_id=area.id, is_active=is_active)
    session.add(station)
    # The Station ID is the audit entity id, so no flush is needed: a
    # race lost at COMMIT rolls back the station and its audit row together.
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.SCAN_STATION,
        entity_id=station.station_id,
        before_data=None,
        after_data=_scan_station_snapshot(station),
    )
    commit(session, _SCAN_STATION_CONFLICTS)
    return station


def update_scan_station(
    session: Session,
    station_id: str,
    *,
    area_id: object = UNSET,
    is_active: object = UNSET,
) -> ScanStation:
    # The Station ID itself is the stable identity (PROJECT_PROFILE
    # §15) and is never renamed; rebinding to another active Area is
    # the Application-controlled configuration workflow.
    station = session.get(
        ScanStation, station_id, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    before = _scan_station_snapshot(station)
    changed = False
    ends_session = False

    if not isinstance(area_id, UnsetType):
        if not isinstance(area_id, int) or isinstance(area_id, bool):
            raise InvalidInputError("Area reference must be an Area id.")
        if area_id != station.area_id:
            area = require_active_area(session, area_id, "receive Scan Stations")
            station.area_id = area.id
            changed = ends_session = True
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Scan Station active status")
        if active != station.is_active:
            station.is_active = active
            changed = True
            ends_session = ends_session or not active

    if changed:
        station.updated_at = func.now()
        if ends_session:
            # A rebind or deactivation ends the station's Worker Session
            # in this transaction (Phase 13), after the station lock and
            # the new Area's parent-activity lock.
            worker_sessions.close_open_sessions(
                session,
                reason=WorkerSessionEndReason.STATION_CHANGED,
                station_ids=[station.station_id],
            )
        after = _scan_station_snapshot(station)
        if after != before:
            audit.append_audit_event(
                session,
                event_type=AuditEventType.UPDATED,
                entity_type=AuditEntityType.SCAN_STATION,
                entity_id=station.station_id,
                before_data=before,
                after_data=after,
            )
        commit(session, _SCAN_STATION_CONFLICTS)
    return station


# ---------------------------------------------------------------------------
# Machine Asset Tag format (Administration → Barcode configuration)
# ---------------------------------------------------------------------------


def get_machine_asset_tag_format(session: Session) -> MachineAssetTagConfig:
    config = session.get(MachineAssetTagConfig, _MACHINE_ASSET_TAG_CONFIG_ID)
    if config is None:
        raise NotFoundError(
            "The Machine Asset Tag format is not configured yet."
            " Configure a prefix and number length before creating Machines."
        )
    return config


def upsert_machine_asset_tag_format(
    session: Session, *, prefix: str, digits: int
) -> MachineAssetTagConfig:
    if re.search(r"[\s:]", prefix):
        raise InvalidInputError("The Asset Tag prefix must not contain whitespace or ':'.")
    if not _ASSET_TAG_DIGITS_MIN <= digits <= _ASSET_TAG_DIGITS_MAX:
        raise InvalidInputError(
            f"The Asset Tag number length must be between {_ASSET_TAG_DIGITS_MIN}"
            f" and {_ASSET_TAG_DIGITS_MAX} digits."
        )

    # Lock-first on the singleton (FOR NO KEY UPDATE, the mode of Machine
    # creation's counter UPDATE): an update serializes with it and with
    # other format edits. No row yet means first configuration, which
    # takes the create path unlocked, as before.
    config = session.get(
        MachineAssetTagConfig,
        _MACHINE_ASSET_TAG_CONFIG_ID,
        with_for_update=_EDIT_LOCK,
        populate_existing=True,
    )
    if config is None:
        # First configuration: the sequence counter starts at 1 through
        # its server default and is owned by Machine creation from then
        # on — this service never writes it.
        config = MachineAssetTagConfig(
            id=_MACHINE_ASSET_TAG_CONFIG_ID, prefix=prefix, digits=digits
        )
        session.add(config)
        audit.append_audit_event(
            session,
            event_type=AuditEventType.CREATED,
            entity_type=AuditEntityType.MACHINE_ASSET_TAG_CONFIG,
            entity_id=str(_MACHINE_ASSET_TAG_CONFIG_ID),
            before_data=None,
            after_data=_asset_tag_format_snapshot(config),
        )
        commit(session, _ASSET_TAG_FORMAT_CONFLICTS)
    elif prefix != config.prefix or digits != config.digits:
        # A format change applies to Machines created afterwards only —
        # existing Asset Tags are never renamed or regenerated, and the
        # never-reuse counter keeps counting.
        before = _asset_tag_format_snapshot(config)
        config.prefix = prefix
        config.digits = digits
        config.updated_at = func.now()
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.MACHINE_ASSET_TAG_CONFIG,
            entity_id=str(_MACHINE_ASSET_TAG_CONFIG_ID),
            before_data=before,
            after_data=_asset_tag_format_snapshot(config),
        )
        commit(session, _ASSET_TAG_FORMAT_CONFLICTS)
    return config
