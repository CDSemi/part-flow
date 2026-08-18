"""Machine management services (Phase 3.5 — Management → Machines).

Application-layer operations behind the Machines management view
(GUI_DESIGN §12) within the Phase 3.5 minimum environment setup:
listing, creation with automatic Asset Tag assignment, metadata
editing, the explicit maintenance override, retirement, reactivation
of the same physical machine, and the append-only lifecycle history.

Rules owned here (PROJECT_PROFILE §7 Machine, §8.6, §10;
IMPLEMENTATION_ROADMAP Phase 3.5):

- The Asset Tag is assigned automatically at creation from the
  configured format (prefix + zero-padded numeric sequence) and is
  never entered or edited. Allocation is one atomic
  ``UPDATE … RETURNING`` on the persisted ``next_sequence`` counter,
  so concurrent creations serialize on the configuration row and a
  sequence number is never issued twice. ``digits`` is a minimum
  width: a sequence that outgrows it renders unpadded, never
  truncated. The Machine barcode is always derived
  (``PF:MACHINE:<asset-tag>``) — no barcode is stored or accepted.
- Display names are unique among the ACTIVE Machines of one Area;
  reuse across time and replacements stays allowed.
- The Area of an active Machine is fixed (moving capacity is a
  replacement); metadata editing never touches it. A retired record is
  never renamed or mutated — reactivation is the only door back.
- Maintenance is an explicit override: starting it changes the derived
  operational state (so ``state_changed_at`` resets), updating the
  note/expected return while it is active changes neither the start
  time nor the state, and clearing it resets ``state_changed_at``
  again. In Phase 3.5 the derived state is Maintenance-else-Idle —
  Running requires assignment data that arrives with Phase 6.
- Retirement and reactivation commit atomically with their
  ``machine_lifecycle_events`` row: one transaction, no lifecycle
  change without its event and no event without its change. Events are
  append-only (database trigger) and carry a nullable, reference-free
  actor — no Worker/User linkage in Phase 3.5.
- Retirement while active quantity is assigned is blocked by the
  production workflow (PROJECT_PROFILE §8.6). Assignment persistence
  (``current_machine_id``) arrives with Phase 6, so in Phase 3.5 no
  assignment can exist and the blocker has nothing to check yet — the
  rule gains its data-backed enforcement in Phase 6.
- Reactivation may move the Machine forward-only to another active
  Area when the physical machine moved while retired; the lifecycle
  event records the previous → current pair. It is blocked when the
  serial number has meanwhile been reissued to another active Machine
  (a data problem to resolve first — Asset Tag reissue is structurally
  impossible through ``uq_machines_asset_tag``), and when the display
  name would collide with an active Machine of the target Area.

Each mutating service commits its own transaction: a 2xx response
always reflects committed state, and a concurrent uniqueness race lost
at COMMIT surfaces as the same ``ConflictError`` as a pre-checked
duplicate.
"""

import datetime
from typing import Final, Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.application.common import (
    UNSET,
    UnsetType,
    commit,
    optional_text,
    required_text,
)
from app.application.environment import require_active_area
from app.application.errors import ConflictError, NotFoundError
from app.domain.enums import MachineLifecycleEventType, MachineLifecycleState
from app.infrastructure.models import (
    Area,
    Machine,
    MachineAssetTagConfig,
    MachineLifecycleEvent,
)

_MACHINE_ASSET_TAG_CONFIG_ID: Final = 1

LifecycleFilter = Literal["active", "retired", "all"]

_MACHINE_CONFLICTS: Final = {
    "uq_machines_area_id_name_active": (
        "The Area already has an active Machine with this name."
        " Display names must be unique among the active Machines of one Area."
    ),
    "uq_machines_asset_tag": (
        "The allocated Asset Tag is already in use by an existing Machine."
        " This indicates an inconsistent Asset Tag counter — review the"
        " Barcode configuration before creating Machines."
    ),
}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_machines(session: Session, *, lifecycle: LifecycleFilter = "all") -> list[Machine]:
    query = select(Machine).order_by(Machine.name, Machine.id)
    if lifecycle == "active":
        query = query.where(Machine.retired_on.is_(None))
    elif lifecycle == "retired":
        query = query.where(Machine.retired_on.is_not(None))
    return list(session.scalars(query))


