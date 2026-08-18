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
    QuantityFlow. Later phases widen this additively (TRANSFERRED,
    ASSIGNED_TO_MACHINE, ...); none of those values exist yet.
    """

    RECEIVED = "RECEIVED"


class QuantityFlowStatus(StrEnum):
    """Lifecycle status of a QuantityFlow.

    ACTIVE is the creation default (SLICE1_DATA_MODEL §9); closing
    statuses arrive with the phases that close flows.
    """

    ACTIVE = "ACTIVE"


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
