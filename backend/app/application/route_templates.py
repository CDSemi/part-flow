"""RouteTemplate services: release selection and Planned Routes management.

Phase 4 (GUI_DESIGN §11.4): the release flow lets the user confirm
``PLANNED`` with an existing **active** RouteTemplate —
``list_active_route_templates`` is that read model, unchanged.

Phase 13 slice 8 (PROJECT_PROFILE §8.8–§8.10, §21; GUI_DESIGN §13):
Management → Planned Routes — list (active and archived, with usage),
the usage of one template, create, full replacement of name,
description and the ordered step set, archive and delete. Rules owned
here:

- A template has a name and at least one step; the request order is the
  route order and the server assigns ``sequence = 1..N``. Every step
  needs an Operation (OD-11: a legacy step without one must be completed
  on its next save); an estimated time is empty or longer than zero. A
  step's Area must exist and be active, and step 1 is never in a
  terminal Area (PROJECT_PROFILE §13 — such a route could never be
  released). The Operation and the optional preferred Machine must
  belong to the step's Area, the Operation must be active and the
  Machine not retired. Every step is validated on every effective save,
  so a stale reference stored earlier is never silently kept or cleared
  (GUI_DESIGN §13.2). Names are not unique.
- Editing never touches an Assigned Route: release, receipt and the
  split/merge copy snapshot the steps (PROJECT_PROFILE §8.8, §8.10).
  An ever-used template (any Assigned Route names it as its source) is
  archived, never deleted; a never-used template is deleted, never
  archived (GUI_DESIGN §13.3). Archive is idempotent; there is no
  unarchive and no versioning.
- Lock order. Writers lock the template row first — FOR NO KEY UPDATE
  for an edit or archive, FOR UPDATE for a delete — and then, before
  inserting any step, the referenced Machines → Areas → Operations FOR
  KEY SHARE, each ascending by id (``lock_step_references``): the production
  order (transfer Machine → Area → Operation, Undo Machines then Areas
  ascending, release Area → Operation), so a save never acquires those
  rows in step order. Release and receipt take the template FOR SHARE
  (``lock_template_for_assignment``) before their starting Area, so a
  production command never holds an Area while it waits on a template,
  and a snapshot copies exactly one committed version of the steps.
  They then lock every Area of the snapshot in one ascending pass — the
  starting Area FOR UPDATE at its place, the others FOR KEY SHARE
  (``lock_assignment_areas``) — and its Operations FOR KEY SHARE,
  ascending, before the snapshot INSERT, so two assignments whose routes
  cross Areas in opposite order serialize instead of deadlocking.

Each write commits its own transaction (2xx = committed) and appends
exactly one ``audit_events`` row in it (entity ``RouteTemplate``,
``entity_id`` the id): ``CREATED``, ``UPDATED`` (edit or archive) or
``DELETED``. Rejected writes and no-ops append nothing. Configuration
writes carry no idempotency key: an identical PUT and an archive of an
archived template are no-ops, a repeated DELETE is 404; a retried POST
may create a second never-used template (S8-OD15). Each audit row
carries ``actor_user_id``, the signed-in User; the routes require Manage
Planned Routes (Phase 14 slice 3).
"""

import datetime
from collections.abc import Sequence
from typing import Any, Final, NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import commit, flush, optional_text
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.assigned_route import StepContent
from app.domain.enums import AuditEntityType, AuditEventType, MovementType
from app.infrastructure.models import (
    Area,
    AssignedRoute,
    Machine,
    Operation,
    PartMovement,
    QuantityFlow,
    RouteStep,
    RouteTemplate,
)

# The usage dialog lists at most this many released Quantity Flows
# (newest first) and reports the total.
USAGE_LIST_LIMIT: Final = 200

# The largest id PostgreSQL can bind to an ``integer`` key. An id
# outside 1.._MAX_ID names no row: it is answered as missing before any
# query instead of failing in the driver.
_MAX_ID: Final = 2_147_483_647

# FOR NO KEY UPDATE: the lock an edit's or archive's own UPDATE takes
# anyway, acquired before the audit snapshot so concurrent writers
# serialize and every before_data is the committed predecessor. It
# conflicts with the assignment lock below.
_EDIT_LOCK: Final = {"key_share": True}

# FOR SHARE: release and receipt hold it to COMMIT. It conflicts with
# every template write and never with another assignment of the same
# template.
_ASSIGNMENT_LOCK: Final = {"read": True}

_SOURCE_TEMPLATE_FK: Final = "fk_assigned_routes_source_route_template_id_route_templates"


