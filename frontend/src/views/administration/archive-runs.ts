// Presentation of the history archival runs and previews of
// Administration → History archival & purge (Phase 16, archival design B
// steps 1, 2, 4 and 5): the plain words of each run state and result, who
// started a run and on whose authority (a requester, or an automatic run
// with its approval or its standing authorization), the counts
// (exported, purged, skipped), sizes and times, the purge's evidence
// (the pre-purge backup and the receipt), and the viewing of a run's
// archive (its newest view, and what Open for viewing does). Presentation
// only — the server owns every rule and decides what a run or a view may
// do; the actions offered here follow the states it answers.

import type { ArchiveCounts, ArchiveRun } from '../../api/history-archive';
import type {
  ArchiveView,
  ArchiveViewState,
} from '../../api/history-archive-views';
import { formatIsoDate, formatTimeOfDay, todayIso } from '../dates';

/** The disclosure of a pre-purge backup in the runs and requests tables.
 * Its hyphen is a non-breaking one (U+2011): a narrow column would
 * otherwise break the word `Pre-purge` after it. */
export const PRE_PURGE_BACKUP_LABEL = 'Pre‑purge backup';

/** What follows a purge, said on the Preview card and before a run is
 * requested or approved. */
export const AFTER_PURGE =
  'Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.';

/** What a run at `cutoffDate` does, said before it is requested or
 * approved. */
export function runEffectText(cutoffDate: string): string {
  return `The run exports the Movement history before ${formatIsoDate(cutoffDate)} to archive files on the server and, after the off-host copy and a pre-purge backup, deletes it from the database. ${AFTER_PURGE}`;
}

/** The plain words of a run's state. */
export function runStateLabel(run: ArchiveRun): string {
  switch (run.state) {
    case 'PROPOSED':
      return 'Proposed — waiting for approval';
    case 'APPROVED':
      // An approved proposal, a run started under the standing
      // authorization, or a requested run (the request is its approval).
      return 'Approved — waiting for the job';
    case 'RUNNING':
      return 'Running';
    case 'WAITING_FOR_COPY':
      // A non-breaking hyphen (U+2011) keeps `off‑host` whole when the
      // pill wraps in the State column.
      return run.copyConfirmedAt === null
        ? 'Waiting for the off‑host copy'
        : 'Copy confirmed — waiting for the job';
    case 'PURGING':
      return 'Purging';
    case 'COMPLETED':
      return 'Completed';
    case 'COMPLETED_WITH_SKIPS':
      return 'Completed with skips';
    case 'NOTHING_ELIGIBLE':
      return 'Nothing eligible';
    case 'DISMISSED':
      return 'Dismissed';
    case 'CANCELLED':
      return 'Cancelled';
    case 'FAILED':
      return 'Failed';
    default:
      return run.state;
  }
}

/** The status-pill tone of a run's state. */
export function runStateTone(run: ArchiveRun): 'on' | 'off' | 'term' | 'err' {
  switch (run.state) {
    case 'COMPLETED':
    case 'COMPLETED_WITH_SKIPS':
      return 'on';
    case 'FAILED':
      return 'err';
    case 'PROPOSED':
    case 'APPROVED':
    case 'RUNNING':
    case 'WAITING_FOR_COPY':
    case 'PURGING':
      return 'term';
    default:
      return 'off';
  }
}

/** A proposed (automatic) run waits for a person to approve it with a
 * reason of their own, or to dismiss it; it is never cancelled. */
export function runAwaitsApproval(run: ArchiveRun): boolean {
  return run.state === 'PROPOSED';
}

/** A requested run, or one waiting for its off-host copy, may be
 * cancelled — never a proposed one (it is dismissed instead) nor one the
 * job works on (`RUNNING`, `PURGING`); the server decides and a refusal
 * is shown. */
export function runCanBeCancelled(run: ArchiveRun): boolean {
  return run.state === 'APPROVED' || run.state === 'WAITING_FOR_COPY';
}

