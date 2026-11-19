import { expect, test } from 'vitest';

import type { ArchiveRun } from '../../api/history-archive';
import type {
  ArchivedFlow,
  ArchivedMovement,
  ArchiveView,
} from '../../api/history-archive-views';
import {
  formatInstant,
  openForViewingText,
  runCanBeOpenedForViewing,
  viewDetailText,
} from './archive-runs';
import {
  describeArchivedMovement,
  EMPTY_SEARCH_FORM,
  flowRecordLines,
  flowStatusLine,
  operationName,
  parseFlowNumber,
  searchOf,
  viewCopyText,
  workOrdersText,
} from './archive-viewer';

// The presentation of viewing an archive (archival step 5, D16): the
// search form's terms as the server takes them, names the archive does not
// hold, an archived Movement and flow as Tracking words live ones, the copy
// the viewer reads, and what Open for viewing says and offers.

const MOVEMENT: ArchivedMovement = {
  id: 3,
  quantityFlowId: 5513,
  partNumber: 'AD-0001',
  movementType: 'TRANSFERRED',
  quantity: 4,
  fromArea: { id: 3, name: 'MAT' },
  toArea: { id: 5, name: null },
  operation: { id: 7, code: 'OP-MAT', name: null },
  sourceMachine: null,
  destinationMachine: null,
  stationId: null,
  worker: { id: 8, name: null },
  occurredAt: '2023-11-03T08:00:00Z',
  deviceEventId: 'evt-3',
  commandSequence: 1,
  movementReason: null,
  reason: null,
  reversesMovementId: null,
  reversedByMovementId: null,
  assignedRouteStep: { id: 42, sequence: null },
  routeDeviation: null,
  lineage: [],
  demand: null,
  actorUser: { userId: 6, displayName: null },
};

test('AV-1: a flow is typed by its number or as Tracking writes it', () => {
  expect(parseFlowNumber('')).toBeNull();
  expect(parseFlowNumber('  ')).toBeNull();
  expect(parseFlowNumber('5512')).toBe(5512);
  expect(parseFlowNumber(' QF-5512 ')).toBe(5512);
  expect(parseFlowNumber('qf5512')).toBe(5512);
  for (const text of ['QF-', 'five', '1.5', '-3', '99999999999999999999']) {
    expect(parseFlowNumber(text)).toBe('invalid');
  }
});

test('AV-2: the search sends only the filled terms, the Work Order Number exactly as typed', () => {
  expect(searchOf(EMPTY_SEARCH_FORM)).toEqual({ search: {} });
  expect(
    searchOf({
      partNumber: 'ad 0001',
      workOrderNumber: ' WO-1 ',
      flow: 'QF-5512',
      areaId: '3',
      operationId: '7',
      machineId: '11',
      workerId: '8',
      dateFrom: '2023-01-01',
      dateTo: '2023-12-31',
    }),
  ).toEqual({
    search: {
      partNumber: 'ad 0001',
      workOrderNumber: ' WO-1 ',
      quantityFlowId: 5512,
      areaId: 3,
      operationId: 7,
      machineId: 11,
      workerId: 8,
      dateFrom: '2023-01-01',
      dateTo: '2023-12-31',
    },
  });
  // A blank text field asks for nothing.
  expect(
    searchOf({ ...EMPTY_SEARCH_FORM, partNumber: '  ', workOrderNumber: ' ' }),
  ).toEqual({ search: {} });
  expect(searchOf({ ...EMPTY_SEARCH_FORM, flow: 'five' })).toEqual({
    error: 'Enter a flow number, such as 5512 or QF-5512.',
  });
});

test('AV-3: an archived Movement reads as Tracking words a live one, with its names at export', () => {
  // A name the archive does not hold reads by its number; a route step it
  // does not hold is left out; the recording User is named.
  expect(describeArchivedMovement(MOVEMENT)).toBe(
    'MAT → Area 5 · qty 4 · QF-5513 · W: Worker 8 · by User 6',
  );
  expect(
    describeArchivedMovement({
      ...MOVEMENT,
      movementType: 'RECEIVED',
      fromArea: null,
      toArea: { id: 3, name: 'MAT' },
      worker: null,
      assignedRouteStep: { id: 41, sequence: 1 },
      demand: {
        workOrderDemandId: 90,
        workOrderId: 31,
        workOrderNumber: null,
        requestType: 'NEW',
      },
      actorUser: null,
    }),
  ).toBe(
    'Received into MAT · qty 4 · QF-5513 · WO — release · Planned Route step 1',
  );
  expect(operationName({ id: 7, code: 'OP-MAT', name: null })).toBe('OP-MAT');
  expect(operationName({ id: 7, code: null, name: null })).toBe('Operation 7');
  expect(operationName({ id: 7, code: 'OP-MAT', name: 'Material' })).toBe(
    'Material',
  );
});