def get_machine(session: Session, machine_id: int) -> Machine:
    machine = session.get(Machine, machine_id)
    if machine is None:
        raise NotFoundError(f"Machine {machine_id} does not exist.")
    return machine


def list_lifecycle_events(session: Session, machine_id: int) -> list[MachineLifecycleEvent]:
    """Append-only lifecycle history of one Machine, oldest first."""
    machine = get_machine(session, machine_id)
    return list(
        session.scalars(
            select(MachineLifecycleEvent)
            .where(MachineLifecycleEvent.machine_id == machine.id)
            .order_by(MachineLifecycleEvent.id)
        )
    )


# ---------------------------------------------------------------------------
# Creation — automatic Asset Tag assignment
# ---------------------------------------------------------------------------


def _reject_duplicate_active_name(
    session: Session, area: Area, name: str, exclude_id: int | None = None
) -> None:
    query = (
        select(Machine.id)
        .where(
            Machine.area_id == area.id,
            Machine.name == name,
            Machine.retired_on.is_(None),
        )
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(Machine.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"'{name}' already exists as an active Machine in Area '{area.name}'.")


def _allocate_asset_tag(session: Session) -> str:
    """Consume the next sequence number and render the Asset Tag.

    One atomic ``UPDATE … RETURNING`` increments the persisted
    never-reuse counter and returns the format with it; the row lock it
    takes serializes concurrent Machine creations until COMMIT, and a
    rolled-back creation returns its unissued number with the
    transaction. RETURNING yields the post-increment counter, so the
    allocated number is ``next_sequence - 1``.
    """
    row = session.execute(
        update(MachineAssetTagConfig)
        .where(MachineAssetTagConfig.id == _MACHINE_ASSET_TAG_CONFIG_ID)
        .values(next_sequence=MachineAssetTagConfig.next_sequence + 1)
        .returning(
            MachineAssetTagConfig.prefix,
            MachineAssetTagConfig.digits,
            MachineAssetTagConfig.next_sequence,
        )
    ).one_or_none()
    if row is None:
        raise ConflictError(
            "The Machine Asset Tag format is not configured yet."
            " Configure a prefix and number length in Administration →"
            " Barcode configuration before creating Machines."
        )
    prefix, digits, next_sequence = row
    return f"{prefix}{next_sequence - 1:0{digits}d}"


def create_machine(
    session: Session,
    *,
    area_id: int,
    name: object,
    description: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    installed_on: datetime.date | None = None,
    notes: str | None = None,
) -> Machine:
    clean_name = required_text(name, "Machine name")
    area = require_active_area(session, area_id, "receive new Machines")
    _reject_duplicate_active_name(session, area, clean_name)
    machine = Machine(
        area_id=area.id,
        name=clean_name,
        asset_tag=_allocate_asset_tag(session),
        description=optional_text(description),
        manufacturer=optional_text(manufacturer),
        model=optional_text(model),
        serial_number=optional_text(serial_number),
        installed_on=installed_on,
        notes=optional_text(notes),
    )
    session.add(machine)
    commit(session, _MACHINE_CONFLICTS)
    return machine


# ---------------------------------------------------------------------------
# Metadata editing
# ---------------------------------------------------------------------------


def _require_not_retired(machine: Machine, action: str) -> None:
    if machine.retired_on is not None:
        raise ConflictError(
            f"Machine '{machine.name}' is retired and cannot {action}."
            " A retired record is never mutated — reactivate the same physical"
            " Machine to return it to service."
        )


def update_machine(
    session: Session,
    machine_id: int,
    *,
    name: object = UNSET,
    description: str | None | UnsetType = UNSET,
    manufacturer: str | None | UnsetType = UNSET,
    model: str | None | UnsetType = UNSET,
    serial_number: str | None | UnsetType = UNSET,
    installed_on: datetime.date | None | UnsetType = UNSET,
    notes: str | None | UnsetType = UNSET,
) -> Machine:
    # The Area binding and the Asset Tag are deliberately not updatable:
    # the Area of an active Machine is fixed (a capacity move is a
    # replacement, or a forward-only change during reactivation), and
    # Asset Tags are immutable forever.
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "be edited")
    changed = False

    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Machine name")
        if clean_name != machine.name:
            area = session.get_one(Area, machine.area_id)
            _reject_duplicate_active_name(session, area, clean_name, exclude_id=machine.id)
            machine.name = clean_name
            changed = True
    if not isinstance(description, UnsetType):
        value = optional_text(description)
        if value != machine.description:
            machine.description = value
            changed = True
    if not isinstance(manufacturer, UnsetType):
        value = optional_text(manufacturer)
        if value != machine.manufacturer:
            machine.manufacturer = value
            changed = True
    if not isinstance(model, UnsetType):
        value = optional_text(model)
        if value != machine.model:
            machine.model = value
            changed = True
    if not isinstance(serial_number, UnsetType):
        value = optional_text(serial_number)
        if value != machine.serial_number:
            machine.serial_number = value
            changed = True
    if not isinstance(installed_on, UnsetType) and installed_on != machine.installed_on:
        machine.installed_on = installed_on
        changed = True
    if not isinstance(notes, UnsetType):
        value = optional_text(notes)
        if value != machine.notes:
            machine.notes = value
            changed = True

    if changed:
        machine.updated_at = func.now()
        commit(session, _MACHINE_CONFLICTS)
    return machine


