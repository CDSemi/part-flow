// History archival runs API (Administration → History archival & purge;
// Phase 16, archival design B steps 1, 2 and 4).
//
// What a run would archive at a cutoff (the preview), the runs newest
// first, requesting a run (the request is the approval: the server
// recomputes and stores the preview), cancelling a run, and confirming
// the off-host copy of a run waiting for it. The settings of the section
// are `/api/policies/data-retention` (`api/policies.ts`). A run exports
// the history to archive files, copies them off the server, takes a
// pre-purge backup and then purges the archived Movements (`PURGING`),
// ending `COMPLETED` or `COMPLETED_WITH_SKIPS` with the result `PURGED`
// or `NOTHING_PURGED`; runs that step 1 finished keep `EXPORT_ONLY`.
// Step 4 adds automatic runs, which the server's archival job creates
// (`AUTOMATIC`, no requester): `PROPOSED` until a person approves it with
// a reason of their own or dismisses it (`DISMISSED`), or `APPROVED`
// at once under the standing authorization of the approval setting. The
// server owns every rule (the cutoff, the one open run, the states, who
// may approve); this module only maps the wire shape.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

/** The counts and date range of the history eligible at a cutoff. */
export interface ArchiveCounts {
  components: number;
  flows: number;
  movements: number;
  /** The oldest and newest Movement eligible (ISO instants); null when
   * nothing is eligible. */
  firstOccurredAt: string | null;
  lastOccurredAt: string | null;
  /** An estimate of the archive's size in bytes (0 when nothing is
   * eligible). */
  estimatedArchiveBytes: number;
}

/** One preview: what a run would archive at `cutoffDate`. */
export interface ArchivePreview extends ArchiveCounts {
  /** The site-calendar day whose start is the cutoff (`YYYY-MM-DD`). */
  cutoffDate: string;
  /** The newest cutoff date the retention period allows today. */
  latestCutoffDate: string;
  siteTimezone: string;
  durationMs: number;
  timeLimitSeconds: number;
}

interface ArchiveCountsWire {
  components: number;
  flows: number;
  movements: number;
  first_occurred_at: string | null;
  last_occurred_at: string | null;
  estimated_archive_bytes: number;
}

interface ArchivePreviewWire extends ArchiveCountsWire {
  cutoff_date: string;
  cutoff: string;
  latest_cutoff_date: string;
  site_timezone: string;
  duration_ms: number;
  time_limit_seconds: number;
}

function toCounts(wire: ArchiveCountsWire): ArchiveCounts {
  return {
    components: wire.components,
    flows: wire.flows,
    movements: wire.movements,
    firstOccurredAt: wire.first_occurred_at,
    lastOccurredAt: wire.last_occurred_at,
    estimatedArchiveBytes: wire.estimated_archive_bytes,
  };
}

/** Preview the run at `cutoffDate` (`YYYY-MM-DD`), or at the newest
 * cutoff allowed when null. Takes up to the preview time limit. */
export async function previewArchive(
  cutoffDate: string | null,
): Promise<ArchivePreview> {
  const query =
    cutoffDate === null ? '' : `?cutoff_date=${encodeURIComponent(cutoffDate)}`;
  const wire = await apiRequest<ArchivePreviewWire>(
    `/api/history-archive/preview${query}`,
  );
  return {
    ...toCounts(wire),
    cutoffDate: wire.cutoff_date,
    latestCutoffDate: wire.latest_cutoff_date,
    siteTimezone: wire.site_timezone,
    durationMs: wire.duration_ms,
    timeLimitSeconds: wire.time_limit_seconds,
  };
}

/** The run states: `PROPOSED` (an automatic run waiting for approval),
 * `APPROVED`, `RUNNING`, `WAITING_FOR_COPY` and `PURGING` are open (at
 * most one run is open); the others are final (`DISMISSED`: a proposed
 * run a person dismissed). */
export type ArchiveRunState =
  | 'PROPOSED'
  | 'APPROVED'
  | 'RUNNING'
  | 'WAITING_FOR_COPY'
  | 'PURGING'
  | 'COMPLETED'
  | 'COMPLETED_WITH_SKIPS'
  | 'NOTHING_ELIGIBLE'
  | 'DISMISSED'
  | 'CANCELLED'
  | 'FAILED';

/** Who started a run: `MANUAL` (requested in Administration) or
 * `AUTOMATIC` (created by the server's archival job). */
export type ArchiveRunTrigger = 'MANUAL' | 'AUTOMATIC';

/** The results of a completed run: `PURGED` (at least one component
 * purged), `NOTHING_PURGED` (every component skipped), `EXPORT_ONLY`
 * (runs that step 1 finished). */