const FLOW: ArchivedFlow = {
  id: 5512,
  partNumber: 'AD-0001',
  partNumberName: null,
  componentId: 5512,
  quantity: 6,
  status: 'SCRAPPED',
  routeMode: 'FLOATING',
  createdAt: '2023-11-01T08:00:00Z',
  closedAt: null,
  record: { scrapped_quantity: 2, stocked_quantity: 1.5 },
  stockedArea: null,
  scrappedArea: { id: 5, name: null },
  demand: null,
  workOrders: [
    { workOrderId: 31, workOrderNumber: 'WO-1' },
    { workOrderId: 32, workOrderNumber: null },
  ],
  parents: [
    { quantityFlowId: 5510, relation: 'MERGED', void: false },
    { quantityFlowId: 5511, relation: 'MERGED', void: false },
    { quantityFlowId: 5509, relation: 'SPLIT', void: true },
  ],
  children: [{ quantityFlowId: 5520, relation: 'SPLIT', void: true }],
  route: null,
  movements: [],
};

test("AV-4: a flow's status line holds its effective lineage, and its record lines what the archive adds", () => {
  expect(flowStatusLine(FLOW)).toBe(
    'scrapped — closed · MERGED from QF-5510 + QF-5511',
  );
  expect(flowStatusLine({ ...FLOW, status: 'ODD', parents: [] })).toBe('odd');
  // A record value that is not a whole number is left out.
  expect(flowRecordLines(FLOW)).toEqual([
    'Work Orders WO-1 and 1 internal Work Order',
    'Scrapped 2 at Area 5',
    `Created ${formatInstant('2023-11-01T08:00:00Z')}`,
  ]);
});

test('AV-4b: the Work Orders of a flow name the numbered ones and count the internal ones', () => {
  const numbered = (number: string) => ({
    workOrderId: 1,
    workOrderNumber: number,
  });
  const internal = { workOrderId: 2, workOrderNumber: null };
  expect(workOrdersText([])).toBeNull();
  expect(workOrdersText([numbered('E2E-WO-1')])).toBe('Work Order E2E-WO-1');
  expect(workOrdersText([numbered('WO-1'), numbered('WO-2')])).toBe(
    'Work Orders WO-1, WO-2',
  );
  expect(
    workOrdersText([numbered('E2E-WO-1'), internal, { ...internal }]),
  ).toBe('Work Orders E2E-WO-1 and 2 internal Work Orders');
  expect(workOrdersText([internal])).toBe('1 internal Work Order');
  expect(workOrdersText([internal, { ...internal }])).toBe(
    '2 internal Work Orders',
  );
});

const VIEW: ArchiveView = {
  id: 4,
  runId: 2,
  state: 'OPEN',
  requestedBy: { userId: 1, displayName: 'Ann' },
  requestedAt: '2026-11-18T10:00:00Z',
  toLoad: {
    archiveName: null,
    movements: 1,
    flows: 1,
    lineageEdges: 0,
    assignedRoutes: 0,
    archiveBytes: 100,
  },
  startedAt: '2026-11-18T10:05:00Z',
  openedAt: null,
  openUntil: null,
  loaded: {
    movements: 1,
    flows: 1,
    lineageEdges: 0,
    assignedRoutes: 0,
    names: 3,
  },
  closingStartedAt: null,
  closedBy: null,
  cancelledBy: null,
  finishedAt: null,
  failureReason: null,
};

test('AV-5: the copy the viewer reads, and the line under a view in the runs', () => {
  expect(viewCopyText(null)).toBe('A copy of an archive loaded for viewing.');
  expect(viewCopyText(VIEW)).toBe(
    'A copy of the archive of run 2, loaded for viewing: 1 Movement in 1 flow.',
  );
  // An open view whose until is not known yet says nothing more.
  expect(viewDetailText(VIEW)).toBeNull();
  expect(viewDetailText({ ...VIEW, state: 'CANCELLED' })).toBeNull();
  expect(viewDetailText({ ...VIEW, state: 'REQUESTED' })).toBeNull();
});

const RUN = {
  id: 7,
  state: 'CANCELLED',
  viewable: true,
  view: null,
  archiveBytes: null,
  exported: null,
} as unknown as ArchiveRun;

test('AV-6: Open for viewing follows the server: a viewable run without an active view; its text without the archive size or the days', () => {
  expect(runCanBeOpenedForViewing(RUN)).toBe(true);
  expect(runCanBeOpenedForViewing({ ...RUN, viewable: false })).toBe(false);
  for (const state of ['REQUESTED', 'LOADING', 'OPEN'] as const) {
    expect(runCanBeOpenedForViewing({ ...RUN, view: { id: 1, state } })).toBe(
      false,
    );
  }
  for (const state of ['CLOSING', 'CLOSED', 'FAILED', 'CANCELLED'] as const) {
    expect(runCanBeOpenedForViewing({ ...RUN, view: { id: 1, state } })).toBe(
      true,
    );
  }
  expect(openForViewingText(RUN, null).slice(1)).toEqual([
    'Until it is closed, the copy takes space in the database.',
    'It closes by itself after the days set under Retention and schedule, or earlier with Close viewing.',
  ]);
  expect(openForViewingText(RUN, 1)[2]).toBe(
    'It closes by itself 1 day after it opens, or earlier with Close viewing.',
  );
  // No article depends on the size.
  expect(openForViewingText({ ...RUN, archiveBytes: 89293 }, 7)[1]).toBe(
    "Until it is closed, the copy takes space in the database: roughly 7 times the archive's size, about 610.4 KB for this run (an archive of 87.2 KB).",
  );
});
