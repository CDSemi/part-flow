"""History archival endpoints (Phase 16, history archival design B; build steps 1, 2, 4).

HTTP surface of the runs of Administration → History archival & purge; its
settings are ``/api/policies/data-retention`` (``app.api.policies``). Routes
stay thin: request schemas validate shape only (``extra="forbid"``), the
Application layer (``app.application.history_archive``) owns every rule,
audit row and transaction, and the central handlers in ``app.api.errors``
translate typed failures (404 unknown run, 409 conflict, 422 invalid).

- ``GET /history-archive/preview?cutoff_date=YYYY-MM-DD`` — what a run would
  archive at the cutoff (the start of that site-calendar day; by default the
  newest the retention period allows): components, flows, Movements, their
  date range and an estimated archive size. Read-only, within the preview
  time limit (409 when it runs out, when no retention period is set);
  422 for a cutoff newer than allowed.
- ``GET /history-archive/runs?limit=N`` — the newest runs first (default
  50, 1-500).
- ``POST /history-archive/runs`` — ``{"reason": text, "cutoff_date":
  date | null}``: request a run (the request is the approval); its preview
  is recomputed and stored; 201 with the run. 409 while another run is
  open or when nothing is eligible.
- ``POST /history-archive/runs/{run_id}/cancel`` — cancel an ``APPROVED`` or
  ``WAITING_FOR_COPY`` run (a cancelled run answers unchanged); a run the
  host job works on (``RUNNING``, ``PURGING``) is refused, and a ``PROPOSED``
  run is dismissed instead.
- ``POST /history-archive/runs/{run_id}/approve`` — ``{"reason": text}``:
  approve a ``PROPOSED`` (automatic) run with a reason of the approver's
  own; it becomes ``APPROVED`` with the approver and the time (approving it
  again as the same User answers unchanged).
- ``POST /history-archive/runs/{run_id}/dismiss`` — dismiss a ``PROPOSED``
  run (``DISMISSED``; no body, like cancel; a dismissed run answers
  unchanged).

A run answers its purge too (build step 2): the pre-purge backup, when the
purge started, what it deleted so far, and the purge receipt's name and
SHA-256; and (build step 4) its trigger (``MANUAL`` or ``AUTOMATIC``; an
automatic run has no ``requested_by``), its approval, who dismissed it, the
standing authorization it was created under, and whether it failed because
the User of its authority may no longer run it.
- ``POST /history-archive/runs/{run_id}/copy-confirmation`` —
  ``{"manifest_sha256": hex}``: confirm the off-host copy of a
  ``WAITING_FOR_COPY`` run, against the manifest SHA-256 shown.

Access: the run list needs a signed-in User (every Administration read);
the preview and every change need ``RUN_HISTORY_ARCHIVAL_AND_PURGE``, and
each audit row names the signed-in User.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.authorization import RequirePermission, SignedInDep
from app.api.dependencies import SessionDep
from app.application import history_archive
from app.application.authentication import Principal
from app.application.history_archive import RunUser, RunView
from app.core.config import get_settings
from app.domain.enums import Permission
from app.infrastructure.models import HistoryArchiveRun

router = APIRouter(prefix="/api")

HistoryArchiveManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE))
]


class PreviewResponse(BaseModel):
    cutoff_date: datetime.date
    cutoff: datetime.datetime
    latest_cutoff_date: datetime.date
    site_timezone: str
    components: int
    flows: int
    movements: int
    first_occurred_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None
    estimated_archive_bytes: int
    duration_ms: int
    time_limit_seconds: int


class UserRef(BaseModel):
    user_id: int
    display_name: str


class RunPreview(BaseModel):
    components: int
    flows: int
    movements: int
    first_occurred_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None
    estimated_archive_bytes: int


class RunExport(BaseModel):
    components: int
    flows: int
    movements: int
    lineage_edges: int
    assigned_routes: int


class RunSkips(BaseModel):
    components: int
    flows: int


class RunBackup(BaseModel):
    """The pre-purge backup the host script took before the purge."""

    name: str
    dump_started_at: datetime.datetime


class RunPurged(BaseModel):
    """What the purge deleted so far: whole components."""

    components: int
    flows: int
    movements: int


class RunApproval(BaseModel):
    """The approval of a proposed (automatic) run, by a person with a reason of their own."""

    approved_by: UserRef
    approved_at: datetime.datetime
    reason: str


class RunAuthorization(BaseModel):
    """The standing authorization an automatic run was created under: the audit row
    that switched the approval step off."""

    audit_event_id: int
    authorized_by: UserRef | None
    authorized_at: datetime.datetime | None


class RunViewRef(BaseModel):
    """The newest view of a run's archive (build step 5)."""

    id: int
    state: str