export type ArchiveRunResult = 'EXPORT_ONLY' | 'PURGED' | 'NOTHING_PURGED';

export interface ArchiveRunUser {
  userId: number;
  displayName: string;
}

/** The counts of whole components, flows and Movements a purge deleted. */
export interface ArchivePurgedCounts {
  components: number;
  flows: number;
  movements: number;
}

/** The approval of a proposed (automatic) run: who, when (the server's
 * time) and the approver's own reason. */
export interface ArchiveRunApproval {
  approvedBy: ArchiveRunUser;
  approvedAt: string;
  reason: string;
}

/** The standing authorization an automatic run was started under without
 * approval: the audited change that switched the approval step off, and
 * who made it when (null when the server could not name them). */
export interface ArchiveRunAuthorization {
  auditEventId: number;
  authorizedBy: ArchiveRunUser | null;
  authorizedAt: string | null;
}

export interface ArchiveRun {
  id: number;
  /** The run's state as the server answered it (an `ArchiveRunState`;
   * a later step may add states, which the table shows as they are). */
  state: string;
  /** An `ArchiveRunTrigger`, as the server answered it. */
  trigger: string;
  cutoffDate: string;
  /** The requester's reason, or the automatic reason of an automatic run. */
  reason: string;
  /** Null exactly for an automatic run. */
  requestedBy: ArchiveRunUser | null;
  requestedAt: string;
  /** Set once a proposed run was approved. */
  approval: ArchiveRunApproval | null;
  /** Who dismissed a proposed run: set exactly when it is `DISMISSED`
   * (its time is `finishedAt`). */
  dismissedBy: ArchiveRunUser | null;
  /** Set for an automatic run started under the standing authorization. */
  authorization: ArchiveRunAuthorization | null;
  /** A `FAILED` run failed because the User of its authority (the
   * requester, the approver or the standing authorization's User) is no
   * longer active or no longer holds the key; `failureReason` says so. */
  authorizationFailed: boolean;
  /** The preview stored with the request. */
  preview: ArchiveCounts;
  startedAt: string | null;
  archiveName: string | null;
  exportedAt: string | null;
  /** Null until the export finished. */
  exported: {
    components: number;
    flows: number;
    movements: number;
    lineageEdges: number;
    assignedRoutes: number;
  } | null;
  archiveBytes: number | null;
  manifestSha256: string | null;
  verifiedAt: string | null;
  /** Null until the verification finished; the components the
   * verification and the purge skipped (they changed after the export),
   * together. */
  skipped: { components: number; flows: number } | null;
  copiedAt: string | null;
  copyConfirmedAt: string | null;
  copyConfirmedBy: ArchiveRunUser | null;
  cancelledBy: ArchiveRunUser | null;
  /** The pre-purge backup the host job took before the purge; null until
   * the purge started. */
  prePurgeBackup: { name: string; dumpStartedAt: string } | null;
  purgeStartedAt: string | null;
  /** What the purge deleted so far (raised per batch); null until the
   * purge started. */
  purged: ArchivePurgedCounts | null;
  /** The purge receipt beside the archive folder and its SHA-256; null
   * until it was written. */
  receiptName: string | null;
  receiptSha256: string | null;
  finishedAt: string | null;
  /** An `ArchiveRunResult`, set exactly when the run is `COMPLETED` or
   * `COMPLETED_WITH_SKIPS`. */
  result: string | null;
  failureReason: string | null;
}

interface UserWire {
  user_id: number;
  display_name: string;
}

interface ArchiveRunWire {
  id: number;
  state: string;
  trigger: string;
  cutoff: string;
  cutoff_date: string;
  reason: string;
  requested_by: UserWire | null;
  requested_at: string;
  approval: {
    approved_by: UserWire;
    approved_at: string;
    reason: string;
  } | null;
  dismissed_by: UserWire | null;
  authorization: {
    audit_event_id: number;
    authorized_by: UserWire | null;
    authorized_at: string | null;
  } | null;
  authorization_failed: boolean;
  preview: ArchiveCountsWire;
  started_at: string | null;
  archive_name: string | null;
  exported_at: string | null;
  exported: {
    components: number;
    flows: number;
    movements: number;
    lineage_edges: number;
    assigned_routes: number;
  } | null;
  archive_bytes: number | null;
  manifest_sha256: string | null;
  verified_at: string | null;
  skipped: { components: number; flows: number } | null;
  copied_at: string | null;
  copy_confirmed_at: string | null;
  copy_confirmed_by: UserWire | null;
  cancelled_by: UserWire | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  purge_started_at: string | null;
  purged: ArchivePurgedCounts | null;
  receipt_name: string | null;
  receipt_sha256: string | null;
  finished_at: string | null;
  result: string | null;
  failure_reason: string | null;
}

