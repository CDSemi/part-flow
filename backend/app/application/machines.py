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
  Creation accepts the previewed tag as an optimistic precondition
  only (stale preview → conflict, counter not consumed) — the client
  value is never the assigned identity.
- Display names are unique among the ACTIVE Machines of one Area;
  reuse across time and replacements stays allowed.
- Save changes of the Edit dialog is ONE transaction: editable
  metadata and — while the override is active — the maintenance
  note/expected return apply together or not at all. A retirement may
  carry the same draft as a recorded Save decision (GUI_DESIGN §12.4)
  and applies it atomically with the retirement and its event.
- The Area of an active Machine is fixed (moving capacity is a
  replacement); metadata editing never touches it. A retired record is
  never renamed or mutated — reactivation is the only door back.
- Maintenance is an explicit override: starting it changes the derived
  operational state (so ``state_changed_at`` resets), updating the
  note/expected return while it is active changes neither the start
  time nor the state, and clearing it resets ``state_changed_at``
  again — to Running when quantity is still assigned, else to Idle.
- Retirement and reactivation commit atomically with their
  ``machine_lifecycle_events`` row: one transaction, no lifecycle
  change without its event and no event without its change. Events are
  append-only (database trigger) and carry a nullable, reference-free
  actor — no Worker/User linkage in Phase 3.5.
- Retirement while active quantity is assigned is blocked
  (PROJECT_PROFILE §8.6): the Machine row is locked ``FOR UPDATE``
  first and the assigned ACTIVE quantity (``quantity_flows
  .current_machine_id``, Phase 6) is counted under that lock, so
  retirement and an assignment in flight have one serial outcome —
  the assignment holds the same Machine lock until COMMIT and re-reads
  the row under it. Retirement takes no flow lock, so it never
  contradicts the production lock order (flow → station → Machine →
  Area → Operation).