class RouteTemplateDetail(NamedTuple):
    """One active RouteTemplate with its ordered steps."""

    template: RouteTemplate
    steps: list[RouteStep]


class RouteStepInput(NamedTuple):
    """One requested step; the request order is the route order."""

    area_id: int
    # None only so the OD-11 refusal can be given (an Operation is
    # required on every step of every save).
    operation_id: int | None
    expected_duration: datetime.timedelta | None
    preferred_machine_id: int | None
    instructions: str | None


class RouteTemplateRecord(NamedTuple):
    """The management read model of one template."""

    template: RouteTemplate
    steps: list[RouteStep]
    # An Assigned Route names this template as its source.
    ever_used: bool
    # Released Quantity Flows (see ``route_template_usage``).
    usage_count: int


class RouteTemplateUsageEntry(NamedTuple):
    quantity_flow_id: int
    part_number: str
    # The flow's RECEIVED Movement (the release or receipt instant).
    released_at: datetime.datetime


class RouteTemplateUsage(NamedTuple):
    total: int
    # Newest first, at most USAGE_LIST_LIMIT.
    flows: list[RouteTemplateUsageEntry]


# ---------------------------------------------------------------------------
# Audit snapshot: an explicit field list. Ids of the template (the audit
# entity_id) and of its steps, created_at and updated_at never belong.
# ---------------------------------------------------------------------------


def _iso_instant(value: datetime.datetime | None) -> str | None:
    # UTC, so the text never depends on the connection TimeZone.
    return value.astimezone(datetime.UTC).isoformat() if value is not None else None


