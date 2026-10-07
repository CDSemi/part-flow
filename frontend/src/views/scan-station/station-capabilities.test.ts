import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { STATION_PERMISSIONS } from '../../api/scan-station';
import {
  STATION_ACTION_KEY,
  stationActionRefusal,
  stationCan,
} from './station-capabilities';
import type { StationAction } from './station-capabilities';

// What an enrolled Scan Station may do (Phase 14 slice 4): the frozen
// action → key table mirrors the server's command → key table, the
// client refusal text equals the server's, and the Scan Station never
// reads a user, a role, the permission vocabulary or the sign-in.

test('the action → key table is the frozen mirror of the server table', () => {
  expect(STATION_ACTION_KEY).toEqual({
    RECEIVE: 'RECEIVE_QUANTITY',
    ASSIGN: 'ASSIGN_QUANTITY_TO_MACHINE',
    QUEUE: 'ASSIGN_QUANTITY_TO_MACHINE',
    DONE: 'CONFIRM_QUANTITY',
    TRANSFER: 'CONFIRM_QUANTITY',
    REPAIR: 'CONFIRM_QUANTITY',
    COMBINE: 'CONFIRM_QUANTITY',
    SCRAP: 'CONFIRM_QUANTITY',
    ADD_QUANTITY: 'CONFIRM_QUANTITY',
    STOCK: 'COMPLETE_INTO_STOCKROOM',
    ALLOCATE: 'CONFIRM_SUGGESTED_ALLOCATION',
    ADJUST_ALLOCATION: 'ADJUST_SUGGESTED_ALLOCATION',
    UNDO: 'UNDO_RECENT_SCANS',
  });
  expect(Object.isFrozen(STATION_ACTION_KEY)).toBe(true);
  // Every key is a station key; the scan keys gate no hidden action.
  for (const key of Object.values(STATION_ACTION_KEY)) {
    expect(STATION_PERMISSIONS).toContain(key);
  }
});

test.each(Object.keys(STATION_ACTION_KEY) as StationAction[])(
  'FS-13: %s is offered exactly while its key is granted',
  (action) => {
    expect(stationCan(action, [...STATION_PERMISSIONS])).toBe(true);
    expect(
      stationCan(
        action,
        STATION_PERMISSIONS.filter((key) => key !== STATION_ACTION_KEY[action]),
      ),
    ).toBe(false);
    expect(stationCan(action, [])).toBe(false);
  },
);

test('the client refusal of a scan-led action is the server text', () => {
  expect(stationActionRefusal('TRANSFER')).toBe(
    'Scan Stations are not allowed to confirm quantity. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.',
  );
  expect(stationActionRefusal('RECEIVE')).toBe(
    'Scan Stations are not allowed to receive quantity. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.',
  );
  expect(stationActionRefusal('QUEUE')).toBe(
    'Scan Stations are not allowed to assign quantity to Machines. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.',
  );
  expect(stationActionRefusal('STOCK')).toBe(
    'Scan Stations are not allowed to complete production into the Stockroom. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.',
  );
});

const stationDir = dirname(fileURLToPath(import.meta.url));

test('FS-12: the Scan Station reads no user, role or sign-in module (the DEV demo badges excepted)', () => {
  const offenders = readdirSync(stationDir)
    .filter((file) => /\.(ts|tsx)$/.test(file) && !/\.test\.tsx?$/.test(file))
    // Development-only convenience (compiled away in production builds):
    // the demo badges keep their slice-2 session check.
    .filter((file) => file !== 'scan-station-dev-badges.tsx')
    .filter((file) =>
      /from '[./]*(api\/(users|roles|session)|app\/session-(context|provider))'/.test(
        readFileSync(join(stationDir, file), 'utf8'),
      ),
    );
  expect(offenders).toEqual([]);
});