class RunResponse(BaseModel):
    id: int
    state: str
    cutoff: datetime.datetime
    cutoff_date: datetime.date
    groups: list[str]
    # MANUAL (requested in Administration) or AUTOMATIC (history-archive evaluate).
    trigger: str
    reason: str
    # Null exactly for an automatic run.
    requested_by: UserRef | None
    requested_at: datetime.datetime
    preview: RunPreview
    started_at: datetime.datetime | None
    archive_name: str | None
    exported_at: datetime.datetime | None
    exported: RunExport | None
    archive_bytes: int | None
    manifest_sha256: str | None
    verified_at: datetime.datetime | None
    skipped: RunSkips | None
    copied_at: datetime.datetime | None
    copy_confirmed_at: datetime.datetime | None
    copy_confirmed_by: UserRef | None
    cancelled_by: UserRef | None
    # The purge (build step 2): null until the run became PURGING.
    pre_purge_backup: RunBackup | None
    purge_started_at: datetime.datetime | None
    purged: RunPurged | None
    # The purge receipt beside the archive folder, once written.
    receipt_name: str | None
    receipt_sha256: str | None
    # Automatic runs (build step 4): the approval of a proposed run, who
    # dismissed it, the standing authorization it was created under.
    approval: RunApproval | None
    dismissed_by: UserRef | None
    authorization: RunAuthorization | None
    finished_at: datetime.datetime | None
    result: str | None
    failure_reason: str | None
    # The run failed because the User of its authority may no longer run it.
    authorization_failed: bool
    # Viewing an archive (build step 5): whether Open for viewing applies — a
    # finished run whose archive was verified — and its newest view.
    viewable: bool
    view: RunViewRef | None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    # The site-calendar day whose start is the cutoff; null = the newest allowed.
    cutoff_date: datetime.date | None = None


class CopyConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_sha256: str


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


def _user(value: RunUser | None) -> UserRef | None:
    return (
        None if value is None else UserRef(user_id=value.user_id, display_name=value.display_name)
    )


def _run_response(view: RunView) -> RunResponse:
    run = view.run
    exported = (
        None
        if run.exported_at is None
        else RunExport(
            components=run.exported_components or 0,
            flows=run.exported_flows or 0,
            movements=run.exported_movements or 0,
            lineage_edges=run.exported_lineage_edges or 0,
            assigned_routes=run.exported_assigned_routes or 0,
        )
    )
    skipped = (
        None
        if run.verified_at is None
        else RunSkips(components=run.skipped_components or 0, flows=run.skipped_flows or 0)
    )
    approval = (
        None
        if run.approved_at is None or view.approved_by is None or run.approval_reason is None
        else RunApproval(
            approved_by=UserRef(
                user_id=view.approved_by.user_id, display_name=view.approved_by.display_name
            ),
            approved_at=run.approved_at,
            reason=run.approval_reason,
        )
    )
    authorization = (
        None
        if view.authorization is None
        else RunAuthorization(
            audit_event_id=view.authorization.audit_event_id,
            authorized_by=_user(view.authorization.authorized_by),
            authorized_at=view.authorization.authorized_at,
        )
    )
    backup = (
        None
        if run.pre_purge_backup_name is None or run.pre_purge_backup_started_at is None
        else RunBackup(
            name=run.pre_purge_backup_name, dump_started_at=run.pre_purge_backup_started_at
        )
    )
    purged = (
        None
        if run.purge_started_at is None
        else RunPurged(
            components=run.purged_components or 0,
            flows=run.purged_flows or 0,
            movements=run.purged_movements or 0,
        )
    )
    return RunResponse(
        id=run.id,
        state=run.state,
        cutoff=run.cutoff,
        cutoff_date=history_archive.site_date_of(run.cutoff, history_archive.site_zone()),
        groups=[history_archive.GROUP_MOVEMENT_HISTORY],
        trigger=run.trigger,
        reason=run.reason,
        requested_by=_user(view.requested_by),
        requested_at=run.requested_at,
        preview=RunPreview(
            components=run.preview_components,
            flows=run.preview_flows,
            movements=run.preview_movements,
            first_occurred_at=run.preview_first_occurred_at,
            last_occurred_at=run.preview_last_occurred_at,
            estimated_archive_bytes=run.preview_archive_bytes,
        ),
        started_at=run.started_at,
        archive_name=run.archive_name,
        exported_at=run.exported_at,
        exported=exported,
        archive_bytes=run.archive_bytes,
        manifest_sha256=run.manifest_sha256,
        verified_at=run.verified_at,
        skipped=skipped,
        copied_at=run.copied_at,
        copy_confirmed_at=run.copy_confirmed_at,
        copy_confirmed_by=_user(view.copy_confirmed_by),
        cancelled_by=_user(view.cancelled_by),
        pre_purge_backup=backup,
        purge_started_at=run.purge_started_at,
        purged=purged,
        receipt_name=history_archive.receipt_name(run.archive_name, run.receipt_sha256),
        receipt_sha256=run.receipt_sha256,
        approval=approval,
        dismissed_by=_user(view.dismissed_by),
        authorization=authorization,
        finished_at=run.finished_at,
        result=run.result,
        failure_reason=run.failure_reason,
        authorization_failed=run.authorization_failed,
        viewable=view.viewable,
        view=(
            None
            if view.archive_view is None
            else RunViewRef(id=view.archive_view[0], state=view.archive_view[1])
        ),
    )


