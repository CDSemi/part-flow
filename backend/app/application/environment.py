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
  database.
- The Machine Asset Tag format is a single prefix + zero-padded
  numeric sequence (never a template engine). ``next_sequence`` is the
  persisted never-reuse counter owned by Machine creation (Phase 3.5
  Machines backend): configuration updates never write it.

Each mutating service commits its own transaction: a 2xx response
always reflects committed state, and a concurrent uniqueness race lost
at COMMIT surfaces as the same ``ConflictError`` as a pre-checked
duplicate.
"""

import datetime
import re
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.common import (
    UNSET,
    UnsetType,
    commit,
    optional_text,
    required_flag,
    required_text,
)
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.enums import QuantityFlowStatus
from app.infrastructure.models import (
    Area,
    Department,
    MachineAssetTagConfig,
    Operation,
    QuantityFlow,
    ScanStation,
)

# PF:AREA namespace (PROJECT_PROFILE §10): the barcode is derived from
# the stable database id at creation and never entered or edited.
AREA_BARCODE_PREFIX: Final = "PF:AREA:"

_MACHINE_ASSET_TAG_CONFIG_ID: Final = 1
_ASSET_TAG_DIGITS_MIN: Final = 1
_ASSET_TAG_DIGITS_MAX: Final = 8


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def list_departments(session: Session) -> list[Department]:
    return list(session.scalars(select(Department).order_by(Department.name, Department.id)))


def _get_department(session: Session, department_id: int) -> Department:
    department = session.get(Department, department_id)
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
    commit(session, _DEPARTMENT_CONFLICTS)
    return department


def update_department(
    session: Session,
    department_id: int,
    *,
    name: object = UNSET,
    is_active: object = UNSET,
) -> Department:
    department = _get_department(session, department_id)
    changed = False

    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Department name")
        if clean_name != department.name:
            _reject_duplicate_department_name(session, clean_name, exclude_id=department.id)
            department.name = clean_name
            changed = True

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
            department.is_active = active
            changed = True

    if changed:
        department.updated_at = func.now()
        commit(session, _DEPARTMENT_CONFLICTS)
    return department


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def list_areas(session: Session) -> list[Area]:
    return list(session.scalars(select(Area).order_by(Area.name, Area.id)))


def _get_area(session: Session, area_id: int) -> Area:
    area = session.get(Area, area_id)
    if area is None:
        raise NotFoundError(f"Area {area_id} does not exist.")
    return area


def require_active_area(session: Session, area_id: int, purpose: str) -> Area:
    area = session.get(Area, area_id)
    if area is None:
        raise InvalidInputError(f"Area {area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(f"Area '{area.name}' is inactive and cannot {purpose}.")
    return area


_AREA_CONFLICTS: Final = {
    "uq_areas_barcode_value": "The derived Area barcode is already assigned.",
}


def create_area(
    session: Session,
    *,
    department_id: int,
    name: object,
    description: str | None = None,
    color: str | None = None,
    icon_url: str | None = None,
    is_terminal: bool = False,
) -> Area:
    clean_name = required_text(name, "Area name")
    department = session.get(Department, department_id)
    if department is None:
        raise InvalidInputError(f"Department {department_id} does not exist.")
    if not department.is_active:
        raise ConflictError(
            f"Department '{department.name}' is inactive and cannot receive new Areas."
        )

    area = Area(
        department_id=department.id,
        name=clean_name,
        description=optional_text(description),
        color=optional_text(color),
        icon_url=optional_text(icon_url),
        is_terminal=is_terminal,
    )
    session.add(area)
    # Two steps, one transaction: the INSERT assigns the stable id, the
    # UPDATE assigns the derived barcode from it — the assign-once
    # trigger permits exactly this NULL → value transition.
    session.flush()
    area.barcode_value = f"{AREA_BARCODE_PREFIX}{area.id}"
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
) -> Area:
    area = _get_area(session, area_id)
    changed = False

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

    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Area active status")
        if active != area.is_active:
            if active:
                department = _get_department(session, area.department_id)
                if not department.is_active:
                    raise ConflictError(
                        f"Department '{department.name}' is inactive."
                        " Activate the Department before activating this Area."
                    )
            else:
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
            area.is_active = active
            changed = True

    if changed:
        area.updated_at = func.now()
        commit(session, _AREA_CONFLICTS)
    return area


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def list_operations(session: Session) -> list[Operation]:
    return list(session.scalars(select(Operation).order_by(Operation.area_id, Operation.code)))


def _get_operation(session: Session, operation_id: int) -> Operation:
    operation = session.get(Operation, operation_id)
    if operation is None:
        raise NotFoundError(f"Operation {operation_id} does not exist.")
    return operation


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
    operation = _get_operation(session, operation_id)
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
    station = get_scan_station(session, station_id)
    changed = False

    if not isinstance(area_id, UnsetType):
        if not isinstance(area_id, int) or isinstance(area_id, bool):
            raise InvalidInputError("Area reference must be an Area id.")
        if area_id != station.area_id:
            area = require_active_area(session, area_id, "receive Scan Stations")
            station.area_id = area.id
            changed = True
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Scan Station active status")
        if active != station.is_active:
            station.is_active = active
            changed = True

    if changed:
        station.updated_at = func.now()
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

    config = session.get(MachineAssetTagConfig, _MACHINE_ASSET_TAG_CONFIG_ID)
    if config is None:
        # First configuration: the sequence counter starts at 1 through
        # its server default and is owned by Machine creation from then
        # on — this service never writes it.
        config = MachineAssetTagConfig(
            id=_MACHINE_ASSET_TAG_CONFIG_ID, prefix=prefix, digits=digits
        )
        session.add(config)
        commit(session, {})
    elif prefix != config.prefix or digits != config.digits:
        # A format change applies to Machines created afterwards only —
        # existing Asset Tags are never renamed or regenerated, and the
        # never-reuse counter keeps counting.
        config.prefix = prefix
        config.digits = digits
        config.updated_at = func.now()
        commit(session, {})
    return config