- The operational state is derived, never stored (PROJECT_PROFILE
  §8.6): Maintenance override wins, else assigned ACTIVE quantity means
  Running, else Idle. ``state_changed_at`` moves only when that derived
  value actually changes — the production commands call
  ``note_assignment_change`` under the Machine lock for exactly that.
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
from collections.abc import Iterable
from typing import Any, Final, Literal

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
from app.domain.enums import (
    MachineLifecycleEventType,
    MachineLifecycleState,
    MachineOperationalState,
    QuantityFlowStatus,
)
from app.infrastructure.models import (
    Area,
    Machine,
    MachineAssetTagConfig,
    MachineLifecycleEvent,
    QuantityFlow,
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


def lock_machine(session: Session, machine_id: int) -> Machine | None:
    """The Machine row locked until COMMIT and RE-READ under that lock.

    ``populate_existing`` matters: a Machine already in the Session
    identity map (an earlier unlocked listing) would otherwise come back
    stale — a retirement or maintenance start committed meanwhile would
    go unseen behind the lock that was meant to see it.
    """
    return session.get(Machine, machine_id, with_for_update=True, populate_existing=True)


def areas_with_machines(session: Session, area_ids: Iterable[int] | None = None) -> set[int]:
    """The Areas that currently have at least one ACTIVE (non-retired) Machine.

    The Area mode follows from its Machines (PROJECT_PROFILE §12): an
    Area in this set uses QUEUE_AND_ASSIGN, any other Area processes
    directly. There is no stored mode and no per-Area configuration.
    ``area_ids`` narrows the lookup; None considers every Area.
    """
    query = select(Machine.area_id).where(Machine.retired_on.is_(None)).distinct()
    if area_ids is not None:
        wanted = set(area_ids)
        if not wanted:
            return set()
        query = query.where(Machine.area_id.in_(wanted))
    return {int(area_id) for area_id in session.scalars(query)}


def area_has_machines(session: Session, area_id: int) -> bool:
    """Whether the Area has an active Machine — its processing mode (§12)."""
    return area_id in areas_with_machines(session, [area_id])


def assigned_quantity(
    session: Session, machine_id: int, *, exclude_flow_id: int | None = None
) -> int:
    """ACTIVE quantity whose projection currently references the Machine.

    Correct for decisions only under the Machine row lock: every
    command that sets or clears ``current_machine_id`` holds it.
    """
    query = select(func.coalesce(func.sum(QuantityFlow.quantity), 0)).where(
        QuantityFlow.current_machine_id == machine_id,
        QuantityFlow.status == QuantityFlowStatus.ACTIVE,
    )
    if exclude_flow_id is not None:
        query = query.where(QuantityFlow.id != exclude_flow_id)
    return int(session.scalar(query) or 0)


def assigned_quantities(session: Session, machine_ids: list[int]) -> dict[int, int]:
    """Assigned ACTIVE quantity per Machine for read models (unlocked)."""
    if not machine_ids:
        return {}
    rows = session.execute(
        select(QuantityFlow.current_machine_id, func.sum(QuantityFlow.quantity))
        .where(
            QuantityFlow.current_machine_id.in_(machine_ids),
            QuantityFlow.status == QuantityFlowStatus.ACTIVE,
        )
        .group_by(QuantityFlow.current_machine_id)
    )
    return {int(machine_id): int(total) for machine_id, total in rows}


def operational_state(machine: Machine, assigned: int) -> MachineOperationalState:
    """Derived operational state (PROJECT_PROFILE §8.6): never chosen, never stored."""
    if machine.maintenance_since is not None:
        return MachineOperationalState.MAINTENANCE
    if assigned > 0:
        return MachineOperationalState.RUNNING
    return MachineOperationalState.IDLE


def note_assignment_change(machine: Machine, *, assigned_before: int, assigned_after: int) -> None:
    """Restart the state age only when the derived state really changes.

    Called by the production commands under the Machine row lock with
    the assigned quantity before and after their write. Under a
    Maintenance override the derived state is Maintenance either way,
    so nothing moves; Idle ↔ Running moves ``state_changed_at``.
    """
    before = operational_state(machine, assigned_before)
    after = operational_state(machine, assigned_after)
    if before != after:
        machine.state_changed_at = func.now()
        machine.updated_at = func.now()


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
    expected_asset_tag: str | None = None,
) -> Machine:
    """Create a Machine with its automatically assigned Asset Tag.

    ``expected_asset_tag`` is an optimistic precondition only — never
    an identity: the Confirm-new-Machine summary (GUI_DESIGN §12.3)
    shows the exact Asset Tag/barcode before creation, and the
    frontend may submit that previewed value so a preview gone stale
    (another Machine created, or the format changed, in between) is
    rejected instead of silently assigning a different tag. The server
    always derives and allocates the tag itself; on a mismatch the
    transaction rolls back — the counter is not consumed and no
    Machine is created — so the UI can refresh the confirmation.
    """
    clean_name = required_text(name, "Machine name")
    area = require_active_area(session, area_id, "receive new Machines")
    _reject_duplicate_active_name(session, area, clean_name)
    asset_tag = _allocate_asset_tag(session)
    if expected_asset_tag is not None and expected_asset_tag != asset_tag:
        # Roll back explicitly: the allocation UPDATE above returns the
        # unissued sequence number with the transaction.
        session.rollback()
        raise ConflictError(
            f"The previewed Asset Tag '{expected_asset_tag}' is out of date:"
            f" the next Asset Tag is now '{asset_tag}'."
            " Refresh the confirmation and try again."
        )
    machine = Machine(
        area_id=area.id,
        name=clean_name,
        asset_tag=asset_tag,
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


def _apply_edits(
    session: Session,
    machine: Machine,
    *,
    name: object = UNSET,
    description: str | None | UnsetType = UNSET,
    manufacturer: str | None | UnsetType = UNSET,
    model: str | None | UnsetType = UNSET,
    serial_number: str | None | UnsetType = UNSET,
    installed_on: datetime.date | None | UnsetType = UNSET,
    notes: str | None | UnsetType = UNSET,
    maintenance_note: str | None | UnsetType = UNSET,
    maintenance_expected_return: datetime.date | None | UnsetType = UNSET,
) -> bool:
    """Validate and apply an Edit Machine draft to the loaded record.

    The single Save-changes surface of the Edit dialog (GUI_DESIGN
    §12.3): editable metadata plus — only while a maintenance override
    is active — the maintenance note/expected return, updated in place
    without touching ``maintenance_since`` or ``state_changed_at``.
    Mutates the ORM object only; the caller owns the transaction, so
    the same draft applies inside a plain update or atomically inside
    a retirement. Returns whether anything changed.
    """
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

    # Maintenance context is editable only inside an active override
    # (the ck_machines_maintenance_shape rule); the in-place update
    # deliberately changes neither the start time nor the state.
    context_provided = not isinstance(maintenance_note, UnsetType) or not isinstance(
        maintenance_expected_return, UnsetType
    )
    if context_provided and machine.maintenance_since is None:
        raise ConflictError(
            f"Machine '{machine.name}' is not under maintenance."
            " Start maintenance before editing its note or expected return."
        )
    if not isinstance(maintenance_note, UnsetType):
        value = optional_text(maintenance_note)
        if value != machine.maintenance_note:
            machine.maintenance_note = value
            changed = True
    if (
        not isinstance(maintenance_expected_return, UnsetType)
        and maintenance_expected_return != machine.maintenance_expected_return
    ):
        machine.maintenance_expected_return = maintenance_expected_return
        changed = True

    return changed


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
    maintenance_note: str | None | UnsetType = UNSET,
    maintenance_expected_return: datetime.date | None | UnsetType = UNSET,
) -> Machine:
    """Save changes of the Edit Machine dialog as one transaction.

    Editable metadata and — while the override is active — the
    maintenance note/expected return commit together or not at all.
    The Area binding and the Asset Tag are deliberately not updatable:
    the Area of an active Machine is fixed (a capacity move is a
    replacement, or a forward-only change during reactivation), and
    Asset Tags are immutable forever. ``maintenance_since``,
    ``state_changed_at`` and the lifecycle fields stay server-owned.
    """
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "be edited")
    changed = _apply_edits(
        session,
        machine,
        name=name,
        description=description,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        installed_on=installed_on,
        notes=notes,
        maintenance_note=maintenance_note,
        maintenance_expected_return=maintenance_expected_return,
    )

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