# ---------------------------------------------------------------------------
# Maintenance override
# ---------------------------------------------------------------------------


def start_maintenance(
    session: Session,
    machine_id: int,
    *,
    note: str | None = None,
    expected_return: datetime.date | None = None,
) -> Machine:
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "start maintenance")
    if machine.maintenance_since is not None:
        raise ConflictError(f"Machine '{machine.name}' is already under maintenance.")
    # Starting the override changes the derived operational state, so
    # the state age restarts. Quantity, if any is ever assigned, stays
    # exactly where it is — maintenance never moves production.
    machine.maintenance_since = func.now()
    machine.maintenance_note = optional_text(note)
    machine.maintenance_expected_return = expected_return
    machine.state_changed_at = func.now()
    machine.updated_at = func.now()
    commit(session, _MACHINE_CONFLICTS)
    return machine


def update_maintenance(
    session: Session,
    machine_id: int,
    *,
    note: str | None | UnsetType = UNSET,
    expected_return: datetime.date | None | UnsetType = UNSET,
) -> Machine:
    """Update the note/expected return of an active override in place.

    Deliberately touches neither ``maintenance_since`` nor
    ``state_changed_at``: the context changes, the state does not
    (PROJECT_PROFILE §8.6).
    """
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "update maintenance")
    if machine.maintenance_since is None:
        raise ConflictError(
            f"Machine '{machine.name}' is not under maintenance."
            " Start maintenance before updating its note or expected return."
        )
    changed = False

    if not isinstance(note, UnsetType):
        value = optional_text(note)
        if value != machine.maintenance_note:
            machine.maintenance_note = value
            changed = True
    if (
        not isinstance(expected_return, UnsetType)
        and expected_return != machine.maintenance_expected_return
    ):
        machine.maintenance_expected_return = expected_return
        changed = True

    if changed:
        machine.updated_at = func.now()
        commit(session, _MACHINE_CONFLICTS)
    return machine


def clear_maintenance(session: Session, machine_id: int) -> Machine:
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "clear maintenance")
    if machine.maintenance_since is None:
        raise ConflictError(f"Machine '{machine.name}' is not under maintenance.")
    # Clearing the override changes the derived state again (to Idle in
    # Phase 3.5; to Running once Phase 6 assignment data can say so).
    machine.maintenance_since = None
    machine.maintenance_note = None
    machine.maintenance_expected_return = None
    machine.state_changed_at = func.now()
    machine.updated_at = func.now()
    commit(session, _MACHINE_CONFLICTS)
    return machine


# ---------------------------------------------------------------------------
# Lifecycle — retirement and reactivation, atomic with their events
# ---------------------------------------------------------------------------


