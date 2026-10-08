import { expect, test } from 'vitest';

import type {
  ImportChange,
  WorkOrderImportEntry,
  WorkOrderImportReport,
} from '../../api/work-order-import';
import type { Permission } from '../../api/roles';
import { formatIsoDate } from '../dates';
import {
  changeNoun,
  changeText,
  commitAllowed,
  fileProblem,
  formatRowList,
  hasStaleUpdates,
  importButtonLabel,
  keptLinesText,
  missingPermissionText,
  missingPermissions,
  orderEntries,
  outcomeIcon,
  outcomeLabel,
  outcomeNotes,
  rowErrorText,
  rowsReadLine,
  summaryLine,
  typedConfirmValue,
  unassignedRowsMessage,
  workOrderNoun,
} from './work-order-import';

// FU-1 / FU-5: the Import Work Orders dialog's pure presentation logic
// (GUI_DESIGN §11.7), including the change lists and the typed
// confirmation of changes to existing Work Orders (Phase 15 slice 2).
// The import rules themselves are the server's.

function entry(
  workOrderNumber: string,
  outcome: WorkOrderImportEntry['outcome'],
  extra?: Partial<WorkOrderImportEntry>,
): WorkOrderImportEntry {
  return {
    workOrderNumber,
    rows: [2],
    outcome,
    lines: [],
    newPartNumbers: [],
    linesWithoutDueDate: 0,
    changes: null,
    completesWorkOrder: null,
    linesNotInFile: null,
    workOrderId: null,
    existingStatus: null,
    differsFromFile: null,
    errors: [],
    ...extra,
  };
}

function edit(extra?: Partial<ImportChange>): ImportChange {
  return {
    kind: 'EDIT_LINE',
    row: 4,
    partNumber: 'A-100',
    demandId: 101,
    newPartNumber: false,
    requestedQuantity: null,
    dueDate: null,
    jobNumbers: null,
    leavesHotList: false,
    ...extra,
  };
}

function add(extra?: Partial<ImportChange>): ImportChange {
  return {
    kind: 'ADD_LINE',
    row: 5,
    partNumber: 'B-200',
    demandId: null,
    newPartNumber: false,
    requestedQuantity: { before: null, after: 3 },
    dueDate: { before: null, after: null },
    jobNumbers: { before: [], after: [] },
    leavesHotList: false,
    ...extra,
  };
}

function changes(k: number): ImportChange[] {
  return Array.from({ length: k }, (_, i) => edit({ row: i + 2 }));
}

function preview(
  extra?: Partial<Extract<WorkOrderImportReport, { dryRun: true }>>,
): WorkOrderImportReport {
  return {
    dryRun: true,
    fileFormat: 'CSV',
    worksheet: null,
    checkToken: 'a'.repeat(64),
    commitBlocked: false,
    rowsRead: 3,
    emptyRowsIgnored: 0,
    ignoredColumns: [],
    linesWithoutDueDate: 0,
    updateToken: null,
    requiredPermissions: ['MANAGE_WORK_ORDERS'],
    workOrders: [],
    unassignedRows: [],
    summary: { willCreate: 2, willUpdate: 0, existing: 0, refused: 0 },
    ...extra,
  };
}

function result(
  extra?: Partial<Extract<WorkOrderImportReport, { dryRun: false }>>,
): WorkOrderImportReport {
  return {
    ...preview(),
    dryRun: false,
    summary: { created: 0, updated: 0, existing: 0, refused: 0 },
    ...extra,
  };
}

const ALL = (): boolean => true;

function holding(...keys: Permission[]): (key: Permission) => boolean {
  return (key) => keys.includes(key);
}

test('fileProblem refuses an unsupported extension, an oversized and an empty file', () => {
  expect(fileProblem({ name: 'orders.csv', size: 10 })).toBeNull();
  expect(fileProblem({ name: 'ORDERS.XLSX', size: 1_048_576 })).toBeNull();
  expect(fileProblem({ name: 'orders.xls', size: 10 })).toBe(
    'Choose a .csv or .xlsx file.',
  );
  expect(fileProblem({ name: 'orders', size: 10 })).toBe(
    'Choose a .csv or .xlsx file.',
  );
  expect(fileProblem({ name: 'orders.csv', size: 1_048_577 })).toBe(
    'The file is larger than 1 MB. Split it into smaller files.',
  );
  expect(fileProblem({ name: 'orders.csv', size: 0 })).toBe(
    'The file is empty.',
  );
});