def route_template_snapshot(template: RouteTemplate, steps: Sequence[RouteStep]) -> dict[str, Any]:
    return {
        "name": template.name,
        "description": template.description,
        "archived_at": _iso_instant(template.archived_at),
        "steps": [
            {
                "sequence": step.sequence,
                "area_id": step.area_id,
                "operation_id": step.operation_id,
                # Seconds as a JSON number (timedelta is not JSON).
                "expected_duration_seconds": (
                    step.expected_duration.total_seconds()
                    if step.expected_duration is not None
                    else None
                ),
                "preferred_machine_id": step.preferred_machine_id,
                "instructions": step.instructions,
            }
            for step in steps
        ],
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _steps_by_template(session: Session, template_ids: Sequence[int]) -> dict[int, list[RouteStep]]:
    steps_by_template: dict[int, list[RouteStep]] = {
        template_id: [] for template_id in template_ids
    }
    if not template_ids:
        return steps_by_template
    steps = session.scalars(
        select(RouteStep)
        .where(RouteStep.route_template_id.in_(template_ids))
        .order_by(RouteStep.route_template_id, RouteStep.sequence)
    )
    for step in steps:
        steps_by_template[step.route_template_id].append(step)
    return steps_by_template


def list_active_route_templates(session: Session) -> list[RouteTemplateDetail]:
    """The active (non-archived) RouteTemplates, steps in sequence order.

    Ordered by name (then id) for a stable, user-facing selection list.
    Archived templates never appear: a new release can only reference an
    active template (SLICE1_DATA_MODEL §8), while historical PLANNED
    flows keep their own AssignedRoute snapshots (past steps immutable)
    regardless.
    """
    templates = list(
        session.scalars(
            select(RouteTemplate)
            .where(RouteTemplate.archived_at.is_(None))
            .order_by(RouteTemplate.name, RouteTemplate.id)
        )
    )
    steps_by_template = _steps_by_template(session, [template.id for template in templates])
    return [
        RouteTemplateDetail(template=template, steps=steps_by_template[template.id])
        for template in templates
    ]


def _ever_used_ids(session: Session, template_ids: Sequence[int]) -> set[int]:
    if not template_ids:
        return set()
    return {
        template_id
        for template_id in session.scalars(
            select(AssignedRoute.source_route_template_id)
            .where(AssignedRoute.source_route_template_id.in_(template_ids))
            .distinct()
        )
        if template_id is not None
    }


def _usage_counts(session: Session, template_ids: Sequence[int]) -> dict[int, int]:
    """Released Quantity Flows per template: flows whose snapshot names
    the template and that have a RECEIVED Movement (the release and the
    Scan Station receipt both append one; split children and merge
    results carry the provenance but were never released with it)."""
    if not template_ids:
        return {}
    rows = session.execute(
        select(
            AssignedRoute.source_route_template_id,
            func.count(func.distinct(QuantityFlow.id)),
        )
        .join(QuantityFlow, QuantityFlow.assigned_route_id == AssignedRoute.id)
        .join(PartMovement, PartMovement.quantity_flow_id == QuantityFlow.id)
        .where(
            AssignedRoute.source_route_template_id.in_(template_ids),
            PartMovement.movement_type == MovementType.RECEIVED,
        )
        .group_by(AssignedRoute.source_route_template_id)
    )
    return {template_id: count for template_id, count in rows if template_id is not None}


def list_route_templates(session: Session) -> list[RouteTemplateRecord]:
    """Every template, active first, then by name and id; unbounded
    (master data in the tens). Steps, usage and ever-used come from one
    query each — no per-template round trip."""
    templates = list(
        session.scalars(
            select(RouteTemplate).order_by(
                RouteTemplate.archived_at.is_not(None), RouteTemplate.name, RouteTemplate.id
            )
        )
    )
    template_ids = [template.id for template in templates]
    steps_by_template = _steps_by_template(session, template_ids)
    ever_used = _ever_used_ids(session, template_ids)
    usage_counts = _usage_counts(session, template_ids)
    return [
        RouteTemplateRecord(
            template=template,
            steps=steps_by_template[template.id],
            ever_used=template.id in ever_used,
            usage_count=usage_counts.get(template.id, 0),
        )
        for template in templates
    ]


def route_template_usage(session: Session, template_id: int) -> RouteTemplateUsage:
    """The Quantity Flows released with the template (PROJECT_PROFILE
    §21): newest RECEIVED first, at most ``USAGE_LIST_LIMIT``, plus the
    total. Works for archived templates."""
    _get_template(session, template_id)
    released_at = func.min(PartMovement.occurred_at).label("released_at")
    released = (
        select(QuantityFlow.id, QuantityFlow.part_number, released_at)
        .join(AssignedRoute, AssignedRoute.id == QuantityFlow.assigned_route_id)
        .join(PartMovement, PartMovement.quantity_flow_id == QuantityFlow.id)
        .where(
            AssignedRoute.source_route_template_id == template_id,
            PartMovement.movement_type == MovementType.RECEIVED,
        )
        .group_by(QuantityFlow.id, QuantityFlow.part_number)
    ).subquery()
    total = session.scalar(select(func.count()).select_from(released)) or 0
    rows = session.execute(
        select(released.c.id, released.c.part_number, released.c.released_at)
        .order_by(released.c.released_at.desc(), released.c.id.desc())
        .limit(USAGE_LIST_LIMIT)
    )
    return RouteTemplateUsage(
        total=total,
        flows=[
            RouteTemplateUsageEntry(
                quantity_flow_id=flow_id, part_number=part_number, released_at=moment
            )
            for flow_id, part_number, moment in rows
        ],
    )


def lock_template_for_assignment(session: Session, template_id: int) -> RouteTemplate | None:
    """The template row FOR SHARE until COMMIT, RE-READ under the lock.

    Taken by release and receipt before their starting Area row lock: a
    concurrent edit, archive or delete is either fully visible to the
    snapshot or waits for it. ``populate_existing`` matters: a template
    already in the identity map would otherwise come back stale.
    """
    return session.get(
        RouteTemplate, template_id, with_for_update=_ASSIGNMENT_LOCK, populate_existing=True
    )


def assignment_steps(session: Session, template_id: int) -> list[RouteStep]:
    """The template's steps in route order, read under the assignment lock."""
    return _template_steps(session, template_id)


def lock_assignment_areas(
    session: Session, starting_area_id: int, steps: Sequence[RouteStep]
) -> Area | None:
    """The starting Area FOR UPDATE and the other step Areas FOR KEY SHARE,
    all ascending by id; the starting Area RE-READ under its lock.

    The snapshot INSERT's FK checks take FOR KEY SHARE on every step Area
    in step order, which conflicts with the FOR UPDATE another release or
    receipt holds on its own starting Area: two assignments whose routes
    cross Areas in opposite order would each hold one Area and wait on
    the other. Taking every Area here in ONE ascending pass — before any
    Operation row — leaves the FK checks only re-requesting held locks.
    """
    others = {step.area_id for step in steps} - {starting_area_id}
    _locked_rows(session, Area, {area_id for area_id in others if area_id < starting_area_id})
    area = session.get(Area, starting_area_id, with_for_update=True, populate_existing=True)
    _locked_rows(session, Area, {area_id for area_id in others if area_id > starting_area_id})
    return area


def lock_assignment_operations(session: Session, operation_ids: set[int]) -> None:
    """The Operations FOR KEY SHARE, ascending by id, after every Area lock.

    The lock the snapshot (and Movement) INSERT's FK checks take anyway,
    acquired in id order instead of step order.
    """
    _locked_rows(session, Operation, operation_ids)


# ---------------------------------------------------------------------------
# Input shape (no database)
# ---------------------------------------------------------------------------


def _route_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError("A route name is required.")
    if "\x00" in value:
        # PostgreSQL text cannot hold NUL; refuse it before the driver.
        raise InvalidInputError("The route name must be text.")
    return value.strip()


def _route_description(value: object) -> str | None:
    if value is not None and (not isinstance(value, str) or "\x00" in value):
        raise InvalidInputError("The route description must be text.")
    return optional_text(value)


def shape_route_steps(
    steps: Sequence[RouteStepInput], *, allow_empty: bool, first_number: int = 1
) -> list[StepContent]:
    """The step rules that need no database, per step in request order.

    Templates require at least one step (``allow_empty=False``); an
    AssignedRoute adjustment may leave no future step. ``first_number``
    is the absolute number of the first step in the messages (an
    adjustment's tail continues the kept route's numbering).
    """
    if not steps and not allow_empty:
        raise InvalidInputError("A Planned Route needs at least one step.")
    shaped: list[StepContent] = []
    for number, step in enumerate(steps, start=first_number):
        if step.operation_id is None:
            raise InvalidInputError(f"Step {number} needs an Operation.")
        duration = step.expected_duration
        if duration is not None and duration <= datetime.timedelta(0):
            raise InvalidInputError(f"Step {number}: the estimated time must be longer than zero.")
        instructions = step.instructions
        if instructions is not None and "\x00" in instructions:
            raise InvalidInputError(f"Step {number}: the instructions must be text.")
        shaped.append(
            StepContent(
                area_id=step.area_id,
                operation_id=step.operation_id,
                expected_duration=duration,
                preferred_machine_id=step.preferred_machine_id,
                instructions=optional_text(instructions),
            )
        )
    return shaped


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def _locked_rows[Row: (Area, Operation, Machine)](
    session: Session, model: type[Row], ids: set[int]
) -> dict[int, Row]:
    """The rows FOR KEY SHARE, ascending by id, re-read under the lock.

    Exactly the lock the step INSERT's FK checks take, acquired in the
    production order beforehand; it never conflicts with admin edits
    (FOR NO KEY UPDATE) or child-write parent locks (FOR SHARE).
    """
    # An id PostgreSQL cannot bind names no row: it stays absent (missing).
    ids = {row_id for row_id in ids if 0 < row_id <= _MAX_ID}
    if not ids:
        return {}
    rows = session.scalars(
        select(model)
        .where(model.id.in_(ids))
        .order_by(model.id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    )
    return {row.id: row for row in rows}


def lock_step_references(
    session: Session, steps: Sequence[StepContent], *, first_number: int = 1
) -> None:
    """Lock the referenced rows in the production order, then validate.

    ``first_number`` is the absolute number of the first step (messages
    and the step-1 terminal rule, which applies only to step 1 itself).

    FOR KEY SHARE on the preferred Machines, then the Areas, then the
    Operations, each ascending by id, before any step INSERT — whose FK
    checks then only re-request locks already held, so this writer never
    acquires those rows in step order (no deadlock against a transfer
    or Undo). The locks exist for ordering, not for an invariant: a
    deactivation or retirement committing right after the save yields
    the legal "saved, then changed" state the editor shows
    ``(unavailable)``. Checks run per step Area → Operation → Machine;
    the first failing step is reported and nothing is written.
    """
    machines = _locked_rows(
        session,
        Machine,
        {step.preferred_machine_id for step in steps if step.preferred_machine_id is not None},
    )
    areas = _locked_rows(session, Area, {step.area_id for step in steps})
    operations = _locked_rows(
        session,
        Operation,
        {step.operation_id for step in steps if step.operation_id is not None},
    )
    for number, step in enumerate(steps, start=first_number):
        area = areas.get(step.area_id)
        if area is None:
            raise InvalidInputError(f"Step {number}: Area {step.area_id} does not exist.")
        if not area.is_active:
            raise ConflictError(
                f"Step {number}: Area '{area.name}' is inactive. Choose an active Area."
            )
        if number == 1 and area.is_terminal:
            raise ConflictError(
                f"Step 1: Area '{area.name}' is a terminal Area and never starts production."
                " Choose a starting Area for the first step."
            )
        operation = operations.get(step.operation_id) if step.operation_id is not None else None
        if operation is None:
            raise InvalidInputError(f"Step {number}: Operation {step.operation_id} does not exist.")
        if operation.area_id != area.id:
            raise InvalidInputError(
                f"Step {number}: Operation '{operation.code}' does not belong to"
                f" Area '{area.name}'."
            )
        if not operation.is_active:
            raise ConflictError(
                f"Step {number}: Operation '{operation.code}' is inactive."
                " Choose an Operation the Area still offers."
            )
        if step.preferred_machine_id is None:
            continue
        machine = machines.get(step.preferred_machine_id)
        if machine is None:
            raise InvalidInputError(
                f"Step {number}: Machine {step.preferred_machine_id} does not exist."
            )
        if machine.area_id != area.id:
            raise InvalidInputError(
                f"Step {number}: Machine '{machine.name}' is not in Area '{area.name}'."
                " Choose one of the Area's active Machines or no preferred Machine."
            )
        if machine.retired_on is not None:
            raise ConflictError(
                f"Step {number}: Machine '{machine.name}' is retired."
                " Choose an active Machine or no preferred Machine."
            )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _template_steps(session: Session, template_id: int) -> list[RouteStep]:
    return list(
        session.scalars(
            select(RouteStep)
            .where(RouteStep.route_template_id == template_id)
            .order_by(RouteStep.sequence)
        )
    )


def _add_steps(session: Session, template_id: int, steps: Sequence[StepContent]) -> list[RouteStep]:
    rows = [
        RouteStep(
            route_template_id=template_id,
            sequence=sequence,
            area_id=step.area_id,
            operation_id=step.operation_id,
            expected_duration=step.expected_duration,
            preferred_machine_id=step.preferred_machine_id,
            instructions=step.instructions,
        )
        for sequence, step in enumerate(steps, start=1)
    ]
    session.add_all(rows)
    return rows


def _usage_count(session: Session, template_id: int) -> int:
    return _usage_counts(session, [template_id]).get(template_id, 0)


def _is_ever_used(session: Session, template_id: int) -> bool:
    return bool(_ever_used_ids(session, [template_id]))


def _commit_record(
    session: Session,
    template: RouteTemplate,
    steps: list[RouteStep],
    *,
    ever_used: bool,
    usage_count: int,
) -> RouteTemplateRecord:
    """Commit and return the record exactly as this transaction left it.

    The route builds its response after the commit; attached rows would
    be expired by it and reloaded in a new transaction, where a delete
    committed in between would fail the reload although this write is
    committed. So the template is re-read under the lock this
    transaction holds and every row is detached before the commit.
    """
    session.refresh(template)
    session.expunge(template)
    for step in steps:
        session.expunge(step)
    commit(session, {})
    return RouteTemplateRecord(template, steps, ever_used, usage_count)


def _get_template(
    session: Session, template_id: int, *, lock: bool | dict[str, bool] = False
) -> RouteTemplate:
    """The template (RE-READ under ``lock`` when given), else 404."""
    template = (
        session.get(RouteTemplate, template_id, with_for_update=lock, populate_existing=True)
        if 0 < template_id <= _MAX_ID
        else None
    )
    if template is None:
        raise NotFoundError(f"Planned Route {template_id} does not exist.")
    return template


def _lock_for_edit(session: Session, template_id: int) -> RouteTemplate:
    return _get_template(session, template_id, lock=_EDIT_LOCK)


def create_route_template(
    session: Session,
    *,
    name: object,
    description: object,
    steps: Sequence[RouteStepInput],
    actor_user_id: int,
) -> RouteTemplateRecord:
    """Create an active, never-used template with its ordered steps."""
    route_name = _route_name(name)
    route_description = _route_description(description)
    shaped = shape_route_steps(steps, allow_empty=False)
    lock_step_references(session, shaped)
    template = RouteTemplate(name=route_name, description=route_description)
    session.add(template)
    flush(session, {})
    rows = _add_steps(session, template.id, shaped)
    flush(session, {})
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.ROUTE_TEMPLATE,
        entity_id=str(template.id),
        before_data=None,
        after_data=route_template_snapshot(template, rows),
        actor_user_id=actor_user_id,
    )
    return _commit_record(session, template, rows, ever_used=False, usage_count=0)


def replace_route_template(
    session: Session,
    template_id: int,
    *,
    name: object,
    description: object,
    steps: Sequence[RouteStepInput],
    actor_user_id: int,
) -> RouteTemplateRecord:
    """Replace name, description and the whole step set (full PUT).

    An identical request is a no-op judged BEFORE any reference check:
    nothing is written, so a retry of a committed save stays a no-op
    even if a referenced row changed meanwhile. Step ids change on
    every effective edit — nothing references ``route_steps.id``.
    Assigned Routes are never touched.
    """
    route_name = _route_name(name)
    route_description = _route_description(description)
    shaped = shape_route_steps(steps, allow_empty=False)
    template = _lock_for_edit(session, template_id)
    if template.archived_at is not None:
        raise ConflictError(
            f"Planned Route '{template.name}' is archived and cannot be edited."
            " Duplicate it to create an editable copy."
        )
    current = _template_steps(session, template.id)
    current_state = [
        StepContent(
            area_id=step.area_id,
            operation_id=step.operation_id,
            expected_duration=step.expected_duration,
            preferred_machine_id=step.preferred_machine_id,
            instructions=step.instructions,
        )
        for step in current
    ]
    if (route_name, route_description, shaped) == (
        template.name,
        template.description,
        current_state,
    ):
        return _commit_record(
            session,
            template,
            current,
            ever_used=_is_ever_used(session, template.id),
            usage_count=_usage_count(session, template.id),
        )
    lock_step_references(session, shaped)
    before = route_template_snapshot(template, current)
    session.execute(delete(RouteStep).where(RouteStep.route_template_id == template.id))
    # UNIQUE (route_template_id, sequence) is not deferrable: the old
    # rows must be gone before the new ones are inserted.
    flush(session, {})
    rows = _add_steps(session, template.id, shaped)
    template.name = route_name
    template.description = route_description
    template.updated_at = func.now()
    flush(session, {})
    session.refresh(template)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.ROUTE_TEMPLATE,
        entity_id=str(template.id),
        before_data=before,
        after_data=route_template_snapshot(template, rows),
        actor_user_id=actor_user_id,
    )
    return _commit_record(
        session,
        template,
        rows,
        ever_used=_is_ever_used(session, template.id),
        usage_count=_usage_count(session, template.id),
    )