/** Who started a run: the requester, or `Automatic` for a run the
 * server's archival job created. */
export function runRequester(run: ArchiveRun): string {
  return run.requestedBy === null || run.trigger === 'AUTOMATIC'
    ? 'Automatic'
    : run.requestedBy.displayName;
}

/** An automatic run started under the standing authorization, without
 * approval: `Started without approval (authorization of Ann Admin, Nov
 * 18, 2026)`; null for every other run. */
export function startedWithoutApprovalText(run: ArchiveRun): string | null {
  const { authorization } = run;
  if (authorization === null) return null;
  const who =
    authorization.authorizedBy?.displayName ??
    `audit row ${authorization.auditEventId}`;
  return authorization.authorizedAt === null
    ? `Started without approval (authorization of ${who})`
    : `Started without approval (authorization of ${who}, ${formatInstantDate(authorization.authorizedAt)})`;
}

/** A run waiting for its off-host copy that nobody confirmed yet. */
export function runAwaitsCopyConfirmation(run: ArchiveRun): boolean {
  return (
    run.state === 'WAITING_FOR_COPY' &&
    run.copyConfirmedAt === null &&
    run.manifestSha256 !== null
  );
}

/** The plain words of a completed run's result. */
function resultLabel(result: string | null): string {
  switch (result) {
    case 'PURGED':
      return 'Archived and purged.';
    case 'NOTHING_PURGED':
      return 'Nothing purged (every component changed).';
    case 'EXPORT_ONLY':
      return 'Exported to files; nothing deleted.';
    default:
      return `Completed (${result ?? 'no result'}).`;
  }
}

/** What a finished run came to, in plain words; null while it is open. */
export function runOutcome(run: ArchiveRun): string | null {
  if (run.state === 'COMPLETED' || run.state === 'COMPLETED_WITH_SKIPS') {
    return resultLabel(run.result);
  }
  if (run.state === 'FAILED') return run.failureReason ?? 'Failed.';
  if (run.state === 'NOTHING_ELIGIBLE') {
    return 'Nothing was eligible at the cutoff.';
  }
  if (run.state === 'DISMISSED') {
    return run.dismissedBy === null
      ? 'Dismissed.'
      : `Dismissed by ${run.dismissedBy.displayName}.`;
  }
  if (run.state === 'CANCELLED') {
    return run.cancelledBy === null
      ? 'Cancelled.'
      : `Cancelled by ${run.cancelledBy.displayName}.`;
  }
  return null;
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString('en-US')} ${noun}${count === 1 ? '' : 's'}`;
}

/** `120 components · 310 flows · 4,210 Movements`. */
export function countsLine(counts: {
  components: number;
  flows: number;
  movements: number;
}): string {
  return [
    plural(counts.components, 'component'),
    plural(counts.flows, 'flow'),
    plural(counts.movements, 'Movement'),
  ].join(' · ');
}

/** `812 KB`, `1.4 MB`, `2.1 GB` (1024-based, as the import's file size). */
export function formatArchiveSize(bytes: number): string {
  if (bytes < 1024) return bytes === 1 ? '1 byte' : `${bytes} bytes`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

/** The local date of an instant: `Nov 17, 2026`; null → `—`. */
export function formatInstantDate(iso: string | null): string {
  return iso === null ? '—' : formatIsoDate(todayIso(new Date(iso).getTime()));
}

/** The local date and time of an instant: `Nov 17, 2026 08:15`. */
export function formatInstant(iso: string): string {
  return `${formatInstantDate(iso)} ${formatTimeOfDay(iso)}`;
}

/** How long a preview took: `0.4 s`, `12 s`, `2 min 05 s`. */
export function formatPreviewDuration(ms: number): string {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s`;
}

/** The date range of the eligible Movements: `Jan 3, 2023 – Nov 16,
 * 2024`; `—` when nothing is eligible. */
