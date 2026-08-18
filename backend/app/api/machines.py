"""Machine management endpoints (Phase 3.5 — Management → Machines).

HTTP surface for the Machines management view (GUI_DESIGN §12):
listing active/retired Machines, creation with the automatically
assigned Asset Tag, metadata editing, the explicit maintenance
override, retirement, reactivation of the same physical machine, and
the append-only lifecycle history.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits a server-owned field such
as ``asset_tag``, ``barcode_value``, ``retired_on`` or a maintenance
timestamp is rejected instead of silently ignored), the Application
layer owns every business rule and the transaction, and the central
handlers in ``app.api.errors`` translate typed failures into HTTP
responses.

Deliberate surface decisions:

- ``asset_tag`` and the derived ``barcode_value``
  (``PF:MACHINE:<asset-tag>``) appear only in responses — they are
  never client-writable, and no independent barcode field exists.
- The Area binding is absent from the update schema: the Area of an
  active Machine is fixed; only reactivation may carry a forward-only
  ``area_id`` for a physical machine that moved while retired.
- The maintenance override is its own sub-resource: ``POST`` starts
  it, ``PATCH`` updates note/expected return in place, ``DELETE``
  clears the override (the override is removed — the Machine record
  itself is never deleted, and environment configuration stays
  deactivate-only).
- Retirement and reactivation are explicit lifecycle actions
  (``POST …/retire``, ``POST …/reactivate``), each committing
  atomically with its ``machine_lifecycle_events`` row. The optional
  ``actor`` travels as the nullable, reference-free value Phase 3.5
  defines — authenticated actor identity arrives with Phase 14.
"""

import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.application import machines
from app.application.common import UNSET
from app.infrastructure.models import Machine

router = APIRouter(prefix="/api")


class MachineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    area_id: int
    name: str
    # Immutable, never-reused identity of the physical asset; the
    # barcode is always derived from it.
    asset_tag: str
    barcode_value: str
    description: str | None
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    installed_on: datetime.date | None
    notes: str | None
    maintenance_since: datetime.datetime | None
    maintenance_note: str | None
    maintenance_expected_return: datetime.date | None
    state_changed_at: datetime.datetime
    # NULL = active; set by retirement, cleared by reactivation.
    retired_on: datetime.date | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MachineLifecycleEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    event_type: str
    occurred_at: datetime.datetime
    actor: str | None
    reason: str | None
    before_state: str
    after_state: str
    # Present as a complete previous → current pair only when the
    # physical machine moved while retired (reactivation).
    from_area_id: int | None
    to_area_id: int | None


class MachineCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_id: int
    name: str
    description: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    installed_on: datetime.date | None = None
    notes: str | None = None


class MachineUpdateRequest(BaseModel):
    # No area_id (fixed while active), no asset_tag (immutable), no
    # lifecycle or maintenance fields (own endpoints).
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    installed_on: datetime.date | None = None
    notes: str | None = None


class MaintenanceStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    expected_return: datetime.date | None = None


class MaintenanceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    expected_return: datetime.date | None = None


class MachineRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    actor: str | None = None


class MachineReactivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A reason is required (GUI_DESIGN §12.4); name and area_id default
    # to the current values — area_id only for a machine that
    # physically moved while retired.
    reason: str
    name: str | None = None
    area_id: int | None = None
    actor: str | None = None


def _machine_response(machine: Machine) -> MachineResponse:
    return MachineResponse.model_validate(machine)


@router.get("/machines")
def list_machines(
    session: SessionDep, lifecycle: Literal["active", "retired", "all"] = "all"
) -> list[MachineResponse]:
    return [
        _machine_response(machine)
        for machine in machines.list_machines(session, lifecycle=lifecycle)
    ]


@router.get("/machines/{machine_id}")
def get_machine(machine_id: int, session: SessionDep) -> MachineResponse:
    return _machine_response(machines.get_machine(session, machine_id))


@router.post("/machines", status_code=201)
def create_machine(body: MachineCreateRequest, session: SessionDep) -> MachineResponse:
    machine = machines.create_machine(
        session,
        area_id=body.area_id,
        name=body.name,
        description=body.description,
        manufacturer=body.manufacturer,
        model=body.model,
        serial_number=body.serial_number,
        installed_on=body.installed_on,
        notes=body.notes,
    )
    return _machine_response(machine)


@router.patch("/machines/{machine_id}")
def update_machine(
    machine_id: int, body: MachineUpdateRequest, session: SessionDep
) -> MachineResponse:
    machine = machines.update_machine(session, machine_id, **body.model_dump(exclude_unset=True))
    return _machine_response(machine)


@router.post("/machines/{machine_id}/maintenance", status_code=201)
def start_maintenance(
    machine_id: int, body: MaintenanceStartRequest, session: SessionDep
) -> MachineResponse:
    machine = machines.start_maintenance(
        session, machine_id, note=body.note, expected_return=body.expected_return
    )
    return _machine_response(machine)


@router.patch("/machines/{machine_id}/maintenance")
def update_maintenance(
    machine_id: int, body: MaintenanceUpdateRequest, session: SessionDep
) -> MachineResponse:
    machine = machines.update_maintenance(
        session, machine_id, **body.model_dump(exclude_unset=True)
    )
    return _machine_response(machine)


@router.delete("/machines/{machine_id}/maintenance")
def clear_maintenance(machine_id: int, session: SessionDep) -> MachineResponse:
    return _machine_response(machines.clear_maintenance(session, machine_id))


@router.post("/machines/{machine_id}/retire")
def retire_machine(
    machine_id: int, body: MachineRetireRequest, session: SessionDep
) -> MachineResponse:
    machine = machines.retire_machine(session, machine_id, reason=body.reason, actor=body.actor)
    return _machine_response(machine)


@router.post("/machines/{machine_id}/reactivate")
def reactivate_machine(
    machine_id: int, body: MachineReactivateRequest, session: SessionDep
) -> MachineResponse:
    provided = body.model_dump(exclude_unset=True)
    machine = machines.reactivate_machine(
        session,
        machine_id,
        reason=body.reason,
        # An omitted name keeps the current one; an explicit null is a
        # value error, not a keep — required_text rejects it.
        name=provided.get("name", UNSET),
        area_id=body.area_id,
        actor=body.actor,
    )
    return _machine_response(machine)


@router.get("/machines/{machine_id}/lifecycle-events")
def list_lifecycle_events(
    machine_id: int, session: SessionDep
) -> list[MachineLifecycleEventResponse]:
    return [
        MachineLifecycleEventResponse.model_validate(event)
        for event in machines.list_lifecycle_events(session, machine_id)
    ]
