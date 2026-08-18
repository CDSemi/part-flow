"""Canonical domain value enumerations (PROJECT_PROFILE §7/§8).

Framework-independent: these enums define the business vocabulary that
the Infrastructure layer enforces with PostgreSQL CHECK constraints.
Each enumeration widens additively in the phase that introduces new
values — no future value is pre-declared here.
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
