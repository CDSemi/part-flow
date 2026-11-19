"""Viewing an archive (Phase 16, history archival design D16 and §9.3; build step 5).

HTTP surface of Open for viewing, Close viewing and the viewer page of
Administration → History archival & purge. Routes stay thin: request schemas
validate shape only (``extra="forbid"``), the Application layer
(``app.application.history_archive_views``) owns every rule, audit row and
transaction, and the central handlers in ``app.api.errors`` translate typed
failures (404 unknown view, run, flow or Movement; 409 conflict; 422
invalid).

- ``GET /history-archive/views?limit=N`` — the newest views first (default
  50, 1-500), each with what its run exported (what a load brings into the
  database) and, once open, what it loaded and until when it stays open.
- ``POST /history-archive/views`` — ``{"run_id": N}``: open a finished run's
  verified archive for viewing; 201 with the view (``REQUESTED``). The host
  job loads it within minutes (``LOADING``, then ``OPEN``).
- ``POST /history-archive/views/{view_id}/cancel`` — cancel a ``REQUESTED``
  view (a cancelled view answers unchanged).
- ``POST /history-archive/views/{view_id}/close`` — Close viewing: an
  ``OPEN`` view becomes ``CLOSING`` and the host job removes its rows (a
  closing or closed view answers unchanged).
- ``GET /history-archive/views/{view_id}/movements`` — search an ``OPEN``
  view's Movements by Part Number, Work Order Number, flow, Area, Operation,
  Machine, Worker and site-calendar days, newest first; ``before`` names the
  last Movement of the previous page.
- ``GET /history-archive/views/{view_id}/flows/{flow_id}`` — one flow's whole
  archived history with its carry-forward record, lineage and route.
- ``GET /history-archive/views/{view_id}/names`` — the Areas, Operations,
  Machines and Workers to search by, as they were at export.

Every route needs ``RUN_HISTORY_ARCHIVAL_AND_PURGE``; each audit row names
the signed-in User. Every name the viewer answers comes from the archive's
``context.json``, never from live master data.
"""

import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy.orm import Session

from app.api.authorization import RequirePermission
from app.api.dependencies import SessionDep
from app.application import history_archive_views
from app.application.authentication import Principal
from app.application.history_archive_views import (
    DemandRef,
    MovementFilters,
    NameRef,
    OperationRef,
    ViewEntry,
    ViewerMovement,
    ViewUser,
)
from app.domain.enums import Permission
from app.infrastructure.models import HistoryArchiveView

router = APIRouter(prefix="/api")

ArchiveViewerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE))
]


class UserRef(BaseModel):
    user_id: int
    display_name: str


class ViewCounts(BaseModel):
    """Rows of a loaded archive, per kind."""

    movements: int
    flows: int
    lineage_edges: int
    assigned_routes: int
    names: int


class ViewRunExport(BaseModel):
    """What the view's run exported: what a load brings into the database."""

    archive_name: str | None
    movements: int
    flows: int
    lineage_edges: int
    assigned_routes: int
    archive_bytes: int


class ViewResponse(BaseModel):
    id: int
    run_id: int
    # REQUESTED, LOADING, OPEN, CLOSING; CLOSED, FAILED, CANCELLED.
    state: str
    requested_by: UserRef
    requested_at: datetime.datetime
    # What the run exported: what a load brings into the database.
    to_load: ViewRunExport
    started_at: datetime.datetime | None
    opened_at: datetime.datetime | None
    # When its days run out (the setting as it stands): the host job then
    # removes its rows. Null until it opened.
    open_until: datetime.datetime | None
    loaded: ViewCounts | None
    closing_started_at: datetime.datetime | None
    # The person who closed it; null when its days ran out.
    closed_by: UserRef | None
    cancelled_by: UserRef | None
    finished_at: datetime.datetime | None
    failure_reason: str | None


class ViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: StrictInt


class NameRefResponse(BaseModel):
    """A named record of the archive as it was at export; ``name`` null when the
    archive does not name it."""

    id: int
    name: str | None