test('importButtonLabel counts the Work Orders it creates and changes', () => {
  expect(importButtonLabel(1, 0)).toBe('Import 1 Work Order');
  expect(importButtonLabel(12, 0)).toBe('Import 12 Work Orders');
  expect(importButtonLabel(0, 0)).toBe('Import Work Orders');
  expect(importButtonLabel(0, 1)).toBe('Change 1 Work Order…');
  expect(importButtonLabel(0, 2)).toBe('Change 2 Work Orders…');
  expect(importButtonLabel(1, 1)).toBe(
    'Create 1 Work Order, change 1 Work Order…',
  );
  expect(importButtonLabel(2, 3)).toBe(
    'Create 2 Work Orders, change 3 Work Orders…',
  );
  expect(workOrderNoun(1)).toBe('Work Order');
  expect(workOrderNoun(0)).toBe('Work Orders');
  expect(changeNoun(1)).toBe('change');
  expect(changeNoun(4)).toBe('changes');
  expect(typedConfirmValue(1)).toBe('CHANGE 1');
  expect(typedConfirmValue(12)).toBe('CHANGE 12');
});

test('formatRowList joins consecutive rows into ranges', () => {
  expect(formatRowList([2, 3, 4, 9])).toBe('2–4, 9');
  expect(formatRowList([5])).toBe('5');
  expect(formatRowList([7, 2, 3])).toBe('2–3, 7');
  expect(formatRowList([2, 4, 6])).toBe('2, 4, 6');
  expect(formatRowList([])).toBe('');
});

test('orderEntries puts refused first, then changed, then created, then existing — file order within', () => {
  const ordered = orderEntries([
    entry('E1', 'EXISTS'),
    entry('C1', 'WILL_CREATE'),
    entry('U1', 'WILL_UPDATE'),
    entry('R1', 'REFUSED'),
    entry('C2', 'CREATED'),
    entry('U2', 'UPDATED'),
    entry('E2', 'EXISTS'),
    entry('R2', 'REFUSED'),
  ]);
  expect(ordered.map((e) => e.workOrderNumber)).toEqual([
    'R1',
    'R2',
    'U1',
    'U2',
    'C1',
    'C2',
    'E1',
    'E2',
  ]);
});

test('outcomeLabel, outcomeIcon and outcomeNotes name every outcome', () => {
  expect(outcomeLabel(entry('A', 'WILL_CREATE'))).toBe('Will be created');
  expect(outcomeLabel(entry('A', 'CREATED'))).toBe('Created');
  expect(outcomeLabel(entry('A', 'WILL_UPDATE', { changes: changes(1) }))).toBe(
    'Will change — 1 change',
  );
  expect(outcomeLabel(entry('A', 'WILL_UPDATE', { changes: changes(3) }))).toBe(
    'Will change — 3 changes',
  );
  expect(outcomeLabel(entry('A', 'UPDATED', { changes: changes(1) }))).toBe(
    'Changed — 1 change',
  );
  expect(outcomeLabel(entry('A', 'UPDATED', { changes: changes(2) }))).toBe(
    'Changed — 2 changes',
  );
  expect(
    outcomeLabel(entry('A', 'EXISTS', { existingStatus: 'COMPLETED' })),
  ).toBe('Already in PartFlow — not changed by this import');
  expect(outcomeLabel(entry('A', 'EXISTS', { existingStatus: 'OPEN' }))).toBe(
    'Already in PartFlow — nothing to change',
  );
  expect(
    outcomeLabel(entry('A', 'EXISTS', { existingStatus: 'RELEASED' })),
  ).toBe('Already in PartFlow — nothing to change');
  expect(outcomeLabel(entry('A', 'REFUSED'))).toBe(
    'Not imported — fix the rows listed',
  );

  expect(outcomeIcon('WILL_UPDATE')).toBe('✓');
  expect(outcomeIcon('UPDATED')).toBe('✓');
  expect(outcomeIcon('CREATED')).toBe('✓');
  expect(outcomeIcon('EXISTS')).toBe('•');
  expect(outcomeIcon('REFUSED')).toBe('✕');

  expect(
    outcomeNotes(entry('A', 'WILL_CREATE', { newPartNumbers: ['X-1'] })),
  ).toEqual(['1 new Part Number']);
  expect(
    outcomeNotes(entry('A', 'WILL_CREATE', { newPartNumbers: ['X', 'Y'] })),
  ).toEqual(['2 new Part Numbers']);
  expect(
    outcomeNotes(
      entry('A', 'WILL_UPDATE', {
        newPartNumbers: ['X'],
        changes: [add({ newPartNumber: true })],
      }),
    ),
  ).toEqual(['1 new Part Number']);
  // An active Work Order: the kept lines replace the old "open the Work
  // Order" note.
  expect(
    outcomeNotes(
      entry('A', 'EXISTS', {
        existingStatus: 'OPEN',
        differsFromFile: true,
        linesNotInFile: ['K-1', 'K-2'],
      }),
    ),
  ).toEqual(['Kept, not in this file: K-1, K-2']);
  expect(
    outcomeNotes(
      entry('A', 'EXISTS', {
        existingStatus: 'RELEASED',
        differsFromFile: false,
        linesNotInFile: [],
      }),
    ),
  ).toEqual([]);
  expect(
    outcomeNotes(
      entry('A', 'EXISTS', {
        existingStatus: 'COMPLETED',
        differsFromFile: true,
      }),
    ),
  ).toEqual([
    'Differs from this file — this Work Order is completed and is never changed.',
  ]);
  expect(
    outcomeNotes(
      entry('A', 'EXISTS', {
        existingStatus: 'COMPLETED',
        differsFromFile: false,
      }),
    ),
  ).toEqual([]);

  expect(keptLinesText(entry('A', 'WILL_UPDATE'))).toBeNull();
  expect(
    keptLinesText(entry('A', 'WILL_UPDATE', { linesNotInFile: ['K-1'] })),
  ).toBe('Kept, not in this file: K-1');
});

