import { expect, test } from 'vitest';

import type {
  TrackingArchivedHistory,
  TrackingDetail,
  TrackingFlow,
  TrackingMovement,
  TrackingMovementPage,
} from '../../api/tracking';
import { formatTimeOfDay, formatTimestampShort } from '../dates';
import {
  archivedHistoryText,
  archivedTimestamp,
  codeRuns,
  describeArchivedHistory,
  detailRevisions,
  historyEntries,
  historyEntryKey,
  routeSteps,
  timestamp,
  WHOLE_CODE_MAX_LENGTH,
} from './tracking-logic';

// The per-section revision signatures of a polled Tracking detail: a
// change means the appended (older) pages of that section are read
// again; an unchanged signature keeps them. And the archived history
// (history archival step 2): its place among the Movements of a page,
// its line and time, and the ARCHIVED route steps.

/** The figures `detailRevisions` reads; the rest of the detail is not
 * part of any signature. */
function detail(
  figures: {
    movements?: number;
    archivedMovements?: number;
    scrap?: number;
    archivedScrap?: number;
    scrapped?: number;
    flows?: number;
    allocations?: number;
    routeAdjustments?: number;
  } = {},
): TrackingDetail {
  return {
    movements: {
      total: figures.movements ?? 9,
      archivedTotal: figures.archivedMovements ?? 0,
    },
    scrapHistory: {
      total: figures.scrap ?? 2,
      archivedTotal: figures.archivedScrap ?? 0,
    },
    scrappedQuantity: figures.scrapped ?? 1,
    flows: { total: figures.flows ?? 2 },
    allocations: { total: figures.allocations ?? 0 },
    routeAdjustmentTotal: figures.routeAdjustments ?? 0,
  } as unknown as TrackingDetail;
}

test('FE-13: a route adjustment alone changes the flow revision and nothing else', () => {
  const before = detailRevisions(detail());
  const after = detailRevisions(detail({ routeAdjustments: 1 }));
  expect(after.flows).not.toBe(before.flows);
  expect(after.movements).toBe(before.movements);
  expect(after.scrap).toBe(before.scrap);
  expect(after.allocations).toBe(before.allocations);
});

test('the flow revision still follows the flow and Movement counts', () => {
  const base = detailRevisions(detail()).flows;
  expect(detailRevisions(detail({ flows: 3 })).flows).not.toBe(base);
  expect(detailRevisions(detail({ movements: 10 })).flows).not.toBe(base);
  expect(detailRevisions(detail()).flows).toBe(base);
});

test('a purge that archives history changes the history and flow revisions, even when the Movement count does not move', () => {
  const before = detailRevisions(detail());
  const purged = detailRevisions(detail({ archivedMovements: 1 }));
  expect(purged.movements).not.toBe(before.movements);
  expect(purged.flows).not.toBe(before.flows);
  expect(purged.scrap).toBe(before.scrap);
  const scrapArchived = detailRevisions(detail({ archivedScrap: 1 }));
  expect(scrapArchived.scrap).not.toBe(before.scrap);
  expect(scrapArchived.movements).toBe(before.movements);
});

function movement(id: number, occurredAt: string): TrackingMovement {
  return { id, occurredAt } as unknown as TrackingMovement;
}

function archived(
  extra: Partial<TrackingArchivedHistory> = {},
): TrackingArchivedHistory {
  return {
    quantityFlowId: 120,
    runId: 3,
    archiveName: 'run-3-20271110T042000Z',
    flowStatus: 'STOCKED',
    movementCount: 12,
    firstOccurredAt: '2025-01-03T12:00:00Z',
    lastOccurredAt: '2025-03-16T12:00:00Z',
    lastMovementId: 40,
    purgedAt: '2027-11-10T05:10:00Z',
    ...extra,
  };
}

function page(
  movements: TrackingMovement[],
  entries: TrackingArchivedHistory[],
): TrackingMovementPage {
  return {
    movements,
    total: movements.length,
    hasMore: false,
    nextBeforeMovementId: null,
    archived: entries,
    archivedTotal: entries.length,
  };
}

/** A row's place, as `m<id>` or `a<lastMovementId>`. */
function places(rows: ReturnType<typeof historyEntries>): string[] {
  return rows.map((row) =>
    row.kind === 'movement'
      ? `m${row.movement.id}`
      : `a${row.archived.lastMovementId}`,
  );
}

test('a page interleaves its Movements and archived entries newest first by (time, id)', () => {
  const rows = historyEntries(
    page(
      [
        movement(90, '2025-06-01T08:00:00Z'),
        // The same time as the archived entry below: the higher id first.
        movement(45, '2025-03-16T12:00:00Z'),
        movement(12, '2025-01-01T08:00:00Z'),
      ],
      [
        archived({
          lastMovementId: 60,
          lastOccurredAt: '2025-04-02T08:00:00Z',
        }),
        archived({
          quantityFlowId: 121,
          lastMovementId: 40,
          lastOccurredAt: '2025-03-16T12:00:00Z',
        }),
      ],
    ),
  );
  expect(places(rows)).toEqual(['m90', 'a60', 'm45', 'a40', 'm12']);
});

