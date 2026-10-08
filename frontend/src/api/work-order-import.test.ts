import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  IMPORT_MEDIA_TYPE,
  checkWorkOrderFile,
  importFileKind,
  importWorkOrderFile,
  toWorkOrderImportReport,
} from './work-order-import';

// FU-2 / FU-4: the Work Order import API module — the report mapper
// over the exact wire contract (Phase 15 slice 1 §4.2, extended by
// slice 2 §4.2: change lists, update outcomes, `update_token`,
// `required_permissions`), file kinds by extension, and the two
// raw-body uploads (the Import adds the confirmation header only when
// a token is given).

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** The bytes of a Blob (jsdom's Blob has no arrayBuffer()). */
function blobBytes(blob: Blob): Promise<number[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve(Array.from(new Uint8Array(reader.result as ArrayBuffer)));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

const TOKEN = 'ab'.repeat(32);

function entryWire(extra?: Record<string, unknown>) {
  return {
    work_order_number: '007201',
    rows: [2, 3],
    outcome: 'WILL_CREATE',
    lines: [
      {
        row: 2,
        part_number: 'A-100',
        requested_quantity: 25,
        due_date: '2026-07-24',
        job_number: '18112',
      },
      {
        row: 3,
        part_number: 'B-200',
        requested_quantity: 5,
        due_date: null,
        job_number: null,
      },
    ],
    new_part_numbers: ['B-200'],
    lines_without_due_date: 1,
    changes: null,
    completes_work_order: null,
    lines_not_in_file: null,
    work_order_id: null,
    existing_status: null,
    differs_from_file: null,
    errors: [],
    ...extra,
  };
}

const CONFIRM = 'cd'.repeat(32);

/** A WILL_UPDATE entry with one edit and one added line. */
function updateWire(extra?: Record<string, unknown>) {
  return entryWire({
    work_order_number: '007400',
    rows: [6, 7],
    outcome: 'WILL_UPDATE',
    lines: [
      {
        row: 6,
        part_number: 'A-100',
        requested_quantity: 15,
        due_date: null,
        job_number: 'J2',
      },
      {
        row: 7,
        part_number: 'N-1',
        requested_quantity: 4,
        due_date: null,
        job_number: null,
      },
    ],
    new_part_numbers: ['N-1'],
    lines_without_due_date: 1,
    changes: [
      {
        kind: 'EDIT_LINE',
        row: 6,
        part_number: 'A-100',
        demand_id: 101,
        new_part_number: false,
        requested_quantity: { before: 10, after: 15 },
        due_date: { before: null, after: '2026-07-24' },
        job_numbers: { before: ['J1'], after: ['J1', 'J2'] },
        leaves_hot_list: true,
      },
      {
        kind: 'ADD_LINE',
        row: 7,
        part_number: 'N-1',
        demand_id: null,
        new_part_number: true,
        requested_quantity: { before: null, after: 4 },
        due_date: { before: null, after: null },
        job_numbers: { before: [], after: [] },
        leaves_hot_list: false,
      },
    ],
    completes_work_order: false,
    lines_not_in_file: ['K-9'],
    work_order_id: 7,
    existing_status: 'OPEN',
    ...extra,
  });
}

function previewWire(extra?: Record<string, unknown>) {
  return {
    dry_run: true,
    file_format: 'XLSX',
    worksheet: 'Work Orders',
    check_token: TOKEN,
    commit_blocked: false,
    rows_read: 4,
    empty_rows_ignored: 1,
    ignored_columns: ['Revision'],
    lines_without_due_date: 1,
    update_token: null,
    required_permissions: ['MANAGE_WORK_ORDERS'],
    work_orders: [
      entryWire(),
      entryWire({
        work_order_number: '007300',
        rows: [4],
        outcome: 'EXISTS',
        lines: [],
        new_part_numbers: [],
        lines_without_due_date: 0,
        work_order_id: 3,
        existing_status: 'COMPLETED',
        differs_from_file: true,
      }),
      entryWire({
        work_order_number: 'WO-9',
        rows: [5],
        outcome: 'REFUSED',
        lines: [],
        new_part_numbers: [],
        lines_without_due_date: 0,
        errors: [
          {
            row: 5,
            column: 'Part Number',
            message: 'Part Number is required.',
          },
          { row: null, column: null, message: 'Too many lines.' },
        ],
      }),
    ],
    unassigned_rows: [],
    summary: { will_create: 1, will_update: 0, existing: 1, refused: 1 },
    ...extra,
  };
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('the preview report maps every field, outcome and null', () => {
  const report = toWorkOrderImportReport(previewWire());
  expect(report).toEqual({
    dryRun: true,
    fileFormat: 'XLSX',
    worksheet: 'Work Orders',
    checkToken: TOKEN,
    commitBlocked: false,
    rowsRead: 4,
    emptyRowsIgnored: 1,
    ignoredColumns: ['Revision'],
    linesWithoutDueDate: 1,
    updateToken: null,
    requiredPermissions: ['MANAGE_WORK_ORDERS'],
    workOrders: [
      {
        workOrderNumber: '007201',
        rows: [2, 3],
        outcome: 'WILL_CREATE',
        lines: [
          {
            row: 2,
            partNumber: 'A-100',
            requestedQuantity: 25,
            dueDate: '2026-07-24',
            jobNumber: '18112',
          },
          {
            row: 3,
            partNumber: 'B-200',
            requestedQuantity: 5,
            dueDate: null,
            jobNumber: null,
          },
        ],
        newPartNumbers: ['B-200'],
        linesWithoutDueDate: 1,
        changes: null,
        completesWorkOrder: null,
        linesNotInFile: null,
        workOrderId: null,
        existingStatus: null,
        differsFromFile: null,
        errors: [],
      },
      {
        workOrderNumber: '007300',
        rows: [4],
        outcome: 'EXISTS',
        lines: [],
        newPartNumbers: [],
        linesWithoutDueDate: 0,
        changes: null,
        completesWorkOrder: null,
        linesNotInFile: null,
        workOrderId: 3,
        existingStatus: 'COMPLETED',
        differsFromFile: true,
        errors: [],
      },
      {
        workOrderNumber: 'WO-9',
        rows: [5],
        outcome: 'REFUSED',
        lines: [],
        newPartNumbers: [],
        linesWithoutDueDate: 0,
        changes: null,
        completesWorkOrder: null,
        linesNotInFile: null,
        workOrderId: null,
        existingStatus: null,
        differsFromFile: null,
        errors: [
          {
            row: 5,
            column: 'Part Number',
            message: 'Part Number is required.',
          },
          { row: null, column: null, message: 'Too many lines.' },
        ],
      },
    ],
    unassignedRows: [],
    summary: { willCreate: 1, willUpdate: 0, existing: 1, refused: 1 },
  });
});

test('FU-4: a WILL_UPDATE entry maps both change kinds, the token and the permissions', () => {
  const report = toWorkOrderImportReport(
    previewWire({
      update_token: CONFIRM,
      required_permissions: ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_WORK_ORDERS'],
      work_orders: [
        updateWire(),
        entryWire({
          work_order_number: '007500',
          outcome: 'EXISTS',
          lines_without_due_date: 0,
          new_part_numbers: [],
          lines_not_in_file: [],
          work_order_id: 8,
          existing_status: 'RELEASED',
          differs_from_file: false,
        }),
      ],
      summary: { will_create: 0, will_update: 1, existing: 1, refused: 0 },
    }),
  );
  expect(report.updateToken).toBe(CONFIRM);
  expect(report.requiredPermissions).toEqual([
    'EDIT_WORK_ORDER_DEMAND',
    'MANAGE_WORK_ORDERS',
  ]);
  expect(report.summary).toEqual({
    willCreate: 0,
    willUpdate: 1,
    existing: 1,
    refused: 0,
  });
  const [update, exists] = report.workOrders;
  expect(update.outcome).toBe('WILL_UPDATE');
  expect(update.workOrderId).toBe(7);
  expect(update.existingStatus).toBe('OPEN');
  expect(update.completesWorkOrder).toBe(false);
  expect(update.linesNotInFile).toEqual(['K-9']);
  expect(update.differsFromFile).toBeNull();
  expect(update.changes).toEqual([
    {
      kind: 'EDIT_LINE',
      row: 6,
      partNumber: 'A-100',
      demandId: 101,
      newPartNumber: false,
      requestedQuantity: { before: 10, after: 15 },
      dueDate: { before: null, after: '2026-07-24' },
      jobNumbers: { before: ['J1'], after: ['J1', 'J2'] },
      leavesHotList: true,
    },
    {
      kind: 'ADD_LINE',
      row: 7,
      partNumber: 'N-1',
      demandId: null,
      newPartNumber: true,
      requestedQuantity: { before: null, after: 4 },
      dueDate: { before: null, after: null },
      jobNumbers: { before: [], after: [] },
      leavesHotList: false,
    },
  ]);
  expect(exists.changes).toBeNull();
  expect(exists.completesWorkOrder).toBeNull();
  expect(exists.linesNotInFile).toEqual([]);

  // An edit that changes only the quantity carries null pairs.
  const quantityOnly = toWorkOrderImportReport(
    previewWire({
      work_orders: [
        updateWire({
          changes: [
            {
              kind: 'EDIT_LINE',
              row: 6,
              part_number: 'A-100',
              demand_id: 101,
              new_part_number: false,
              requested_quantity: { before: 10, after: 8 },
              due_date: null,
              job_numbers: null,
              leaves_hot_list: false,
            },
          ],
          completes_work_order: true,
          lines_not_in_file: [],
        }),
      ],
    }),
  );
  expect(quantityOnly.workOrders[0].changes?.[0]).toMatchObject({
    requestedQuantity: { before: 10, after: 8 },
    dueDate: null,
    jobNumbers: null,
  });
  expect(quantityOnly.workOrders[0].completesWorkOrder).toBe(true);
});

test('FU-4: the result maps UPDATED entries and its updated count', () => {
  const report = toWorkOrderImportReport(
    previewWire({
      dry_run: false,
      update_token: null,
      required_permissions: ['EDIT_WORK_ORDER_DEMAND'],
      work_orders: [
        updateWire({ outcome: 'UPDATED', existing_status: 'COMPLETED' }),
        entryWire({
          work_order_number: '007600',
          outcome: 'REFUSED',
          lines: [],
          new_part_numbers: [],
          lines_without_due_date: 0,
          work_order_id: 9,
          existing_status: 'RELEASED',
          errors: [
            {
              row: null,
              column: null,
              message:
                'This Work Order changed after the file was checked, so nothing was changed on it. Check the file again.',
            },
          ],
        }),
      ],
      summary: { created: 0, updated: 1, existing: 0, refused: 1 },
    }),
  );
  expect(report.dryRun).toBe(false);
  expect(report.summary).toEqual({
    created: 0,
    updated: 1,
    existing: 0,
    refused: 1,
  });
  expect(report.workOrders[0].outcome).toBe('UPDATED');
  expect(report.workOrders[0].existingStatus).toBe('COMPLETED');
  expect(report.workOrders[0].changes).toHaveLength(2);
  expect(report.workOrders[1].workOrderId).toBe(9);
  expect(report.workOrders[1].existingStatus).toBe('RELEASED');
});

test('the result report maps its own summary and CREATED entries', () => {
  const report = toWorkOrderImportReport(
    previewWire({
      dry_run: false,
      file_format: 'CSV',
      worksheet: null,
      work_orders: [entryWire({ outcome: 'CREATED', work_order_id: 41 })],
      unassigned_rows: [],
      summary: { created: 1, updated: 0, existing: 0, refused: 0 },
    }),
  );
  expect(report.dryRun).toBe(false);
  expect(report.summary).toEqual({
    created: 1,
    updated: 0,
    existing: 0,
    refused: 0,
  });
  expect(report.fileFormat).toBe('CSV');
  expect(report.worksheet).toBeNull();
  expect(report.workOrders[0].outcome).toBe('CREATED');
  expect(report.workOrders[0].workOrderId).toBe(41);

  const blocked = toWorkOrderImportReport(
    previewWire({
      commit_blocked: true,
      unassigned_rows: [
        {
          row: 7,
          column: 'Work Order Number',
          message: 'Work Order Number is missing.',
        },
      ],
    }),
  );
  expect(blocked.commitBlocked).toBe(true);
  expect(blocked.unassignedRows).toEqual([
    {
      row: 7,
      column: 'Work Order Number',
      message: 'Work Order Number is missing.',
    },
  ]);
});

test('an unknown outcome, change kind or permission, or a malformed report throws instead of rendering', () => {
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ work_orders: [entryWire({ outcome: 'WILL_DELETE' })] }),
    ),
  ).toThrow('The server answered a malformed import report.');
  const removal = {
    ...(updateWire().changes as unknown as Record<string, unknown>[])[0],
    kind: 'REMOVE_LINE',
  };
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ work_orders: [updateWire({ changes: [removal] })] }),
    ),
  ).toThrow('The server answered a malformed import report.');
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ required_permissions: ['IMPORT_EVERYTHING'] }),
    ),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(previewWire({ update_token: undefined })),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ work_orders: [updateWire({ changes: undefined })] }),
    ),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ summary: { will_create: 1, existing: 0, refused: 0 } }),
    ),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ work_orders: [entryWire({ existing_status: 'CLOSED' })] }),
    ),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(previewWire({ file_format: 'XLS' })),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(previewWire({ summary: { created: 1 } })),
  ).toThrow();
  expect(() =>
    toWorkOrderImportReport(previewWire({ rows_read: '4' })),
  ).toThrow();
  expect(() => toWorkOrderImportReport(null)).toThrow();
  expect(() => toWorkOrderImportReport([])).toThrow();
});