class OperationRefResponse(BaseModel):
    id: int
    code: str | None
    name: str | None


class ArchivedUserResponse(BaseModel):
    user_id: int
    display_name: str | None


class DemandRefResponse(BaseModel):
    work_order_demand_id: int
    work_order_id: int | None
    work_order_number: str | None
    request_type: str | None


class WorkOrderRefResponse(BaseModel):
    work_order_id: int
    work_order_number: str | None


class LineageRefResponse(BaseModel):
    parent_flow_id: int
    child_flow_id: int
    relation: str
    # The split or merge was undone.
    void: bool


class StepRefResponse(BaseModel):
    id: int
    sequence: int | None


class ViewerMovementResponse(BaseModel):
    id: int
    quantity_flow_id: int
    part_number: str
    movement_type: str
    quantity: int
    from_area: NameRefResponse | None
    to_area: NameRefResponse
    operation: OperationRefResponse
    source_machine: NameRefResponse | None
    destination_machine: NameRefResponse | None
    station_id: str | None
    worker: NameRefResponse | None
    occurred_at: datetime.datetime
    device_event_id: str
    command_sequence: int
    movement_reason: str | None
    reason: str | None
    # Set on a REVERSED row: the original it undoes.
    reverses_movement_id: int | None
    # Set on an original that was undone: the REVERSED row.
    reversed_by_movement_id: int | None
    assigned_route_step: StepRefResponse | None
    route_deviation: dict[str, Any] | None
    lineage: list[LineageRefResponse]
    # The demand line a RECEIVED released (with its Work Order).
    demand: DemandRefResponse | None
    # The signed-in User of a Management command.
    actor_user: ArchivedUserResponse | None


class MovementSearchResponse(BaseModel):
    movements: list[ViewerMovementResponse]
    # The Movements of the view that match the search.
    total: int
    has_more: bool
    # Pass as `before` for the next (older) page; null on the last page.
    next_before_movement_id: int | None


class FlowLinkResponse(BaseModel):
    quantity_flow_id: int
    relation: str
    void: bool


class RouteStepResponse(BaseModel):
    id: int
    sequence: int
    area: NameRefResponse
    operation: OperationRefResponse | None
    preferred_machine: NameRefResponse | None
    expected_duration: str | None
    instructions: str | None


class RouteResponse(BaseModel):
    assigned_route_id: int
    source_route_template_id: int | None
    created_at: datetime.datetime | None
    steps: list[RouteStepResponse]


class ViewerFlowResponse(BaseModel):
    id: int
    part_number: str
    part_number_name: str | None
    component_id: int
    quantity: int
    status: str
    route_mode: str
    created_at: datetime.datetime
    closed_at: datetime.datetime | None
    # The carry-forward record exactly as the archive holds it.
    record: dict[str, Any]
    stocked_area: NameRefResponse | None
    scrapped_area: NameRefResponse | None
    demand: DemandRefResponse | None
    # The Work Orders it was released for, directly or through the flows it
    # was split or merged from.
    work_orders: list[WorkOrderRefResponse]
    parents: list[FlowLinkResponse]
    children: list[FlowLinkResponse]
    route: RouteResponse | None
    # Its whole archived history, newest first.
    movements: list[ViewerMovementResponse]


class ViewNamesResponse(BaseModel):
    areas: list[NameRefResponse]
    operations: list[OperationRefResponse]
    machines: list[NameRefResponse]
    workers: list[NameRefResponse]


def _user(value: ViewUser | None) -> UserRef | None:
    return (
        None if value is None else UserRef(user_id=value.user_id, display_name=value.display_name)
    )


