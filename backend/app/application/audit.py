"""Generic audit persistence protocol (SLICE1_DATA_MODEL §16).

One helper appends the append-only ``audit_events`` row that records a
master-data or business-demand change — WorkOrder, WorkOrderDemand, or
PartNumber. The helper only stages the row on the caller's session:
**the caller owns the transaction**, so the audit row and the audited
change commit together or roll back together — an audited write without
its audit row (or vice versa) is impossible by construction.

Production release is deliberately absent here: the ``RECEIVED``
PartMovement is itself the immutable production audit record and no
generic audit row duplicates it.
"""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.enums import AuditEntityType, AuditEventType
from app.infrastructure.models import AuditEvent


def append_audit_event(
    session: Session,
    *,
    event_type: AuditEventType,
    entity_type: AuditEntityType,
    entity_id: str,
    before_data: dict[str, Any] | None,
    after_data: dict[str, Any] | None,
    actor_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Stage one audit row in the caller's open transaction.

    ``before_data`` is NULL for creation events; edits append a new
    ``UPDATED`` row and never rewrite prior rows (the append-only
    trigger owned by migration 0004 enforces this in PostgreSQL).
    ``actor_reference`` stays a nullable, reference-free value until
    authentication exists (Phase 14).
    """
    session.add(
        AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_reference=actor_reference,
            occurred_at=func.now(),
            before_data=before_data,
            after_data=after_data,
            metadata_=metadata,
        )
    )
