import { expect, test } from 'vitest';

import type {
  WorkOrderImportEntry,
  WorkOrderImportReport,
} from '../../api/work-order-import';
import {
  commitAllowed,
  fileProblem,
  formatRowList,
  importButtonLabel,
  orderEntries,
  outcomeLabel,
  outcomeNotes,
  rowErrorText,
  rowsReadLine,
  summaryLine,
  unassignedRowsMessage,
  undatedLinesToCreate,
} from './work-order-import';

// FU-1: the Import Work Orders dialog's pure presentation logic
// (GUI_DESIGN §11.7). The import rules themselves are the server's.

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
    workOrderId: null,
    existingStatus: null,
    differsFromFile: null,
    errors: [],
    ...extra,
  };
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
    workOrders: [],
    unassignedRows: [],
    summary: { willCreate: 2, existing: 0, refused: 0 },
    ...extra,
  };
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

test('importButtonLabel counts the Work Orders it creates', () => {
  expect(importButtonLabel(1)).toBe('Import 1 Work Order');
  expect(importButtonLabel(12)).toBe('Import 12 Work Orders');
  expect(importButtonLabel(0)).toBe('Import Work Orders');
});

test('formatRowList joins consecutive rows into ranges', () => {
  expect(formatRowList([2, 3, 4, 9])).toBe('2–4, 9');
  expect(formatRowList([5])).toBe('5');
  expect(formatRowList([7, 2, 3])).toBe('2–3, 7');
  expect(formatRowList([2, 4, 6])).toBe('2, 4, 6');
  expect(formatRowList([])).toBe('');
});

test('orderEntries puts refused first, then created, then existing — file order within', () => {
  const ordered = orderEntries([
    entry('E1', 'EXISTS'),
    entry('C1', 'WILL_CREATE'),
    entry('R1', 'REFUSED'),
    entry('C2', 'CREATED'),
    entry('E2', 'EXISTS'),
    entry('R2', 'REFUSED'),
  ]);
  expect(ordered.map((e) => e.workOrderNumber)).toEqual([
    'R1',
    'R2',
    'C1',
    'C2',
    'E1',
    'E2',
  ]);
});

test('outcomeLabel and outcomeNotes name every outcome', () => {
  expect(outcomeLabel(entry('A', 'WILL_CREATE'))).toBe('Will be created');
  expect(outcomeLabel(entry('A', 'CREATED'))).toBe('Created');
  expect(outcomeLabel(entry('A', 'EXISTS'))).toBe(
    'Already in PartFlow — not changed by this import',
  );
  expect(outcomeLabel(entry('A', 'REFUSED'))).toBe(
    'Not imported — fix the rows listed',
  );

  expect(
    outcomeNotes(entry('A', 'WILL_CREATE', { newPartNumbers: ['X-1'] })),
  ).toEqual(['1 new Part Number']);
  expect(
    outcomeNotes(entry('A', 'WILL_CREATE', { newPartNumbers: ['X', 'Y'] })),
  ).toEqual(['2 new Part Numbers']);
  expect(
    outcomeNotes(
      entry('A', 'EXISTS', { existingStatus: 'OPEN', differsFromFile: true }),
    ),
  ).toEqual(['Differs from this file — open the Work Order to apply changes.']);
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
      entry('A', 'EXISTS', { existingStatus: 'OPEN', differsFromFile: false }),
    ),
  ).toEqual([]);
});

test('commitAllowed truth table', () => {
  const ok = preview();
  expect(commitAllowed(ok, false, false)).toBe(true);
  expect(commitAllowed(ok, true, false)).toBe(false);
  expect(commitAllowed(ok, false, true)).toBe(false);
  expect(commitAllowed(preview({ commitBlocked: true }), false, false)).toBe(
    false,
  );
  expect(
    commitAllowed(
      preview({ summary: { willCreate: 0, existing: 2, refused: 0 } }),
      false,
      false,
    ),
  ).toBe(false);
  const result: WorkOrderImportReport = {
    ...ok,
    dryRun: false,
    summary: { created: 2, existing: 0, refused: 0 },
  };
  expect(commitAllowed(result, false, false)).toBe(false);
});

test('report lines: summary, rows read, row errors, undated lines and the blocked-rows copy', () => {
  expect(summaryLine(preview())).toBe(
    'Will create 2 · Already in PartFlow 0 · Not imported 0',
  );
  expect(
    summaryLine({
      ...preview(),
      dryRun: false,
      summary: { created: 1, existing: 3, refused: 2 },
    }),
  ).toBe('Created 1 · Already in PartFlow 3 · Not imported 2');

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

  expect(
    undatedLinesToCreate(
      preview({
        workOrders: [
          entry('A', 'WILL_CREATE', { linesWithoutDueDate: 2 }),
          entry('B', 'EXISTS', { linesWithoutDueDate: 5 }),
          entry('C', 'WILL_CREATE', { linesWithoutDueDate: 1 }),
        ],
      }),
    ),
  ).toBe(3);

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
