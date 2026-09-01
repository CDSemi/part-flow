"""Canonical domain value enumerations (PROJECT_PROFILE §7/§8).

Framework-independent business vocabulary. Where the canonical Slice 1
schema defines a CHECK constraint (request_type, route_mode,
movement_type, and — from Phase 8 — the QuantityFlow status), the
Infrastructure layer enforces these values in PostgreSQL. Each enumeration widens additively in the
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
    complete, the quantity waits as READY_TO_TRANSFER). Phase 8 adds
    the two quantity-lineage events: SPLIT (one QuantityFlow is consumed
    into child flows — recorded on the consumed source AND on every
    child) and MERGED (several QuantityFlows of one PN are consumed into
    one resulting flow — recorded on every consumed source AND on the
    result). Neither is a position change: a lineage event keeps the
    Area, the Machine, the Operation and the holding state of the
    quantity it carries, which are derived by following the lineage to
    the last position-bearing Movement. Phase 9 adds the three
    correction/quantity-event types (PROJECT_PROFILE §8.11, §11, §16):
    SCRAPPED (damaged quantity removed from active production — the
    flow, or the split-off part, closes), QUANTITY_ADJUSTED (the
    intentional, reasoned addition of physical quantity — direction
    INCREASE, recorded in the Movement metadata — introducing a new
    QuantityFlow exactly like a RECEIVED does, never altering any
    requested quantity), and REVERSED (the compensating Movement of a
    command-level Undo: one REVERSED row per original Movement,
    referencing it through ``reverses_movement_id``; the original is
    preserved and the derived state is restored by EXCLUDING the
    reversed pair from every derivation, never by re-stating state on
    the REVERSED row). Phase 10 adds STOCKED (PROJECT_PROFILE §18): the
    scan of quantity into the terminal Stockroom Area — recorded like a
    TRANSFERRED (source Area → terminal Area at a Station, preceded by
    the implicit AREA_COMPLETED of actively processing quantity) — the
    quantity is manufacturing-complete, leaves active production (the
    flow closes as STOCKED) and becomes available for Work Order
    Allocation, which is recorded separately from Movement. Later
    phases widen this additively (ROUTE_ADJUSTED, ...); none of those
    values exist yet.
    """

    RECEIVED = "RECEIVED"
    TRANSFERRED = "TRANSFERRED"
    ASSIGNED_TO_MACHINE = "ASSIGNED_TO_MACHINE"
    RELEASED_FROM_MACHINE = "RELEASED_FROM_MACHINE"
    AREA_COMPLETED = "AREA_COMPLETED"
    SPLIT = "SPLIT"
    MERGED = "MERGED"
    SCRAPPED = "SCRAPPED"
    QUANTITY_ADJUSTED = "QUANTITY_ADJUSTED"
    REVERSED = "REVERSED"
    STOCKED = "STOCKED"


class MovementReason(StrEnum):
    """Typed, optional movement intent (PROJECT_PROFILE §8.11, §14).

    First value REPAIR: an explicit return of quantity to a previously
    visited Area to correct earlier work — always ``movement_type =
    TRANSFERRED``, always chosen explicitly by the user (never inferred
    from route history), never a Work Order Demand and never a Request
    Type. The free-text explanation stays in the separate ``reason``
    column and is mandatory for a Repair.
    """

    REPAIR = "REPAIR"


class AdjustmentDirection(StrEnum):
    """Direction of a ``QUANTITY_ADJUSTED`` Movement (PROJECT_PROFILE §8.11).

    Phase 9 records only INCREASE (the intentional addition of physical
    quantity); the direction lives in the Movement metadata
    (``adjustment.direction``). Removal of damaged quantity is never an
    adjustment — it is the canonical ``SCRAPPED`` event.
    """

    INCREASE = "INCREASE"


class LineageRelation(StrEnum):
    """How a child QuantityFlow descends from a parent (Phase 8).

    One row per (parent, child) edge of `quantity_flow_lineage`: a
    SPLIT edge fans one parent out to several children (1 → N), a
    MERGED edge fans several parents into one child (N → 1). Both are
    the same edge shape, so a single table reconstructs either
    direction without forcing N → 1 into a single parent column.
    """

    SPLIT = "SPLIT"
    MERGED = "MERGED"


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

    ACTIVE is the creation default (SLICE1_DATA_MODEL §9). Phase 8 adds
    the two consumption closures: SPLIT (the flow was consumed into its
    child flows) and MERGED (the flow was consumed into a resulting
    flow). Phase 9 adds SCRAPPED (the flow's damaged quantity was
    removed from active production by a confirmed Scrap) and REVERSED
    (the command that CREATED the flow — a SPLIT, a MERGED or a
    QUANTITY_ADJUSTED addition — was undone, so the flow never counts
    as quantity again; a flow closed by a command that is later undone
    reopens as ACTIVE instead). A closed flow keeps its history and its
    last position but is never active inventory again; `closed_at` is
    set exactly for a non-ACTIVE status (CHECK). Phase 10 adds STOCKED
    (the flow's whole quantity was scanned into the terminal Stockroom
    Area: manufacturing-complete, never active inventory again, and the
    quantity that Work Order Allocation draws from).
    """

    ACTIVE = "ACTIVE"
    SPLIT = "SPLIT"
    MERGED = "MERGED"
    SCRAPPED = "SCRAPPED"
    REVERSED = "REVERSED"
    STOCKED = "STOCKED"


class WorkOrderStatus(StrEnum):
    """Status vocabulary of a WorkOrder (PROJECT_PROFILE §8.2).

    OPEN is the manual-intake initial state and matches the database
    creation default — it is the only STORED value in Phase 4.
    RELEASED is a DERIVED read-model value (GUI_DESIGN §11.1): a Work
    Order whose every current demand line has committed release
    evidence reads as RELEASED; the stored column stays OPEN, because
    the derivation from immutable ``RECEIVED`` Movement context can
    never drift while a stored copy could. COMPLETED (Phase 10) is
    likewise a DERIVED read value: a Work Order whose every demand line
    is fully allocated from stocked quantity reads as COMPLETED — the
    allocation records stay the source of truth, ``completed_at`` is
    the persisted done-date projection they set and clear (PROJECT_PROFILE
    §8.2), and the stored status column stays OPEN.
    """

    OPEN = "OPEN"
    # Derived read statuses only — never written to work_orders.status.
    RELEASED = "RELEASED"
    COMPLETED = "COMPLETED"


class AllocationSource(StrEnum):
    """Where a WorkOrderAllocation row was recorded (PROJECT_PROFILE §18).

    STOCKROOM: the receiving confirmation at a Stockroom Scan Station
    (the routine Operator workflow, no Manager approval). MANAGEMENT: an
    allocation or adjustment made from Management (Admin/Manager may
    adjust allocation at any time; authorization itself arrives with
    Phase 14 — nothing is simulated before).
    """

    STOCKROOM = "STOCKROOM"
    MANAGEMENT = "MANAGEMENT"


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