test('instants a microsecond apart and mixed fractions keep the server order', () => {
  const rows = historyEntries(
    page(
      [
        // 100 µs before the archived entry: milliseconds alone would tie
        // and put the higher id first.
        movement(50, '2025-03-16T12:00:00.000400Z'),
        movement(30, '2025-03-16T12:00:01Z'),
      ],
      [
        archived({
          lastMovementId: 40,
          lastOccurredAt: '2025-03-16T12:00:00.000500Z',
        }),
        // Half a second after a whole-second time written without a
        // fraction.
        archived({
          quantityFlowId: 121,
          lastMovementId: 20,
          lastOccurredAt: '2025-03-16T12:00:01.5Z',
        }),
      ],
    ),
  );
  expect(places(rows)).toEqual(['a20', 'm30', 'a40', 'm50']);
});

test('a history row key never confuses a Movement with an archived entry', () => {
  const rows = historyEntries(
    page(
      [movement(40, '2025-06-01T08:00:00Z')],
      [archived({ lastMovementId: 40 })],
    ),
  );
  expect(rows.map(historyEntryKey)).toEqual(['m-40', 'a-3-120']);
});

test('the archived line states the run, the dates and the Movements as the server recorded them', () => {
  expect(archivedHistoryText(archived())).toBe(
    'History archived in run 3 (Jan 03, 2025 – Mar 16, 2025, 12 Movements)',
  );
  expect(
    archivedHistoryText(
      archived({
        movementCount: 1,
        firstOccurredAt: '2025-03-16T12:00:00Z',
        lastOccurredAt: '2025-03-16T12:30:00Z',
      }),
    ),
  ).toBe('History archived in run 3 (Mar 16, 2025, 1 Movement)');
  expect(archivedHistoryText(archived({ movementCount: 4210 }))).toBe(
    'History archived in run 3 (Jan 03, 2025 – Mar 16, 2025, 4,210 Movements)',
  );
  expect(describeArchivedHistory(archived())).toBe(
    'History archived in run 3 (Jan 03, 2025 – Mar 16, 2025, 12 Movements) · QF-120 · run-3-20271110T042000Z',
  );
  expect(describeArchivedHistory(archived({ archiveName: null }))).toBe(
    'History archived in run 3 (Jan 03, 2025 – Mar 16, 2025, 12 Movements) · QF-120',
  );
});

test('an archived entry’s time always shows its year; the other history rows keep the short form', () => {
  // Archived history is older than the retention period: without the year
  // a row from 2023 would read as this year.
  expect(archivedTimestamp('2023-11-18T12:06:00Z')).toBe(
    `Nov 18, 2023 ${formatTimeOfDay('2023-11-18T12:06:00Z')}`,
  );
  expect(timestamp('2023-11-18T12:06:00Z')).toBe(
    formatTimestampShort('2023-11-18T12:06:00Z'),
  );
  expect(timestamp('2023-11-18T12:06:00Z')).not.toContain('2023');
});

test('the snapshot steps of an archived flow read archived — neither done nor future', () => {
  const step = (id: number, state: string) => ({
    id,
    sequence: id,
    area: { id, name: `Area ${id}`, color: null, isTerminal: false },
    operation: null,
    state,
  });
  const flow = {
    routeMode: 'PLANNED',
    routeSteps: [step(1, 'ARCHIVED'), step(2, 'ARCHIVED')],
    trace: [],
    position: null,
  } as unknown as TrackingFlow;
  expect(routeSteps(flow).map((view) => view.state)).toEqual([
    'archived',
    'archived',
  ]);
  const live = {
    ...flow,
    routeSteps: [step(1, 'DONE'), step(2, 'CURRENT'), step(3, 'FUTURE')],
  } as unknown as TrackingFlow;
  expect(routeSteps(live).map((view) => view.state)).toEqual([
    'done',
    'cur',
    'future',
  ]);
});

test('a history row keeps its codes whole: every hyphenated run of characters up to the limit, the text unchanged', () => {
  const text =
    'QC → PACK · qty 10 · QF-44 · W: Anna-Lee · ST-PACK · reason: rework — re-cut';
  const runs = codeRuns(text);
  expect(runs.map((run) => run.text).join('')).toBe(text);
  expect(runs.filter((run) => run.code).map((run) => run.text)).toEqual([
    'QF-44',
    'Anna-Lee',
    'ST-PACK',
    're-cut',
  ]);
  // A dash between words and a lone hyphen are not codes.
  expect(codeRuns('Lathe — ready · -3 · a--b')).toEqual([
    { text: 'Lathe — ready · -3 · a--b', code: false },
  ]);
  // A code longer than the limit may still break at its hyphens.
  const long = `PN-${'X'.repeat(WHOLE_CODE_MAX_LENGTH)}`;
  expect(codeRuns(`${long} · QF-1`)).toEqual([
    { text: `${long} · `, code: false },
    { text: 'QF-1', code: true },
  ]);
  expect(codeRuns('')).toEqual([]);
});