def _view_response(entry: ViewEntry) -> ViewResponse:
    view = entry.view
    loaded = (
        None
        if view.opened_at is None
        else ViewCounts(
            movements=view.loaded_movements or 0,
            flows=view.loaded_flows or 0,
            lineage_edges=view.loaded_lineage_edges or 0,
            assigned_routes=view.loaded_assigned_routes or 0,
            names=view.loaded_names or 0,
        )
    )
    return ViewResponse(
        id=view.id,
        run_id=view.run_id,
        state=view.state,
        requested_by=UserRef(
            user_id=entry.requested_by.user_id, display_name=entry.requested_by.display_name
        ),
        requested_at=view.requested_at,
        to_load=ViewRunExport(**entry.run._asdict()),
        started_at=view.started_at,
        opened_at=view.opened_at,
        open_until=entry.open_until,
        loaded=loaded,
        closing_started_at=view.closing_started_at,
        closed_by=_user(entry.closed_by),
        cancelled_by=_user(entry.cancelled_by),
        finished_at=view.finished_at,
        failure_reason=view.failure_reason,
    )


def _answer(session: Session, view: HistoryArchiveView) -> ViewResponse:
    return _view_response(history_archive_views.view_entry(session, view))


def _name(value: NameRef | None) -> NameRefResponse | None:
    return None if value is None else NameRefResponse(id=value.id, name=value.name)


def _operation(value: OperationRef | None) -> OperationRefResponse | None:
    return (
        None
        if value is None
        else OperationRefResponse(id=value.id, code=value.code, name=value.name)
    )


def _demand(value: DemandRef | None) -> DemandRefResponse | None:
    return None if value is None else DemandRefResponse(**value._asdict())


def _movement(entry: ViewerMovement) -> ViewerMovementResponse:
    to_area = _name(entry.to_area)
    operation = _operation(entry.operation)
    assert to_area is not None and operation is not None
    return ViewerMovementResponse(
        id=entry.id,
        quantity_flow_id=entry.quantity_flow_id,
        part_number=entry.part_number,
        movement_type=entry.movement_type,
        quantity=entry.quantity,
        from_area=_name(entry.from_area),
        to_area=to_area,
        operation=operation,
        source_machine=_name(entry.source_machine),
        destination_machine=_name(entry.destination_machine),
        station_id=entry.station_id,
        worker=_name(entry.worker),
        occurred_at=entry.occurred_at,
        device_event_id=entry.device_event_id,
        command_sequence=entry.command_sequence,
        movement_reason=entry.movement_reason,
        reason=entry.reason,
        reverses_movement_id=entry.reverses_movement_id,
        reversed_by_movement_id=entry.reversed_by_movement_id,
        assigned_route_step=(
            None
            if entry.assigned_route_step is None
            else StepRefResponse(**entry.assigned_route_step._asdict())
        ),
        route_deviation=entry.route_deviation,
        lineage=[LineageRefResponse(**edge._asdict()) for edge in entry.lineage],
        demand=_demand(entry.demand),
        actor_user=(
            None if entry.actor_user is None else ArchivedUserResponse(**entry.actor_user._asdict())
        ),
    )


@router.get("/history-archive/views")
def get_views(
    principal: ArchiveViewerDep,
    session: SessionDep,
    limit: Annotated[int, Query()] = history_archive_views.DEFAULT_VIEW_LIMIT,
) -> list[ViewResponse]:
    return [
        _view_response(entry) for entry in history_archive_views.list_views(session, limit=limit)
    ]


@router.post("/history-archive/views", status_code=201)
def post_view(principal: ArchiveViewerDep, body: ViewRequest, session: SessionDep) -> ViewResponse:
    view = history_archive_views.request_view(
        session, run_id=body.run_id, actor_user_id=principal.user_id
    )
    return _answer(session, view)


@router.post("/history-archive/views/{view_id}/cancel")
def post_view_cancel(
    principal: ArchiveViewerDep, view_id: int, session: SessionDep
) -> ViewResponse:
    view = history_archive_views.cancel_view(session, view_id, actor_user_id=principal.user_id)
    return _answer(session, view)


@router.post("/history-archive/views/{view_id}/close")
def post_view_close(principal: ArchiveViewerDep, view_id: int, session: SessionDep) -> ViewResponse:
    view = history_archive_views.close_view(session, view_id, actor_user_id=principal.user_id)
    return _answer(session, view)


