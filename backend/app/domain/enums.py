"""Canonical domain value enumerations (PROJECT_PROFILE §7/§8).

Framework-independent business vocabulary. Where the canonical Slice 1
schema defines a CHECK constraint (request_type, route_mode,
movement_type), the Infrastructure layer enforces these values in
PostgreSQL; QuantityFlowStatus is not CHECK-constrained — ACTIVE is
only the creation default. Each enumeration widens additively in the
phase that introduces new values — no future value is pre-declared
here.
"""

from enum import StrEnum


class RequestType(StrEnum):
    """Why a Work Order Demand exists (PROJECT_PROFILE §7 Request Type).

    Repair is not a Request Type — it is a later-phase movement intent.
    """

    NEW = "NEW"
    MODIFY = "MODIFY"


class RouteMode(StrEnum):
    """Route mode of a QuantityFlow (PROJECT_PROFILE §7 Route and Route Mode).

    FLOATING is the default: the actual route is derived from immutable
    Movement history and the flow has no AssignedRoute snapshot. Only a
    PLANNED flow carries an AssignedRoute.
    """

    FLOATING = "FLOATING"
    PLANNED = "PLANNED"


class MovementType(StrEnum):
    """Immutable PartMovement event types (PROJECT_PROFILE §8.11).

    Phase 3 needs only RECEIVED — the first Movement of every
    QuantityFlow. Phase 5 adds TRANSFERRED — the Scan Station move of a
    whole QuantityFlow from its current Area into the station's Area.
    Phase 6 adds the Machine-Area processing events: ASSIGNED_TO_MACHINE
    (queued quantity becomes ON_MACHINE), RELEASED_FROM_MACHINE (the
    QUEUE action — unfinished quantity returns to the Area queue) and
    AREA_COMPLETED (the DONE action — processing at the current Area is
    complete, the quantity waits as READY_TO_TRANSFER). Later phases
    widen this additively (SPLIT, MERGED, REVERSED, STOCKED, ...); none
    of those values exist yet.
    """

    RECEIVED = "RECEIVED"
    TRANSFERRED = "TRANSFERRED"
    ASSIGNED_TO_MACHINE = "ASSIGNED_TO_MACHINE"
    RELEASED_FROM_MACHINE = "RELEASED_FROM_MACHINE"
    AREA_COMPLETED = "AREA_COMPLETED"


class ProcessingState(StrEnum):
    """Derived holding state of ACTIVE quantity in an Area
    (PROJECT_PROFILE §12 Area Processing States).

    Never stored: derived from the flow's latest Movement AND the mode
    of the Area it is in — exactly two Area modes exist and the mode
    follows from the Area's Machines (an Area with one or more active
    Machines uses QUEUE_AND_ASSIGN; an Area without Machines processes
    directly; no per-Area configuration). An ASSIGNED_TO_MACHINE makes
    it ON_MACHINE (the projection then carries the Machine), an
    AREA_COMPLETED makes it READY_TO_TRANSFER (finished, waiting on the
    Area's rack with NO Machine), and every other latest Movement
    (RECEIVED, TRANSFERRED, RELEASED_FROM_MACHINE) leaves it QUEUED in
    a Machine Area or PROCESSING — actively processing, owned directly
    by the Area, Machine NULL — in an Area without Machines (Phase 7).
    A NULL current Machine therefore never means "queued" by itself.
    """

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    ON_MACHINE = "ON_MACHINE"
    READY_TO_TRANSFER = "READY_TO_TRANSFER"


class MachineOperationalState(StrEnum):
    """Derived operational state of a Machine (PROJECT_PROFILE §8.6).

    Never chosen by users and never stored: an explicit Maintenance
    override wins; otherwise assigned ACTIVE quantity means Running;
    otherwise Idle. `machines.state_changed_at` records when this
    derived value last changed.
    """

    MAINTENANCE = "MAINTENANCE"
    RUNNING = "RUNNING"
    IDLE = "IDLE"


class QuantityFlowStatus(StrEnum):
    """Lifecycle status of a QuantityFlow.

    ACTIVE is the creation default (SLICE1_DATA_MODEL §9); closing
    statuses arrive with the phases that close flows.
    """

    ACTIVE = "ACTIVE"


class WorkOrderStatus(StrEnum):
    """Status vocabulary of a WorkOrder (PROJECT_PROFILE §8.2).

    OPEN is the manual-intake initial state and matches the database
    creation default — it is the only STORED value in Phase 4.
    RELEASED is a DERIVED read-model value (GUI_DESIGN §11.1): a Work
    Order whose every current demand line has committed release
    evidence reads as RELEASED; the stored column stays OPEN, because
    the derivation from immutable ``RECEIVED`` Movement context can
    never drift while a stored copy could. Completion stays derived
    from allocation records (`completed_at`, Phase 10) and is never a
    stored status value either.
    """

    OPEN = "OPEN"
    # Derived read status only — never written to work_orders.status.
    RELEASED = "RELEASED"


class AuditEventType(StrEnum):
    """Generic audit event types (SLICE1_DATA_MODEL §16).

    Slice 1 records exactly creation and edit; the vocabulary widens
    additively in the phases that introduce new auditable actions.
    """

    CREATED = "CREATED"
    UPDATED = "UPDATED"


class AuditEntityType(StrEnum):
    """Entity types the generic audit table records (SLICE1_DATA_MODEL §16).

    Exactly the master-data and business-demand entities — never
    production activity (PartMovement is the production audit record)
    and never Machine (machine_lifecycle_events owns that history).
    Widens additively in later phases.
    """

    WORK_ORDER = "WorkOrder"
    WORK_ORDER_DEMAND = "WorkOrderDemand"
    PART_NUMBER = "PartNumber"


class MachineLifecycleEventType(StrEnum):
    """Append-only Machine lifecycle event types (PROJECT_PROFILE §8.6).

    Exactly the two events the canonical lifecycle history records:
    retirement and return-to-service of the same physical machine.
    Activation at creation stays implicit — it is never an event.
    """

    RETIRED = "RETIRED"
    REACTIVATED = "REACTIVATED"


class MachineLifecycleState(StrEnum):
    """Lifecycle state of a Machine (PROJECT_PROFILE §7/§8.6).

    A Machine is ACTIVE until retired; `machines.retired_on` carries
    the state, and lifecycle events record the before/after pair. The
    operational Running/Idle/Maintenance state is derived presentation,
    never a lifecycle state and never stored.
    """

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
