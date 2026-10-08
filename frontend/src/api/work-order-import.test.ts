import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  IMPORT_MEDIA_TYPE,
  checkWorkOrderFile,
  importFileKind,
  importWorkOrderFile,
  toWorkOrderImportReport,
} from './work-order-import';

// FU-2: the Work Order import API module — the report mapper over the
// exact wire contract (Phase 15 slice 1, §4.2), file kinds by
// extension, and the two raw-body uploads.

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
    work_order_id: null,
    existing_status: null,
    differs_from_file: null,
    errors: [],
    ...extra,
  };
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
    summary: { will_create: 1, existing: 1, refused: 1 },
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
    summary: { willCreate: 1, existing: 1, refused: 1 },
  });
});

test('the result report maps its own summary and CREATED entries', () => {
  const report = toWorkOrderImportReport(
    previewWire({
      dry_run: false,
      file_format: 'CSV',
      worksheet: null,
      work_orders: [entryWire({ outcome: 'CREATED', work_order_id: 41 })],
      unassigned_rows: [],
      summary: { created: 1, existing: 0, refused: 0 },
    }),
  );
  expect(report.dryRun).toBe(false);
  expect(report.summary).toEqual({ created: 1, existing: 0, refused: 0 });
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

test('an unknown outcome or a malformed report throws instead of rendering', () => {
  expect(() =>
    toWorkOrderImportReport(
      previewWire({ work_orders: [entryWire({ outcome: 'WILL_UPDATE' })] }),
    ),
  ).toThrow('The server answered a malformed import report.');
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
        summary: { created: 1, existing: 0, refused: 0 },
      }),
    ),
  );
  const bytes = new Uint8Array([1, 2, 3]).buffer;

  const report = await importWorkOrderFile(bytes, 'CSV', TOKEN);

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