export function eligibleRange(counts: ArchiveCounts): string {
  if (counts.firstOccurredAt === null || counts.lastOccurredAt === null) {
    return '—';
  }
  return `${formatInstantDate(counts.firstOccurredAt)} – ${formatInstantDate(counts.lastOccurredAt)}`;
}

/** The times a run reached, oldest first, as label → instant. */
export function runTimes(run: ArchiveRun): { label: string; at: string }[] {
  const times: { label: string; at: string | null }[] = [
    { label: 'Requested', at: run.requestedAt },
    { label: 'Approved', at: run.approval?.approvedAt ?? null },
    { label: 'Started', at: run.startedAt },
    { label: 'Exported', at: run.exportedAt },
    { label: 'Verified', at: run.verifiedAt },
    { label: 'Copied', at: run.copiedAt },
    { label: 'Copy confirmed', at: run.copyConfirmedAt },
    { label: 'Backup started', at: run.prePurgeBackup?.dumpStartedAt ?? null },
    { label: 'Purge started', at: run.purgeStartedAt },
    { label: 'Finished', at: run.finishedAt },
  ];
  return times.filter(
    (time): time is { label: string; at: string } => time.at !== null,
  );
}

/** `4,210 Movements`. */
export function movementsText(movements: number): string {
  return plural(movements, 'Movement');
}

/** `120 components · 310 flows`. */
export function componentsAndFlows(counts: {
  components: number;
  flows: number;
}): string {
  return `${plural(counts.components, 'component')} · ${plural(counts.flows, 'flow')}`;
}

/** The counts a runs-table row shows: the exported numbers once there
 * are any, otherwise the preview's; `previewLine` says when the preview
 * needs a line of its own (`only`: the numbers are the preview's;
 * `differs`: the export differs from the preview). */
export function runCounts(run: ArchiveRun): {
  shown: { components: number; flows: number; movements: number };
  previewLine: 'only' | 'differs' | null;
} {
  if (run.exported === null) return { shown: run.preview, previewLine: 'only' };
  const differs =
    run.exported.components !== run.preview.components ||
    run.exported.flows !== run.preview.flows ||
    run.exported.movements !== run.preview.movements;
  return { shown: run.exported, previewLine: differs ? 'differs' : null };
}

/** The latest step a run reached after its request (Finished, Purge
 * started, Verified, Exported, Started or Approved), or null when it has
 * neither started nor been approved. */
export function latestRunStep(
  run: ArchiveRun,
): { label: string; at: string } | null {
  const steps: { label: string; at: string | null }[] = [
    { label: 'Finished', at: run.finishedAt },
    { label: 'Purge started', at: run.purgeStartedAt },
    { label: 'Verified', at: run.verifiedAt },
    { label: 'Exported', at: run.exportedAt },
    { label: 'Started', at: run.startedAt },
    { label: 'Approved', at: run.approval?.approvedAt ?? null },
  ];
  const latest = steps.find((step) => step.at !== null);
  return latest ? { label: latest.label, at: latest.at as string } : null;
}

/** The purged counts of a run whose purge started, as the server counts
 * them: `Purged: 4,190 Movements · 118 components · 305 flows` (`Purged so
 * far` while it purges); null before the purge. */
export function purgedLine(run: ArchiveRun): string | null {
  const { purged } = run;
  if (purged === null) return null;
  const label = run.state === 'PURGING' ? 'Purged so far' : 'Purged';
  return `${label}: ${movementsText(purged.movements)} · ${componentsAndFlows(purged)}`;
}

// ---------------------------------------------------------------------------
// Viewing a run's archive (archival design D16, build step 5)
// ---------------------------------------------------------------------------

