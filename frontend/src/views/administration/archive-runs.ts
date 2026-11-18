// Presentation of the history archival runs and previews of
// Administration → History archival & purge (Phase 16, archival design B
// steps 1 and 2): the plain words of each run state and result, the
// counts (exported, purged, skipped), sizes and times, and the purge's
// evidence (the pre-purge backup and the receipt). Presentation only —
// the server owns every rule and decides what a run may do; the actions
// offered here follow the states it answers.

import type { ArchiveCounts, ArchiveRun } from '../../api/history-archive';
import { formatIsoDate, formatTimeOfDay, todayIso } from '../dates';

/** The plain words of a run's state. */
export function runStateLabel(run: ArchiveRun): string {
  switch (run.state) {
    case 'APPROVED':
      return 'Requested — waiting for the job';
    case 'RUNNING':
      return 'Running';
    case 'WAITING_FOR_COPY':
      return run.copyConfirmedAt === null
        ? 'Waiting for the off-host copy'
        : 'Copy confirmed — waiting for the job';
    case 'PURGING':
      return 'Purging';
    case 'COMPLETED':
      return 'Completed';
    case 'COMPLETED_WITH_SKIPS':
      return 'Completed with skips';
    case 'NOTHING_ELIGIBLE':
      return 'Nothing eligible';
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
    case 'APPROVED':
    case 'RUNNING':
    case 'WAITING_FOR_COPY':
    case 'PURGING':
      return 'term';
    default:
      return 'off';
  }
}

/** A requested run, or one waiting for its off-host copy, may be
 * cancelled — never one the job works on (`RUNNING`, `PURGING`); the
 * server decides and a refusal is shown. */
export function runCanBeCancelled(run: ArchiveRun): boolean {
  return run.state === 'APPROVED' || run.state === 'WAITING_FOR_COPY';
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
 * started, Verified, Exported or Started), or null when it has not
 * started. */
export function latestRunStep(
  run: ArchiveRun,
): { label: string; at: string } | null {
  const steps: { label: string; at: string | null }[] = [
    { label: 'Finished', at: run.finishedAt },
    { label: 'Purge started', at: run.purgeStartedAt },
    { label: 'Verified', at: run.verifiedAt },
    { label: 'Exported', at: run.exportedAt },
    { label: 'Started', at: run.startedAt },
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