def _answer(session: Session, run: HistoryArchiveRun) -> RunResponse:
    return _run_response(history_archive.run_view(session, run))


@router.get("/history-archive/preview")
def get_preview(
    principal: HistoryArchiveManagerDep,
    session: SessionDep,
    cutoff_date: Annotated[datetime.date | None, Query()] = None,
) -> PreviewResponse:
    result = history_archive.preview(session, cutoff_date)
    return PreviewResponse(
        cutoff_date=result.cutoff.day,
        cutoff=result.cutoff.instant,
        latest_cutoff_date=result.latest_cutoff_date,
        site_timezone=get_settings().site_timezone,
        components=result.counts.components,
        flows=result.counts.flows,
        movements=result.counts.movements,
        first_occurred_at=result.counts.first_occurred_at,
        last_occurred_at=result.counts.last_occurred_at,
        estimated_archive_bytes=result.counts.estimated_archive_bytes,
        duration_ms=result.duration_ms,
        time_limit_seconds=result.time_limit_seconds,
    )


@router.get("/history-archive/runs")
def get_runs(
    principal: SignedInDep,
    session: SessionDep,
    limit: Annotated[int, Query()] = history_archive.DEFAULT_RUN_LIMIT,
) -> list[RunResponse]:
    return [_run_response(view) for view in history_archive.list_runs(session, limit=limit)]


@router.post("/history-archive/runs", status_code=201)
def post_run(
    principal: HistoryArchiveManagerDep, body: RunRequest, session: SessionDep
) -> RunResponse:
    run = history_archive.request_run(
        session, reason=body.reason, cutoff_date=body.cutoff_date, actor_user_id=principal.user_id
    )
    return _answer(session, run)


@router.post("/history-archive/runs/{run_id}/cancel")
def post_cancel(
    principal: HistoryArchiveManagerDep, run_id: int, session: SessionDep
) -> RunResponse:
    run = history_archive.cancel_run(session, run_id, actor_user_id=principal.user_id)
    return _answer(session, run)


@router.post("/history-archive/runs/{run_id}/approve")
def post_approve(
    principal: HistoryArchiveManagerDep, run_id: int, body: ApprovalRequest, session: SessionDep
) -> RunResponse:
    run = history_archive.approve_run(
        session, run_id, reason=body.reason, actor_user_id=principal.user_id
    )
    return _answer(session, run)


@router.post("/history-archive/runs/{run_id}/dismiss")
def post_dismiss(
    principal: HistoryArchiveManagerDep, run_id: int, session: SessionDep
) -> RunResponse:
    run = history_archive.dismiss_run(session, run_id, actor_user_id=principal.user_id)
    return _answer(session, run)


@router.post("/history-archive/runs/{run_id}/copy-confirmation")
def post_copy_confirmation(
    principal: HistoryArchiveManagerDep,
    run_id: int,
    body: CopyConfirmationRequest,
    session: SessionDep,
) -> RunResponse:
    run = history_archive.confirm_copy(
        session, run_id, manifest_sha256=body.manifest_sha256, actor_user_id=principal.user_id
    )
    return _answer(session, run)