function toUser(wire: UserWire | null): ArchiveRunUser | null {
  return wire === null
    ? null
    : { userId: wire.user_id, displayName: wire.display_name };
}

function toRun(wire: ArchiveRunWire): ArchiveRun {
  return {
    id: wire.id,
    state: wire.state,
    trigger: wire.trigger,
    cutoffDate: wire.cutoff_date,
    reason: wire.reason,
    requestedBy: toUser(wire.requested_by),
    requestedAt: wire.requested_at,
    approval:
      wire.approval === null
        ? null
        : {
            approvedBy: {
              userId: wire.approval.approved_by.user_id,
              displayName: wire.approval.approved_by.display_name,
            },
            approvedAt: wire.approval.approved_at,
            reason: wire.approval.reason,
          },
    dismissedBy: toUser(wire.dismissed_by),
    authorization:
      wire.authorization === null
        ? null
        : {
            auditEventId: wire.authorization.audit_event_id,
            authorizedBy: toUser(wire.authorization.authorized_by),
            authorizedAt: wire.authorization.authorized_at,
          },
    authorizationFailed: wire.authorization_failed,
    preview: toCounts(wire.preview),
    startedAt: wire.started_at,
    archiveName: wire.archive_name,
    exportedAt: wire.exported_at,
    exported:
      wire.exported === null
        ? null
        : {
            components: wire.exported.components,
            flows: wire.exported.flows,
            movements: wire.exported.movements,
            lineageEdges: wire.exported.lineage_edges,
            assignedRoutes: wire.exported.assigned_routes,
          },
    archiveBytes: wire.archive_bytes,
    manifestSha256: wire.manifest_sha256,
    verifiedAt: wire.verified_at,
    skipped: wire.skipped,
    copiedAt: wire.copied_at,
    copyConfirmedAt: wire.copy_confirmed_at,
    copyConfirmedBy: toUser(wire.copy_confirmed_by),
    cancelledBy: toUser(wire.cancelled_by),
    prePurgeBackup:
      wire.pre_purge_backup === null
        ? null
        : {
            name: wire.pre_purge_backup.name,
            dumpStartedAt: wire.pre_purge_backup.dump_started_at,
          },
    purgeStartedAt: wire.purge_started_at,
    purged:
      wire.purged === null
        ? null
        : {
            components: wire.purged.components,
            flows: wire.purged.flows,
            movements: wire.purged.movements,
          },
    receiptName: wire.receipt_name,
    receiptSha256: wire.receipt_sha256,
    finishedAt: wire.finished_at,
    result: wire.result,
    failureReason: wire.failure_reason,
  };
}

const RUNS_PATH = '/api/history-archive/runs';

/** The newest runs first (the server's default limit). */
export async function listArchiveRuns(): Promise<ArchiveRun[]> {
  const wire = await apiRequest<ArchiveRunWire[]>(RUNS_PATH);
  return wire.map(toRun);
}

/** Request a run at `cutoffDate` with the reason; the server recomputes
 * the preview (up to its time limit) and answers the new run. */
export async function requestArchiveRun(
  reason: string,
  cutoffDate: string | null,
): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(RUNS_PATH, {
      method: 'POST',
      body: { reason, cutoff_date: cutoffDate },
    }),
  );
}

/** Cancel a run that is requested or waiting for its off-host copy (a
 * cancelled run answers unchanged; a proposed run is dismissed instead). */
export async function cancelArchiveRun(runId: number): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(`${RUNS_PATH}/${runId}/cancel`, {
      method: 'POST',
    }),
  );
}

/** Approve a proposed (automatic) run with a reason of the approver's
 * own; it becomes `APPROVED` (approving it again as the same User answers
 * unchanged). */
export async function approveArchiveRun(
  runId: number,
  reason: string,
): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(`${RUNS_PATH}/${runId}/approve`, {
      method: 'POST',
      body: { reason },
    }),
  );
}

/** Dismiss a proposed run, without a reason, as Cancel does (a dismissed
 * run answers unchanged). */
export async function dismissArchiveRun(runId: number): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(`${RUNS_PATH}/${runId}/dismiss`, {
      method: 'POST',
    }),
  );
}

/** Confirm the off-host copy of a run waiting for it, against the
 * manifest SHA-256 shown (confirming again answers unchanged). */
export async function confirmArchiveCopy(
  runId: number,
  manifestSha256: string,
): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(
      `${RUNS_PATH}/${runId}/copy-confirmation`,
      { method: 'POST', body: { manifest_sha256: manifestSha256 } },
    ),
  );
}
