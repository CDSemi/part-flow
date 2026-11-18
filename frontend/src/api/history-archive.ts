// History archival runs API (Administration → History archival & purge;
// Phase 16, archival design B step 1).
//
// What a run would archive at a cutoff (the preview), the runs newest
// first, requesting a run (the request is the approval: the server
// recomputes and stores the preview), cancelling a run, and confirming
// the off-host copy of a run waiting for it. The settings of the section
// are `/api/policies/data-retention` (`api/policies.ts`). In step 1 a run
// exports the history to archive files and deletes nothing: a completed
// run's result is `EXPORT_ONLY`. The server owns every rule (the cutoff,
// the one open run, the states); this module only maps the wire shape.
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

/** The run states of step 1. */
export type ArchiveRunState =
  | 'APPROVED'
  | 'RUNNING'
  | 'WAITING_FOR_COPY'
  | 'COMPLETED'
  | 'NOTHING_ELIGIBLE'
  | 'CANCELLED'
  | 'FAILED';

export interface ArchiveRunUser {
  userId: number;
  displayName: string;
}

export interface ArchiveRun {
  id: number;
  /** The run's state as the server answered it (an `ArchiveRunState`;
   * a later step may add states, which the table shows as they are). */
  state: string;
  cutoffDate: string;
  reason: string;
  requestedBy: ArchiveRunUser;
  requestedAt: string;
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
  /** Null until the verification finished. */
  skipped: { components: number; flows: number } | null;
  copiedAt: string | null;
  copyConfirmedAt: string | null;
  copyConfirmedBy: ArchiveRunUser | null;
  cancelledBy: ArchiveRunUser | null;
  finishedAt: string | null;
  /** `EXPORT_ONLY` exactly when the run is `COMPLETED`. */
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
  cutoff: string;
  cutoff_date: string;
  reason: string;
  requested_by: UserWire;
  requested_at: string;
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
    cutoffDate: wire.cutoff_date,
    reason: wire.reason,
    requestedBy: {
      userId: wire.requested_by.user_id,
      displayName: wire.requested_by.display_name,
    },
    requestedAt: wire.requested_at,
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
 * cancelled run answers unchanged). */
export async function cancelArchiveRun(runId: number): Promise<ArchiveRun> {
  return toRun(
    await apiRequest<ArchiveRunWire>(`${RUNS_PATH}/${runId}/cancel`, {
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