def archive_route_template(
    session: Session, template_id: int, *, actor_user_id: int
) -> RouteTemplateRecord:
    """Archive an ever-used template; already archived is a no-op.

    Ever-used is read under the edit lock, which a release or receipt
    of the template (FOR SHARE) waits for or has already committed.
    """
    template = _lock_for_edit(session, template_id)
    steps = _template_steps(session, template.id)
    usage_count = _usage_count(session, template.id)
    ever_used = _is_ever_used(session, template.id)
    if template.archived_at is not None:
        return _commit_record(
            session, template, steps, ever_used=ever_used, usage_count=usage_count
        )
    if not ever_used:
        raise ConflictError(
            f"Planned Route '{template.name}' has never been used by a released"
            " Quantity Flow. Delete it instead of archiving it."
        )
    before = route_template_snapshot(template, steps)
    template.archived_at = func.now()
    template.updated_at = func.now()
    flush(session, {})
    session.refresh(template)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.ROUTE_TEMPLATE,
        entity_id=str(template.id),
        before_data=before,
        after_data=route_template_snapshot(template, steps),
        actor_user_id=actor_user_id,
    )
    return _commit_record(session, template, steps, ever_used=True, usage_count=usage_count)


def delete_route_template(session: Session, template_id: int, *, actor_user_id: int) -> None:
    """Delete a never-used template and its steps.

    The row is locked FOR UPDATE (the DELETE's own mode): a release or
    receipt holding the template FOR SHARE commits first and makes it
    used, or waits and then finds it gone. The source FK is the backstop.
    """
    template = _get_template(session, template_id, lock=True)
    used_message = (
        f"Planned Route '{template.name}' has been used by released Quantity Flows,"
        " so it cannot be deleted. Archive it instead."
    )
    if _is_ever_used(session, template.id):
        raise ConflictError(used_message)
    before = route_template_snapshot(template, _template_steps(session, template.id))
    session.execute(delete(RouteStep).where(RouteStep.route_template_id == template.id))
    session.delete(template)
    conflicts = {_SOURCE_TEMPLATE_FK: used_message}
    flush(session, conflicts)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.DELETED,
        entity_type=AuditEntityType.ROUTE_TEMPLATE,
        entity_id=str(template_id),
        before_data=before,
        after_data=None,
        actor_user_id=actor_user_id,
    )
    commit(session, conflicts)
