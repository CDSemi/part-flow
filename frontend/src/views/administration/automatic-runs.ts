// Presentation of the automatic runs of Administration → History archival
// & purge (archival design B step 4, D9–D11): the plain words of each
// measure and of its limit, the limit field of each measure in the Edit
// dialog, the standing authorization in words, and the phrase that
// confirms switching the approval step off. Presentation only — the
// server validates every value, checks the confirmation, judges the
// standing authorization and decides when a run is needed; its messages
// are shown as given.

import { ARCHIVE_MEASURES } from '../../api/policies';
import type {
  ArchiveMeasure,
  RetentionPolicy,
  StandingAuthorization,
} from '../../api/policies';
import { formatInstant } from './archive-runs';

/** What a holder of the key types to switch the approval step off (the
 * server compares it without regard to letter case or spacing). */
export const APPROVAL_OFF_CONFIRMATION = 'RUN WITHOUT APPROVAL';

/** The setting that holds each measure's own limit. */
export const MEASURE_LIMIT: Record<
  ArchiveMeasure,
  'limitMovementRows' | 'limitHistoryMonths' | 'limitDatabaseGib'
> = {
  LIVE_MOVEMENT_ROWS: 'limitMovementRows',
  RETAINED_HISTORY_AGE: 'limitHistoryMonths',
  DATABASE_SIZE: 'limitDatabaseGib',
};

/** The plain words of each measure. */
export const MEASURE_LABELS: Record<ArchiveMeasure, string> = {
  LIVE_MOVEMENT_ROWS: 'Live Movement rows',
  RETAINED_HISTORY_AGE: 'Age of retained history',
  DATABASE_SIZE: 'Database size',
};

/** The limit field of each measure in the Edit dialog, and what it does. */
export const LIMIT_FIELDS: Record<
  ArchiveMeasure,
  { label: string; help: string }
> = {
  LIVE_MOVEMENT_ROWS: {
    label: 'Live Movement rows above',
    help: 'A run is needed when the database holds more live Movement rows than this; 0 means whenever something is eligible.',
  },
  RETAINED_HISTORY_AGE: {
    label: 'Retained history older than the cutoff by more than (months)',
    help: 'A run is needed when the oldest Movement still in the database is older than the retention cutoff by more than this; 0 means whenever something is eligible.',
  },
  DATABASE_SIZE: {
    label: 'Database size above (GiB)',
    help: 'A run is needed when the database is larger than this; 0 means whenever something is eligible. A purge lets the database reuse the space it frees but does not make it smaller.',
  },
};

export function isArchiveMeasure(value: string): value is ArchiveMeasure {
  return (ARCHIVE_MEASURES as readonly string[]).includes(value);
}

/** The plain words of a measure; one this page does not know, as the
 * server answered it. */
export function measureLabel(measure: string): string {
  return isArchiveMeasure(measure) ? MEASURE_LABELS[measure] : measure;
}

function months(count: number): string {
  return `${count} ${count === 1 ? 'month' : 'months'}`;
}

/** The stored measure's limit, which the measure passes when it is above
 * it: `5,000,000 rows`, `1 month older than the cutoff`, `20 GiB` (short
 * enough to stay on one line in a card at phone width). */
export function limitText(policy: RetentionPolicy): string {
  switch (policy.measure) {
    case 'LIVE_MOVEMENT_ROWS': {
      const rows = policy.limitMovementRows;
      return `${rows.toLocaleString('en-US')} ${rows === 1 ? 'row' : 'rows'}`;
    }
    case 'RETAINED_HISTORY_AGE':
      return `${months(policy.limitHistoryMonths)} older than the cutoff`;
    case 'DATABASE_SIZE':
      return `${policy.limitDatabaseGib.toLocaleString('en-US')} GiB`;
    default:
      return '—';
  }
}

/** Who gave the standing authorization, when and why: `Given by Ann
 * Admin on Nov 18, 2026 09:12. Reason: Weekly archival`. */
export function standingAuthorizationText(
  authorization: StandingAuthorization,
): string {
  const who =
    authorization.authorizedBy === null
      ? `Given in audit row ${authorization.auditEventId}`
      : `Given by ${authorization.authorizedBy.displayName}`;
  const when =
    authorization.authorizedAt === null
      ? ''
      : ` on ${formatInstant(authorization.authorizedAt)}`;
  const why =
    authorization.reason === null ? '.' : `. Reason: ${authorization.reason}`;
  return `${who}${when}${why}`;
}