/** The plain words of a view's state (a run's newest view). */
export function viewStateLabel(state: ArchiveViewState): string {
  switch (state) {
    case 'REQUESTED':
      return 'Waiting to be loaded for viewing';
    case 'LOADING':
      return 'Being loaded for viewing';
    case 'OPEN':
      return 'Open for viewing';
    case 'CLOSING':
      return 'Viewing closed — removing the copy';
    case 'CLOSED':
      return 'Viewing closed';
    case 'FAILED':
      return 'Loading for viewing failed';
    case 'CANCELLED':
      return 'Viewing cancelled';
  }
}

/** The status-pill tone of a view's state. */
export function viewStateTone(
  state: ArchiveViewState,
): 'on' | 'off' | 'term' | 'err' {
  switch (state) {
    case 'OPEN':
      return 'on';
    case 'REQUESTED':
    case 'LOADING':
    case 'CLOSING':
      return 'term';
    case 'FAILED':
      return 'err';
    default:
      return 'off';
  }
}

/** A view waiting to be loaded, being loaded or open: the run's one active
 * view (the server allows no second one). */
export function viewIsActive(state: ArchiveViewState): boolean {
  return state === 'REQUESTED' || state === 'LOADING' || state === 'OPEN';
}

/** Open for viewing is offered on a run the server marks viewable (a
 * finished run whose archive was verified) while it has no active view;
 * the runs loaded at once are the server's to judge, and its refusal is
 * shown as given. */
export function runCanBeOpenedForViewing(run: ArchiveRun): boolean {
  return run.viewable && (run.view === null || !viewIsActive(run.view.state));
}

/** The line under a view's state: until when an open view stays open, why
 * its load failed, who closed or cancelled it; null when the state says
 * it all. */
export function viewDetailText(view: ArchiveView): string | null {
  switch (view.state) {
    case 'OPEN':
      return view.openUntil === null
        ? null
        : `Until ${formatInstant(view.openUntil)}`;
    case 'FAILED':
      return view.failureReason;
    case 'CLOSING':
    case 'CLOSED':
      return view.closedBy === null
        ? 'Its days ran out.'
        : `Closed by ${view.closedBy.displayName}.`;
    case 'CANCELLED':
      return view.cancelledBy === null
        ? null
        : `Cancelled by ${view.cancelledBy.displayName}.`;
    default:
      return null;
  }
}

/** A loaded archive takes roughly this many times its archive's size in
 * the database: the backend contract of build step 5 (§5.3) measured
 * 1.34 GB for a 184 MB archive of 2.0 million Movements and lets the
 * confirmation of Open for viewing say so before a run is opened. */
export const VIEW_SPACE_FACTOR = 7;

/** `7 days`, `1 day`. */
function daysText(days: number): string {
  return `${days} day${days === 1 ? '' : 's'}`;
}

/** What Open for viewing does, said before a run is opened: a separate,
 * read-only copy that never joins live history; the database space it
 * takes until it is closed; and when it closes (`viewDays`, the setting;
 * null when the settings are not loaded). */
export function openForViewingText(
  run: ArchiveRun,
  viewDays: number | null,
): string[] {
  const space =
    run.archiveBytes === null
      ? 'Until it is closed, the copy takes space in the database.'
      : `Until it is closed, the copy takes space in the database: roughly ${VIEW_SPACE_FACTOR} times the archive's size, about ${formatArchiveSize(run.archiveBytes * VIEW_SPACE_FACTOR)} for this run (${
          run.exported === null
            ? ''
            : `${movementsText(run.exported.movements)} in ${plural(run.exported.flows, 'flow')}, `
        }an archive of ${formatArchiveSize(run.archiveBytes)}).`;
  return [
    "The archival job on the server loads a copy of this run's archive within minutes, for reading on a page of its own. The copy is read-only and separate from the live history: nothing is added to the live history, and Tracking and every figure stay as they are.",
    space,
    viewDays === null
      ? 'It closes by itself after the days set under Retention and schedule, or earlier with Close viewing.'
      : `It closes by itself ${daysText(viewDays)} after it opens, or earlier with Close viewing.`,
  ];
}