@router.get("/history-archive/views/{view_id}/movements")
def get_view_movements(
    principal: ArchiveViewerDep,
    session: SessionDep,
    view_id: int,
    part_number: str | None = None,
    work_order_number: str | None = None,
    quantity_flow_id: int | None = None,
    area_id: int | None = None,
    operation_id: int | None = None,
    machine_id: int | None = None,
    worker_id: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    before: int | None = None,
    limit: int = Query(
        history_archive_views.DEFAULT_MOVEMENT_LIMIT,
        ge=1,
        le=history_archive_views.MAX_MOVEMENT_LIMIT,
    ),
) -> MovementSearchResponse:
    """One page of an open view's Movements matching the search, newest first
    (``(occurred_at DESC, id DESC)``); ``before`` names the last Movement of
    the previous page."""
    page = history_archive_views.search_movements(
        session,
        view_id,
        MovementFilters(
            part_number=part_number,
            work_order_number=work_order_number,
            quantity_flow_id=quantity_flow_id,
            area_id=area_id,
            operation_id=operation_id,
            machine_id=machine_id,
            worker_id=worker_id,
            date_from=date_from,
            date_to=date_to,
        ),
        before_movement_id=before,
        limit=limit,
    )
    return MovementSearchResponse(
        movements=[_movement(entry) for entry in page.movements],
        total=page.total,
        has_more=page.has_more,
        next_before_movement_id=page.next_before_movement_id,
    )


@router.get("/history-archive/views/{view_id}/flows/{flow_id}")
def get_view_flow(
    principal: ArchiveViewerDep, session: SessionDep, view_id: int, flow_id: int
) -> ViewerFlowResponse:
    flow = history_archive_views.flow_history(session, view_id, flow_id)
    return ViewerFlowResponse(
        id=flow.id,
        part_number=flow.part_number,
        part_number_name=flow.part_number_name,
        component_id=flow.component_id,
        quantity=flow.quantity,
        status=flow.status,
        route_mode=flow.route_mode,
        created_at=flow.created_at,
        closed_at=flow.closed_at,
        record=flow.record,
        stocked_area=_name(flow.stocked_area),
        scrapped_area=_name(flow.scrapped_area),
        demand=_demand(flow.demand),
        work_orders=[WorkOrderRefResponse(**ref._asdict()) for ref in flow.work_orders],
        parents=[FlowLinkResponse(**link._asdict()) for link in flow.parents],
        children=[FlowLinkResponse(**link._asdict()) for link in flow.children],
        route=(
            None
            if flow.route is None
            else RouteResponse(
                assigned_route_id=flow.route.assigned_route_id,
                source_route_template_id=flow.route.source_route_template_id,
                created_at=flow.route.created_at,
                steps=[
                    RouteStepResponse(
                        id=step.id,
                        sequence=step.sequence,
                        area=NameRefResponse(id=step.area.id, name=step.area.name),
                        operation=_operation(step.operation),
                        preferred_machine=_name(step.preferred_machine),
                        expected_duration=step.expected_duration,
                        instructions=step.instructions,
                    )
                    for step in flow.route.steps
                ],
            )
        ),
        movements=[_movement(entry) for entry in flow.movements],
    )


@router.get("/history-archive/views/{view_id}/names")
def get_view_names(
    principal: ArchiveViewerDep, session: SessionDep, view_id: int
) -> ViewNamesResponse:
    names = history_archive_views.view_names(session, view_id)
    return ViewNamesResponse(
        areas=[NameRefResponse(id=ref.id, name=ref.name) for ref in names.areas],
        operations=[
            OperationRefResponse(id=ref.id, code=ref.code, name=ref.name)
            for ref in names.operations
        ],
        machines=[NameRefResponse(id=ref.id, name=ref.name) for ref in names.machines],
        workers=[NameRefResponse(id=ref.id, name=ref.name) for ref in names.workers],
    )
