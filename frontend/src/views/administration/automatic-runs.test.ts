import { expect, test } from 'vitest';

import type { RetentionPolicy } from '../../api/policies';
import { formatInstant } from './archive-runs';
import {
  limitText,
  measureLabel,
  standingAuthorizationText,
} from './automatic-runs';

// The wording of the automatic runs of History archival & purge
// (archival step 4): each measure with its own limit, in units short
// enough for a card at phone width, and the standing authorization as
// the server answers it (who, when, why).

const POLICY: RetentionPolicy = {
  retentionPeriodMonths: 24,
  shortestRetentionMonths: 12,
  startMinute: 20,
  previewSeconds: 90,
  maxWaitDays: 7,
  purgeBatchMovements: 50000,
  purgeBatchPartNumbers: 20,
  automaticRuns: true,
  measure: 'LIVE_MOVEMENT_ROWS',
  limitMovementRows: 5000000,
  limitHistoryMonths: 1,
  limitDatabaseGib: 20,
  approvalRequired: true,
  intervalDays: 7,
  viewDays: 7,
  viewMaxLoaded: 1,
  standingAuthorization: null,
  updatedAt: '2026-11-18T08:00:00Z',
};

test('AR-1: each measure shows its own limit, in its unit', () => {
  expect(limitText(POLICY)).toBe('5,000,000 rows');
  expect(limitText({ ...POLICY, limitMovementRows: 1 })).toBe('1 row');
  expect(limitText({ ...POLICY, measure: 'RETAINED_HISTORY_AGE' })).toBe(
    '1 month older than the cutoff',
  );
  expect(
    limitText({
      ...POLICY,
      measure: 'RETAINED_HISTORY_AGE',
      limitHistoryMonths: 0,
    }),
  ).toBe('0 months older than the cutoff');
  expect(
    limitText({ ...POLICY, measure: 'DATABASE_SIZE', limitDatabaseGib: 1500 }),
  ).toBe('1,500 GiB');
  // A measure this page does not know: as the server answered it.
  expect(measureLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW');
  expect(limitText({ ...POLICY, measure: 'SOMETHING_NEW' })).toBe('—');
  expect(measureLabel('RETAINED_HISTORY_AGE')).toBe('Age of retained history');
});

test('AR-2: the standing authorization names who gave it, when and why', () => {
  const at = '2026-11-10T12:00:00Z';
  expect(
    standingAuthorizationText({
      auditEventId: 12,
      authorizedBy: { userId: 4, displayName: 'Ann Admin' },
      authorizedAt: at,
      reason: 'Weekly archival',
      valid: true,
      problem: null,
    }),
  ).toBe(`Given by Ann Admin on ${formatInstant(at)}. Reason: Weekly archival`);
  // The server may not name the User or the time: the audit row stands in.
  expect(
    standingAuthorizationText({
      auditEventId: 12,
      authorizedBy: null,
      authorizedAt: null,
      reason: null,
      valid: false,
      problem: 'No User.',
    }),
  ).toBe('Given in audit row 12.');
});