def retire_machine(
    session: Session,
    machine_id: int,
    *,
    reason: str | None = None,
    actor: str | None = None,
) -> Machine:
    machine = get_machine(session, machine_id)
    if machine.retired_on is not None:
        raise ConflictError(f"Machine '{machine.name}' is already retired.")
    # The assigned-quantity blocker (PROJECT_PROFILE §8.6) has nothing
    # to check in Phase 3.5: assignment persistence arrives with
    # Phase 6, so no quantity can be assigned yet. Phase 6 must add the
    # data-backed check here.
    machine.retired_on = func.current_date()
    machine.updated_at = func.now()
    session.add(
        MachineLifecycleEvent(
            machine_id=machine.id,
            event_type=MachineLifecycleEventType.RETIRED,
            occurred_at=func.now(),
            actor=optional_text(actor),
            reason=optional_text(reason),
            before_state=MachineLifecycleState.ACTIVE,
            after_state=MachineLifecycleState.RETIRED,
        )
    )
    # One transaction: the retirement and its lifecycle event commit
    # together or not at all.
    commit(session, _MACHINE_CONFLICTS)
    return machine


def reactivate_machine(
    session: Session,
    machine_id: int,
    *,
    reason: object,
    name: object = UNSET,
    area_id: int | None = None,
    actor: str | None = None,
) -> Machine:
    """Return the same physical machine to service (RETIRED → ACTIVE).

    Same record, same identity, same Asset Tag and barcode, history
    unchanged. A different physical machine is always a new record —
    that confirmation is collected by the UI (GUI_DESIGN §12.4); the
    required reason is recorded on the lifecycle event.
    """
    clean_reason = required_text(reason, "Reactivation reason")
    machine = get_machine(session, machine_id)
    if machine.retired_on is None:
        raise ConflictError(f"Machine '{machine.name}' is not retired.")

    # Forward-only Area change: only when the physical machine moved
    # while retired. The target must be an active Area either way.
    target_area = require_active_area(
        session,
        area_id if area_id is not None else machine.area_id,
        "receive reactivated Machines",
    )
    new_name = machine.name if isinstance(name, UnsetType) else required_text(name, "Machine name")

    # Reissued-identity blockers (PROJECT_PROFILE §8.6): a serial
    # number meanwhile reissued to another active Machine is a data
    # problem to resolve first. The Asset Tag cannot be reissued —
    # uq_machines_asset_tag spans all Machines forever.
    if machine.serial_number is not None:
        reissued_to = session.scalar(
            select(Machine.id)
            .where(
                Machine.serial_number == machine.serial_number,
                Machine.retired_on.is_(None),
                Machine.id != machine.id,
            )
            .limit(1)
        )
        if reissued_to is not None:
            raise ConflictError(
                f"The serial number of Machine '{machine.name}' has meanwhile been"
                " reissued to another active Machine. Resolve this data problem"
                " before reactivating."
            )
    _reject_duplicate_active_name(session, target_area, new_name, exclude_id=machine.id)

    moved = target_area.id != machine.area_id
    from_area_id = machine.area_id if moved else None

    # One UPDATE for the whole transition: the database trigger permits
    # an area_id change only inside the same row update that clears
    # retired_on. The Machine returns as Idle — any maintenance
    # override clears and the state age restarts; Running stays derived
    # from assigned quantity (Phase 6), never from reactivation.
    machine.name = new_name
    machine.area_id = target_area.id
    machine.retired_on = None
    machine.maintenance_since = None
    machine.maintenance_note = None
    machine.maintenance_expected_return = None
    machine.state_changed_at = func.now()
    machine.updated_at = func.now()
    session.add(
        MachineLifecycleEvent(
            machine_id=machine.id,
            event_type=MachineLifecycleEventType.REACTIVATED,
            occurred_at=func.now(),
            actor=optional_text(actor),
            reason=clean_reason,
            before_state=MachineLifecycleState.RETIRED,
            after_state=MachineLifecycleState.ACTIVE,
            from_area_id=from_area_id,
            to_area_id=target_area.id if moved else None,
        )
    )
    # One transaction: the reactivation and its lifecycle event commit
    # together or not at all.
    commit(session, _MACHINE_CONFLICTS)
    return machine