def clear_maintenance(session: Session, machine_id: int) -> Machine:
    machine = get_machine(session, machine_id)
    _require_not_retired(machine, "clear maintenance")
    if machine.maintenance_since is None:
        raise ConflictError(f"Machine '{machine.name}' is not under maintenance.")
    # Clearing the override changes the derived state again (to Running
    # when quantity is still assigned, otherwise to Idle) — either way
    # the state age restarts.
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
    edits: dict[str, Any] | None = None,
) -> Machine:
    """Retire a Machine, optionally applying a recorded Edit draft first.

    GUI_DESIGN §12.4: a retirement started with unsaved edits records a
    Save/Discard decision that is executed only at the final
    confirmation. A recorded Save arrives here as ``edits`` — the same
    draft shape the Edit dialog saves — and the draft, the retirement
    date, and the ``RETIRED`` lifecycle event commit in ONE
    transaction: if any validation or write fails, none of the three
    persists. A recorded Discard simply sends no draft.
    """
    # The Machine row lock is taken FIRST: an assignment in flight holds
    # the same lock until its COMMIT (and re-reads the row under it), so
    # either it committed before this count — and the retirement is
    # refused — or it re-reads a retired Machine and is refused itself.
    machine = lock_machine(session, machine_id)
    if machine is None:
        raise NotFoundError(f"Machine {machine_id} does not exist.")
    if machine.retired_on is not None:
        raise ConflictError(f"Machine '{machine.name}' is already retired.")
    assigned = assigned_quantity(session, machine.id)
    if assigned > 0:
        raise ConflictError(
            f"Machine '{machine.name}' still holds {assigned} pcs of active quantity"
            " assigned to it. Complete (DONE) or return the quantity to the queue"
            " through the production workflow before retiring the Machine."
        )
    if edits:
        _apply_edits(session, machine, **edits)
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