test('changeText writes every field combination of both change kinds', () => {
  const text = (change: ImportChange) => changeText(change, formatIsoDate);
  // Added lines.
  expect(text(add())).toBe('Row 5 · Add B-200 · Qty 3');
  expect(
    text(
      add({
        dueDate: { before: null, after: '2026-07-24' },
        jobNumbers: { before: [], after: ['18112'] },
        newPartNumber: true,
      }),
    ),
  ).toBe(
    'Row 5 · Add B-200 · Qty 3 · Due Jul 24, 2026 · Job 18112 · new Part Number',
  );
  // Edits: only the changed fields are written.
  expect(text(edit({ requestedQuantity: { before: 10, after: 15 } }))).toBe(
    'Row 4 · A-100 · Qty 10 → 15',
  );
  expect(text(edit({ dueDate: { before: null, after: '2026-07-24' } }))).toBe(
    'Row 4 · A-100 · Due — → Jul 24, 2026',
  );
  expect(
    text(edit({ dueDate: { before: '2026-07-01', after: '2026-07-24' } })),
  ).toBe('Row 4 · A-100 · Due Jul 01, 2026 → Jul 24, 2026');
  expect(text(edit({ jobNumbers: { before: [], after: ['J2'] } }))).toBe(
    'Row 4 · A-100 · Job Numbers — → J2',
  );
  expect(
    text(
      edit({ jobNumbers: { before: ['J0', 'J1'], after: ['J0', 'J1', 'J2'] } }),
    ),
  ).toBe('Row 4 · A-100 · Job Numbers J0, J1 → J0, J1, J2');
  expect(
    text(
      edit({
        requestedQuantity: { before: 20, after: 12 },
        dueDate: { before: '2026-07-01', after: '2026-08-03' },
        jobNumbers: { before: ['J1'], after: ['J1', 'J2'] },
        leavesHotList: true,
      }),
    ),
  ).toBe(
    'Row 4 · A-100 · Qty 20 → 12 · Due Jul 01, 2026 → Aug 03, 2026 · Job Numbers J1 → J1, J2 · leaves the Hot list',
  );
});

test('missingPermissions and their lines name each key the content needs', () => {
  const both = preview({
    requiredPermissions: ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_WORK_ORDERS'],
  });
  expect(missingPermissions(both, ALL)).toEqual([]);
  expect(missingPermissions(both, holding('MANAGE_WORK_ORDERS'))).toEqual([
    'EDIT_WORK_ORDER_DEMAND',
  ]);
  expect(missingPermissions(both, holding('EDIT_WORK_ORDER_DEMAND'))).toEqual([
    'MANAGE_WORK_ORDERS',
  ]);
  expect(missingPermissions(both, holding())).toEqual([
    'EDIT_WORK_ORDER_DEMAND',
    'MANAGE_WORK_ORDERS',
  ]);
  expect(missingPermissionText('MANAGE_WORK_ORDERS')).toBe(
    'Creating Work Orders needs the "Create and edit Work Orders" permission.',
  );
  expect(missingPermissionText('EDIT_WORK_ORDER_DEMAND')).toBe(
    'Changing existing Work Orders needs the "Edit Work Order Demand" permission.',
  );
});