test('the file kind comes from the extension, case-insensitive', () => {
  expect(importFileKind('orders.CSV')).toBe('CSV');
  expect(importFileKind('Orders.Xlsx')).toBe('XLSX');
  expect(importFileKind('orders.xls')).toBeNull();
  expect(importFileKind('orders.xlsm')).toBeNull();
  expect(importFileKind('orders.csv.txt')).toBeNull();
});

test('Check file sends the raw bytes labelled by extension, never by File.type', async () => {
  for (const reported of ['application/vnd.ms-excel', '']) {
    fetchMock.mockResolvedValueOnce(json(previewWire()));
    const file = new File(['Work Order Number\n'], 'orders.CSV', {
      type: reported,
    });
    const kind = importFileKind(file.name);
    expect(kind).toBe('CSV');
    const bytes = new Uint8Array(await blobBytes(file)).buffer;

    const report = await checkWorkOrderFile(bytes, kind!);

    expect(report.checkToken).toBe(TOKEN);
    const [path, init] = fetchMock.mock.lastCall as [string, RequestInit];
    expect(path).toBe('/api/work-orders/import/preview');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({
      'Content-Type': 'text/csv',
      'X-PartFlow-CSRF': '1',
    });
    const body = init.body as Blob;
    expect(body.type).toBe('text/csv');
    expect(await blobBytes(body)).toEqual(await blobBytes(file));
  }

  fetchMock.mockResolvedValueOnce(json(previewWire()));
  await checkWorkOrderFile(new Uint8Array([0x50, 0x4b]).buffer, 'XLSX');
  const [, init] = fetchMock.mock.lastCall as [string, RequestInit];
  expect((init.headers as Record<string, string>)['Content-Type']).toBe(
    IMPORT_MEDIA_TYPE.XLSX,
  );
  expect(IMPORT_MEDIA_TYPE.XLSX).toBe(
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
});

test('Import sends the same bytes with the check token header', async () => {
  fetchMock.mockResolvedValueOnce(
    json(
      previewWire({
        dry_run: false,
        work_orders: [entryWire({ outcome: 'CREATED', work_order_id: 9 })],
        summary: { created: 1, updated: 0, existing: 0, refused: 0 },
      }),
    ),
  );
  const bytes = new Uint8Array([1, 2, 3]).buffer;

  const report = await importWorkOrderFile(bytes, 'CSV', TOKEN, null);

  expect(report.dryRun).toBe(false);
  const [path, init] = fetchMock.mock.lastCall as [string, RequestInit];
  expect(path).toBe('/api/work-orders/import');
  expect(init.method).toBe('POST');
  expect(init.headers).toEqual({
    'Content-Type': 'text/csv',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Import-Check': TOKEN,
  });
  expect(await blobBytes(init.body as Blob)).toEqual([1, 2, 3]);
});

test('FU-4: Import sends the confirmation header only when a token is given', async () => {
  fetchMock.mockResolvedValueOnce(
    json(
      previewWire({
        dry_run: false,
        work_orders: [updateWire({ outcome: 'UPDATED' })],
        summary: { created: 0, updated: 1, existing: 0, refused: 0 },
      }),
    ),
  );
  const bytes = new Uint8Array([4, 5]).buffer;

  await importWorkOrderFile(bytes, 'XLSX', TOKEN, CONFIRM);

  const [path, init] = fetchMock.mock.lastCall as [string, RequestInit];
  expect(path).toBe('/api/work-orders/import');
  expect(init.headers).toEqual({
    'Content-Type': IMPORT_MEDIA_TYPE.XLSX,
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Import-Check': TOKEN,
    'X-PartFlow-Import-Confirm': CONFIRM,
  });
  expect(await blobBytes(init.body as Blob)).toEqual([4, 5]);
});
