"""Environment configuration endpoints (Phase 3.5 minimum environment setup).

HTTP surface for the Administration sections that become real in
Phase 3.5: Departments, Areas, Operations, Scan Stations, and the
Machine Asset Tag format (Administration → Barcode configuration).

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits a server-owned field such
as an Area ``barcode_value`` or the ``next_sequence`` counter is
rejected instead of silently ignored), the Application layer owns
every business rule and the transaction, and the central handlers in
``app.api.errors`` translate typed failures into HTTP responses.

Deliberate surface decisions:

- No DELETE endpoints exist — environment configuration deactivates,
  it is never hard-deleted (GUI_DESIGN §9 table + editor pattern).
- ``GET /api/scan-stations/{station_id}`` resolves one station by its
  stable Station ID: the Station Selector and the per-station route
  (``/scan-station/<station-id>``, PROJECT_PROFILE §15) resolve real
  configuration from it.
- Durations (``default_expected_duration``) travel as ISO 8601
  duration strings (for example ``PT30M``), Pydantic's canonical
  ``timedelta`` JSON form.
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.application import environment

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DepartmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class DepartmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_active: bool | None = None


@router.get("/departments")
def list_departments(session: SessionDep) -> list[DepartmentResponse]:
    return [
        DepartmentResponse.model_validate(department)
        for department in environment.list_departments(session)
    ]


@router.post("/departments", status_code=201)
def create_department(body: DepartmentCreateRequest, session: SessionDep) -> DepartmentResponse:
    department = environment.create_department(session, name=body.name)
    return DepartmentResponse.model_validate(department)


@router.patch("/departments/{department_id}")
def update_department(
    department_id: int, body: DepartmentUpdateRequest, session: SessionDep
) -> DepartmentResponse:
    department = environment.update_department(
        session, department_id, **body.model_dump(exclude_unset=True)
    )
    return DepartmentResponse.model_validate(department)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


class AreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    department_id: int
    name: str
    # Derived PF:AREA:<id> value — assigned by the server at creation,
    # stable forever afterwards.
    barcode_value: str | None
    description: str | None
    color: str | None
    icon_url: str | None
    is_terminal: bool
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AreaCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: int
    name: str
    description: str | None = None
    color: str | None = None
    icon_url: str | None = None
    is_terminal: bool = False


class AreaUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon_url: str | None = None
    is_terminal: bool | None = None
    is_active: bool | None = None


@router.get("/areas")
def list_areas(session: SessionDep) -> list[AreaResponse]:
    return [AreaResponse.model_validate(area) for area in environment.list_areas(session)]


@router.post("/areas", status_code=201)
def create_area(body: AreaCreateRequest, session: SessionDep) -> AreaResponse:
    area = environment.create_area(
        session,
        department_id=body.department_id,
        name=body.name,
        description=body.description,
        color=body.color,
        icon_url=body.icon_url,
        is_terminal=body.is_terminal,
    )
    return AreaResponse.model_validate(area)


@router.patch("/areas/{area_id}")
def update_area(area_id: int, body: AreaUpdateRequest, session: SessionDep) -> AreaResponse:
    area = environment.update_area(session, area_id, **body.model_dump(exclude_unset=True))
    return AreaResponse.model_validate(area)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class OperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    area_id: int
    code: str
    name: str | None
    description: str | None
    default_expected_duration: datetime.timedelta | None
    is_external: bool
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class OperationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_id: int
    code: str
    name: str | None = None
    description: str | None = None
    default_expected_duration: datetime.timedelta | None = None
    is_external: bool = False


class OperationUpdateRequest(BaseModel):
    # The Area binding is not updatable (no area_id field): Movement
    # history references Operations in their Area context.
    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    name: str | None = None
    description: str | None = None
    default_expected_duration: datetime.timedelta | None = None
    is_external: bool | None = None
    is_active: bool | None = None


@router.get("/operations")
def list_operations(session: SessionDep) -> list[OperationResponse]:
    return [
        OperationResponse.model_validate(operation)
        for operation in environment.list_operations(session)
    ]


@router.post("/operations", status_code=201)
def create_operation(body: OperationCreateRequest, session: SessionDep) -> OperationResponse:
    operation = environment.create_operation(
        session,
        area_id=body.area_id,
        code=body.code,
        name=body.name,
        description=body.description,
        default_expected_duration=body.default_expected_duration,
        is_external=body.is_external,
    )
    return OperationResponse.model_validate(operation)


@router.patch("/operations/{operation_id}")
def update_operation(
    operation_id: int, body: OperationUpdateRequest, session: SessionDep
) -> OperationResponse:
    operation = environment.update_operation(
        session, operation_id, **body.model_dump(exclude_unset=True)
    )
    return OperationResponse.model_validate(operation)


# ---------------------------------------------------------------------------
# Scan Stations
# ---------------------------------------------------------------------------


class ScanStationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    station_id: str
    area_id: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ScanStationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str
    area_id: int
    is_active: bool = True


class ScanStationUpdateRequest(BaseModel):
    # The Station ID is the stable identity and is never renamed.
    model_config = ConfigDict(extra="forbid")

    area_id: int | None = None
    is_active: bool | None = None


@router.get("/scan-stations")
def list_scan_stations(session: SessionDep) -> list[ScanStationResponse]:
    return [
        ScanStationResponse.model_validate(station)
        for station in environment.list_scan_stations(session)
    ]


@router.get("/scan-stations/{station_id}")
def get_scan_station(station_id: str, session: SessionDep) -> ScanStationResponse:
    station = environment.get_scan_station(session, station_id)
    return ScanStationResponse.model_validate(station)


@router.post("/scan-stations", status_code=201)
def create_scan_station(body: ScanStationCreateRequest, session: SessionDep) -> ScanStationResponse:
    station = environment.create_scan_station(
        session,
        station_id=body.station_id,
        area_id=body.area_id,
        is_active=body.is_active,
    )
    return ScanStationResponse.model_validate(station)


@router.patch("/scan-stations/{station_id}")
def update_scan_station(
    station_id: str, body: ScanStationUpdateRequest, session: SessionDep
) -> ScanStationResponse:
    station = environment.update_scan_station(
        session, station_id, **body.model_dump(exclude_unset=True)
    )
    return ScanStationResponse.model_validate(station)


# ---------------------------------------------------------------------------
# Barcode configuration — Machine Asset Tag format
# ---------------------------------------------------------------------------


class MachineAssetTagFormatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prefix: str
    digits: int
    # Read-only view of the persisted never-reuse counter: the UI
    # derives the "Next Asset Tag" preview from it. Allocation is owned
    # by Machine creation, never by this configuration surface.
    next_sequence: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MachineAssetTagFormatPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    digits: int


@router.get("/barcode-configuration/machine-asset-tag-format")
def get_machine_asset_tag_format(session: SessionDep) -> MachineAssetTagFormatResponse:
    config = environment.get_machine_asset_tag_format(session)
    return MachineAssetTagFormatResponse.model_validate(config)


@router.put("/barcode-configuration/machine-asset-tag-format")
def put_machine_asset_tag_format(
    body: MachineAssetTagFormatPutRequest, session: SessionDep
) -> MachineAssetTagFormatResponse:
    config = environment.upsert_machine_asset_tag_format(
        session, prefix=body.prefix, digits=body.digits
    )
    return MachineAssetTagFormatResponse.model_validate(config)