test('commitAllowed truth table, permissions included', () => {
  const ok = preview();
  expect(commitAllowed(ok, false, false, ALL)).toBe(true);
  expect(commitAllowed(ok, true, false, ALL)).toBe(false);
  expect(commitAllowed(ok, false, true, ALL)).toBe(false);
  expect(
    commitAllowed(preview({ commitBlocked: true }), false, false, ALL),
  ).toBe(false);
  const nothing = preview({
    requiredPermissions: [],
    summary: { willCreate: 0, willUpdate: 0, existing: 2, refused: 0 },
  });
  expect(commitAllowed(nothing, false, false, ALL)).toBe(false);
  // Changes only: allowed for a holder of Edit Work Order Demand.
  const updateOnly = preview({
    requiredPermissions: ['EDIT_WORK_ORDER_DEMAND'],
    updateToken: 'c'.repeat(64),
    summary: { willCreate: 0, willUpdate: 1, existing: 0, refused: 0 },
  });
  expect(
    commitAllowed(updateOnly, false, false, holding('EDIT_WORK_ORDER_DEMAND')),
  ).toBe(true);
  expect(
    commitAllowed(updateOnly, false, false, holding('MANAGE_WORK_ORDERS')),
  ).toBe(false);
  // Creates only, without Create and edit Work Orders.
  expect(
    commitAllowed(ok, false, false, holding('EDIT_WORK_ORDER_DEMAND')),
  ).toBe(false);
  // Both: each key is needed.
  const mixed = preview({
    requiredPermissions: ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_WORK_ORDERS'],
    summary: { willCreate: 1, willUpdate: 1, existing: 0, refused: 0 },
  });
  expect(commitAllowed(mixed, false, false, ALL)).toBe(true);
  expect(
    commitAllowed(mixed, false, false, holding('MANAGE_WORK_ORDERS')),
  ).toBe(false);
  expect(commitAllowed(result(), false, false, ALL)).toBe(false);
});

test('hasStaleUpdates: an Import answer with an existing Work Order refused without a row', () => {
  const stale = entry('A', 'REFUSED', {
    workOrderId: 7,
    existingStatus: 'OPEN',
    errors: [
      {
        row: null,
        column: null,
        message:
          'Work Orders in this file changed after the changes were confirmed, so this Work Order was not changed. Check the file again and confirm the new changes.',
      },
    ],
  });
  expect(hasStaleUpdates(result({ workOrders: [stale] }))).toBe(true);
  // A row error (e.g. a line below its released quantity) is not stale.
  const rowError = entry('A', 'REFUSED', {
    workOrderId: 7,
    errors: [{ row: 4, column: 'Requested Quantity', message: 'Too low.' }],
  });
  expect(hasStaleUpdates(result({ workOrders: [rowError] }))).toBe(false);
  // A new Work Order refused without a row is not an existing one.
  const newOne = entry('B', 'REFUSED', {
    errors: [{ row: null, column: null, message: 'Too many lines.' }],
  });
  expect(hasStaleUpdates(result({ workOrders: [newOne] }))).toBe(false);
  expect(hasStaleUpdates(preview({ workOrders: [stale] }))).toBe(false);
});

test('report lines: summary, rows read, row errors and the blocked-rows copy', () => {
  expect(summaryLine(preview())).toBe(
    'Will create 2 · Will change 0 · Already in PartFlow 0 · Not imported 0',
  );
  expect(
    summaryLine(
      preview({
        summary: { willCreate: 1, willUpdate: 3, existing: 2, refused: 1 },
      }),
    ),
  ).toBe(
    'Will create 1 · Will change 3 · Already in PartFlow 2 · Not imported 1',
  );
  expect(
    summaryLine(
      result({ summary: { created: 1, updated: 2, existing: 3, refused: 2 } }),
    ),
  ).toBe('Created 1 · Changed 2 · Already in PartFlow 3 · Not imported 2');

  expect(rowsReadLine(preview({ rowsRead: 1 }))).toBe('1 row read');
  expect(rowsReadLine(preview({ rowsRead: 9, emptyRowsIgnored: 1 }))).toBe(
    '9 rows read · 1 empty row ignored',
  );
  expect(rowsReadLine(preview({ rowsRead: 9, emptyRowsIgnored: 3 }))).toBe(
    '9 rows read · 3 empty rows ignored',
  );

  expect(
    rowErrorText({ row: 4, column: 'Due Date', message: 'Bad date.' }),
  ).toBe('Row 4 · Due Date — Bad date.');
  expect(rowErrorText({ row: 4, column: null, message: 'Bad.' })).toBe(
    'Row 4 — Bad.',
  );
  expect(rowErrorText({ row: null, column: null, message: 'Too many.' })).toBe(
    'Too many.',
  );

  const r1 = { column: 'Work Order Number', message: 'Missing.' };
  expect(
    unassignedRowsMessage(preview({ unassignedRows: [{ row: 5, ...r1 }] })),
  ).toBe(
    '1 row has no usable Work Order Number. Add or fix it, or delete those rows, then check the file again.',
  );
  expect(
    unassignedRowsMessage(
      preview({
        unassignedRows: [
          { row: 5, ...r1 },
          { row: 5, column: 'Part Number', message: 'Required.' },
          { row: 8, ...r1 },
        ],
      }),
    ),
  ).toBe(
    '2 rows have no usable Work Order Number. Add or fix it, or delete those rows, then check the file again.',
  );
});
