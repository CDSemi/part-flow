import { createHash } from 'node:crypto';

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';
import { PERMISSIONS } from '../../api/roles';

// Import Work Orders dialog (Phase 15 slice 1 — GUI_DESIGN §11.7),
// driven through the real Work Orders view against an in-memory fake of
// the import routes that answers the exact wire contract: the preview
// returns the SHA-256 check token of the bytes it received, and the
// Import refuses a missing token (422) or a token of other bytes (409)
// exactly like the server. Covered: the whole flow and the list reload,
// the same-bytes rule, every reason Import is disabled, the in-flight
// state (no close, navigation and unload guarded), the lost-outcome
// state, file-level refusals and the report presentation.

const C3 = 'Check the file before importing it.';
const C4 =
  'This is not the file that was checked. Check the file again before importing.';
const UNKNOWN_COPY =
  'The import may be partly saved. Check the file again: Work Orders already in PartFlow are never duplicated.';
const CSV_TEXT =
  'Work Order Number,Part Number,Requested Quantity\r\nWO-1,A-100,5\r\nWO-2,B-200,3\r\n';

type Wire = Record<string, unknown>;

interface Upload {
  url: string;
  contentType: string;
  checkHeader: string | null;
  text: string;
}

interface Scenario {
  /** The preview's Work Orders (file order). */
  entries: Wire[];
  /** Overrides of the report's top-level fields. */
  extra: Wire;
}

let scenario: Scenario;
let uploads: Upload[];
let listRows: Wire[];
let listCalls: number;
let healthDown: boolean;
let nextPreviewFailure: Response | 'network' | null;
let nextCommitFailure: Response | 'network' | null;
let holdCommit: Promise<void> | null;
let nextWorkOrderId: number;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function detail(message: string, status: number): Response {
  return json({ detail: message }, status);
}

/** The bytes of a Blob (jsdom's Blob has no arrayBuffer()). */
function blobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function lineWire(
  row: number,
  partNumber: string,
  quantity: number,
  dueDate: string | null = null,
  jobNumber: string | null = null,
): Wire {
  return {
    row,
    part_number: partNumber,
    requested_quantity: quantity,
    due_date: dueDate,
    job_number: jobNumber,
  };
}

function entryWire(number: string, outcome: string, extra?: Wire): Wire {
  return {
    work_order_number: number,
    rows: [2],
    outcome,
    lines: [],
    new_part_numbers: [],
    lines_without_due_date: 0,
    work_order_id: null,
    existing_status: null,
    differs_from_file: null,
    errors: [],
    ...extra,
  };
}

function defaultScenario(): Scenario {
  return {
    entries: [
      entryWire('WO-1', 'WILL_CREATE', {
        rows: [2],
        lines: [lineWire(2, 'A-100', 5, '2026-07-24')],
      }),
      entryWire('WO-2', 'WILL_CREATE', {
        rows: [3],
        lines: [lineWire(3, 'B-200', 3)],
        new_part_numbers: ['B-200'],
        lines_without_due_date: 1,
      }),
    ],
    extra: {},
  };
}

function count(entries: Wire[], outcome: string): number {
  return entries.filter((e) => e.outcome === outcome).length;
}

function reportWire(dryRun: boolean, token: string, entries: Wire[]): Wire {
  return {
    dry_run: dryRun,
    file_format: 'CSV',
    worksheet: null,
    check_token: token,
    commit_blocked: false,
    rows_read: entries.reduce((sum, e) => sum + (e.rows as number[]).length, 0),
    empty_rows_ignored: 0,
    ignored_columns: [],
    lines_without_due_date: entries.reduce(
      (sum, e) => sum + (e.lines_without_due_date as number),
      0,
    ),
    work_orders: entries,
    unassigned_rows: [],
    summary: dryRun
      ? {
          will_create: count(entries, 'WILL_CREATE'),
          existing: count(entries, 'EXISTS'),
          refused: count(entries, 'REFUSED'),
        }
      : {
          created: count(entries, 'CREATED'),
          existing: count(entries, 'EXISTS'),
          refused: count(entries, 'REFUSED'),
        },
    ...scenario.extra,
  };
}

function summaryWire(id: number, number: string): Wire {
  return {
    id,
    work_order_number: number,
    received_date: '2026-10-01',
    due_date: null,
    status: 'OPEN',
    completed_at: null,
    done_date: null,
    due_outcome: null,
    days_late: null,
    demand_line_count: 1,
    part_numbers: [],
  };
}

function sessionResponse(): Response {
  return json({
    user: {
      id: 90,
      login_name: 'mia',
      display_name: 'Mia Manager',
      role_id: 2,
      role_name: 'Manager',
      avatar_updated_at: null,
      permissions: PERMISSIONS,
      must_change_password: false,
      session_expires_at: null,
      theme_preference: null,
    },
    setup_open: false,
  });
}

async function importRoute(
  url: string,
  init: RequestInit | undefined,
): Promise<Response> {
  const headers = (init?.headers ?? {}) as Record<string, string>;
  const body = init?.body as Blob;
  const bytes = await blobBytes(body);
  uploads.push({
    url,
    contentType: headers['Content-Type'],
    checkHeader: headers['X-PartFlow-Import-Check'] ?? null,
    text: new TextDecoder().decode(bytes),
  });
  const token = sha256(bytes);
  if (url === '/api/work-orders/import/preview') {
    const failure = nextPreviewFailure;
    nextPreviewFailure = null;
    if (failure === 'network') throw new TypeError('Failed to fetch');
    if (failure) return failure;
    return json(reportWire(true, token, scenario.entries));
  }
  if (holdCommit) await holdCommit;
  const failure = nextCommitFailure;
  nextCommitFailure = null;
  if (failure === 'network') throw new TypeError('Failed to fetch');
  if (failure) return failure;
  const sent = headers['X-PartFlow-Import-Check'];
  if (sent === undefined || !/^[0-9a-f]{64}$/.test(sent)) {
    return detail(C3, 422);
  }
  if (sent !== token) return detail(C4, 409);
  const committed = scenario.entries.map((e) => {
    if (e.outcome !== 'WILL_CREATE') return e;
    const id = nextWorkOrderId++;
    listRows.push(summaryWire(id, e.work_order_number as string));
    return { ...e, outcome: 'CREATED', work_order_id: id };
  });
  return json(reportWire(false, token, committed));
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  if (url === '/api/session') return sessionResponse();
  if (url === '/api/health') {
    return healthDown
      ? detail('Service unavailable.', 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/policies/due-soon') {
    return json({
      due_soon_min_days: 2,
      due_soon_lead_time_percent: 15,
      due_soon_max_days: 7,
      updated_at: '2026-10-01T08:00:00Z',
    });
  }
  if (method === 'GET' && url.startsWith('/api/work-orders')) {
    listCalls += 1;
    return json(listRows);
  }
  if (method === 'POST' && url.startsWith('/api/work-orders/import')) {
    return importRoute(url, init);
  }
  return detail(`Unhandled fake route: ${method} ${url}`, 500);
}

beforeEach(() => {
  scenario = defaultScenario();
  uploads = [];
  listRows = [summaryWire(1, '007201')];
  listCalls = 0;
  healthDown = false;
  nextPreviewFailure = null;
  nextCommitFailure = null;
  holdCommit = null;
  nextWorkOrderId = 50;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A picked file whose content can change on disk after it was read. */
function importFile(
  name: string,
  content: string = CSV_TEXT,
  type = '',
): { file: File; reads: () => number; change: (next: string) => void } {
  const file = new File([content], name, { type });
  let current = content;
  let reads = 0;
  Object.defineProperty(file, 'arrayBuffer', {
    value: () => {
      reads += 1;
      return Promise.resolve(new TextEncoder().encode(current).buffer);
    },
  });
  return {
    file,
    reads: () => reads,
    change: (next) => {
      current = next;
    },
  };
}

async function openImport() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
  await screen.findByText('007201');
  const opener = screen.getByRole('button', { name: 'Import from file…' });
  // Connectivity is confirmed by the first health answer.
  await waitFor(() => expect(opener).toBeEnabled());
  opener.focus();
  fireEvent.click(opener);
  return screen.getByRole('dialog', { name: 'Import Work Orders' });
}

function pick(dialog: HTMLElement, file: File) {
  fireEvent.change(within(dialog).getByLabelText('Choose file'), {
    target: { files: [file] },
  });
}

function checkButton(dialog: HTMLElement) {
  return within(dialog).getByRole('button', { name: /^Check file/ });
}

async function checkFile(dialog: HTMLElement) {
  fireEvent.click(checkButton(dialog));
  return within(dialog).findByRole('heading', { name: 'Check result' });
}

function importButton(dialog: HTMLElement) {
  return within(dialog).getByRole('button', { name: /^Import .*Work Order/ });
}

function uploadsTo(url: string): Upload[] {
  return uploads.filter((u) => u.url === url);
}

const PREVIEW = '/api/work-orders/import/preview';
const COMMIT = '/api/work-orders/import';

/* ============ FV-1 — the whole flow ============ */

test('FV-1: choose → Check file → report → Import N → result → Close reloads the list', async () => {
  const dialog = await openImport();
  const fileInput = within(dialog).getByLabelText('Choose file');
  expect(document.activeElement).toBe(fileInput);
  expect(fileInput).toHaveAttribute('accept', '.csv,.xlsx');
  expect(importButton(dialog)).toBeDisabled();

  const { file } = importFile(
    'orders.csv',
    CSV_TEXT,
    'application/vnd.ms-excel',
  );
  pick(dialog, file);
  expect(within(dialog).getByText('orders.csv')).toBeInTheDocument();
  const heading = await checkFile(dialog);
  expect(document.activeElement).toBe(heading);

  expect(uploadsTo(PREVIEW)).toHaveLength(1);
  expect(uploadsTo(PREVIEW)[0].contentType).toBe('text/csv');
  expect(uploadsTo(PREVIEW)[0].text).toBe(CSV_TEXT);
  expect(
    within(dialog).getByText(
      'Will create 2 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByText('2 rows read')).toBeInTheDocument();
  // Nothing was written by the check.
  expect(listRows).toHaveLength(1);

  const importIt = importButton(dialog);
  expect(importIt).toHaveTextContent('Import 2 Work Orders');
  expect(importIt).toBeEnabled();
  fireEvent.click(importIt);

  const result = await within(dialog).findByRole('heading', {
    name: 'Import result',
  });
  expect(document.activeElement).toBe(result);
  expect(
    within(dialog).getByText(
      'Created 2 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getAllByText('Created')).toHaveLength(2);
  expect(uploadsTo(COMMIT)).toHaveLength(1);
  expect(uploadsTo(COMMIT)[0].checkHeader).toBe(
    sha256(new TextEncoder().encode(CSV_TEXT)),
  );
  expect(
    within(dialog).queryByRole('button', { name: /^Import .*Work Order/ }),
  ).toBeNull();

  const callsBefore = listCalls;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(
    screen.queryByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeNull();
  expect(await screen.findByText('WO-1')).toBeInTheDocument();
  expect(screen.getByText('WO-2')).toBeInTheDocument();
  expect(listCalls).toBeGreaterThan(callsBefore);
  // Focus returns to the opener.
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Import from file…' }),
  );
});

test('FV-1: closing without importing writes nothing and does not reload', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  const callsBefore = listCalls;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.queryByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeNull();
  expect(uploadsTo(COMMIT)).toHaveLength(0);
  expect(listCalls).toBe(callsBefore);
});

/* ============ FV-2 — the same bytes ============ */

test('FV-2: Import sends the bytes read at Check file, even after the file changed on disk', async () => {
  const dialog = await openImport();
  const picked = importFile('orders.csv');
  pick(dialog, picked.file);
  await checkFile(dialog);
  picked.change(
    'Work Order Number,Part Number,Requested Quantity\r\nWO-9,Z,1\r\n',
  );

  // Check file again reuses the kept bytes — the file is read once.
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await waitFor(() => expect(uploadsTo(PREVIEW)).toHaveLength(2));
  await within(dialog).findByRole('heading', { name: 'Check result' });
  fireEvent.click(importButton(dialog));
  await within(dialog).findByRole('heading', { name: 'Import result' });

  expect(picked.reads()).toBe(1);
  expect(uploads.map((u) => u.text)).toEqual([CSV_TEXT, CSV_TEXT, CSV_TEXT]);
});

test('FV-2: picking another file clears the report and the kept bytes', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeEnabled();

  const second = importFile('more.xlsx', 'PK-bytes');
  pick(dialog, second.file);

  expect(
    within(dialog).queryByRole('heading', { name: 'Check result' }),
  ).toBeNull();
  expect(importButton(dialog)).toBeDisabled();
  expect(within(dialog).getByText(C3)).toBeInTheDocument();
  expect(checkButton(dialog)).toHaveTextContent(/^Check file$/);

  await checkFile(dialog);
  expect(second.reads()).toBe(1);
  expect(uploadsTo(PREVIEW)[1].text).toBe('PK-bytes');
  expect(uploadsTo(PREVIEW)[1].contentType).toBe(
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
});

/* ============ FV-3 — when Import is disabled ============ */

test('FV-3: disconnected disables Check file and Import, with the reason', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeEnabled();

  healthDown = true;
  await waitFor(() => expect(importButton(dialog)).toBeDisabled(), {
    timeout: 8000,
  });
  expect(checkButton(dialog)).toBeDisabled();
  expect(
    within(dialog).getByText('Reconnect to check or import the file.'),
  ).toBeInTheDocument();
  // The toolbar entry is disabled with its own reason too.
  expect(
    screen.getByRole('button', { name: 'Import from file…' }),
  ).toHaveAttribute('title', 'Reconnect to import Work Orders.');
});

test('FV-3: nothing new to import keeps Import disabled', async () => {
  scenario.entries = [
    entryWire('007201', 'EXISTS', {
      work_order_id: 1,
      existing_status: 'OPEN',
      differs_from_file: false,
      lines: [lineWire(2, 'A-100', 25)],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeDisabled();
  expect(importButton(dialog)).toHaveTextContent('Import Work Orders');
  expect(
    within(dialog).getByText('Nothing new to import.'),
  ).toBeInTheDocument();
});

test('FV-3: rows without a usable Work Order Number block the Import', async () => {
  scenario.extra = {
    commit_blocked: true,
    unassigned_rows: [
      {
        row: 4,
        column: 'Work Order Number',
        message: 'Work Order Number is missing.',
      },
      {
        row: 6,
        column: 'Work Order Number',
        message:
          'Work Order Number must be text, not a date. Format the column as Text and retype the value.',
      },
    ],
  };
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);

  expect(
    within(dialog).getByRole('heading', {
      name: 'Rows without a usable Work Order Number',
    }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText('Row 4 — Work Order Number is missing.'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText(
      '2 rows have no usable Work Order Number. Add or fix it, or delete those rows, then check the file again.',
    ),
  ).toBeInTheDocument();
  expect(importButton(dialog)).toBeDisabled();
});

test('FV-3: Import is disabled while it is in flight', async () => {
  let release!: () => void;
  holdCommit = new Promise<void>((resolve) => {
    release = resolve;
  });
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  fireEvent.click(importButton(dialog));

  await within(dialog).findByText('Importing… Keep this page open.');
  expect(importButton(dialog)).toBeDisabled();
  expect(checkButton(dialog)).toBeDisabled();
  expect(within(dialog).getByLabelText('Choose file')).toBeDisabled();
  fireEvent.click(importButton(dialog));

  await act(async () => {
    release();
  });
  await within(dialog).findByRole('heading', { name: 'Import result' });
  expect(uploadsTo(COMMIT)).toHaveLength(1);
});

/* ============ FV-4 — the in-flight Import cannot be left silently ============ */

test('FV-4: during the Import, close requests are ignored and navigation and unload are guarded', async () => {
  let release!: () => void;
  holdCommit = new Promise<void>((resolve) => {
    release = resolve;
  });
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  fireEvent.click(importButton(dialog));
  await within(dialog).findByText('Importing… Keep this page open.');

  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement!);
  const cancel = within(dialog).getByRole('button', { name: 'Cancel (Esc)' });
  expect(cancel).toBeDisabled();
  fireEvent.click(cancel);
  expect(
    screen.getByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeInTheDocument();

  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));
  expect(confirmSpy).toHaveBeenCalledWith(
    'An import is in progress. Leave anyway? The import continues on the server.',
  );
  expect(window.location.pathname).toBe('/management/work-orders');

  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);

  await act(async () => {
    release();
  });
  await within(dialog).findByRole('heading', { name: 'Import result' });

  // Both guards are released once the Import answered.
  const after = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(after);
  expect(after.defaultPrevented).toBe(false);
  confirmSpy.mockClear();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(
    screen.queryByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));
  expect(confirmSpy).not.toHaveBeenCalled();
  expect(window.location.pathname).toBe('/scan-station');
});

/* ============ FV-5 — a lost Import answer ============ */

for (const [name, failure] of [
  ['a network error', 'network'],
  ['a 503 answer', 'http503'],
] as const) {
  test(`FV-5: ${name} on Import shows the unknown outcome, never retries, and Check file again re-runs the preview`, async () => {
    const dialog = await openImport();
    pick(dialog, importFile('orders.csv').file);
    await checkFile(dialog);
    nextCommitFailure =
      failure === 'network'
        ? 'network'
        : detail('The service is unavailable.', 503);
    fireEvent.click(importButton(dialog));

    expect(await within(dialog).findByText(UNKNOWN_COPY)).toBeInTheDocument();
    expect(uploadsTo(COMMIT)).toHaveLength(1);
    expect(
      within(dialog).queryByRole('heading', { name: 'Check result' }),
    ).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeEnabled();

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Check file again' }),
    );
    await within(dialog).findByRole('heading', { name: 'Check result' });
    expect(uploadsTo(PREVIEW)).toHaveLength(2);
    expect(uploadsTo(COMMIT)).toHaveLength(1);

    // The outcome was unknown, so closing reloads the list.
    const callsBefore = listCalls;
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
  });
}

test('FV-5: closing straight from the unknown outcome reloads the list', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  nextCommitFailure = 'network';
  fireEvent.click(importButton(dialog));
  await within(dialog).findByText(UNKNOWN_COPY);

  const callsBefore = listCalls;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(
    screen.queryByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeNull();
  await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
});

test('FV-5: a 409 "not the file that was checked" shows the message and Check file again', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  nextCommitFailure = detail(C4, 409);
  fireEvent.click(importButton(dialog));

  const alert = await within(dialog).findByRole('alert');
  expect(alert).toHaveTextContent(C4);
  expect(within(dialog).queryByText(UNKNOWN_COPY)).toBeNull();
  expect(importButton(dialog)).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(importButton(dialog)).toBeEnabled();
  expect(within(dialog).queryByRole('alert')).toBeNull();
});

/* ============ FV-6 — file-level refusals ============ */

test('FV-6: a file-level 422 and a 413 show one alert and keep the file', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  const f9 =
    'Required column missing: Requested Quantity. The header row must name Work Order Number, Part Number and Requested Quantity.';
  nextPreviewFailure = detail(f9, 422);
  fireEvent.click(checkButton(dialog));
  const alert = await within(dialog).findByRole('alert');
  expect(alert).toHaveTextContent(f9);
  expect(within(dialog).getAllByRole('alert')).toHaveLength(1);
  expect(within(dialog).getByText('orders.csv')).toBeInTheDocument();
  expect(importButton(dialog)).toBeDisabled();

  nextPreviewFailure = detail(
    'The file is larger than 1 MB. Split it into smaller files.',
    413,
  );
  fireEvent.click(checkButton(dialog));
  await waitFor(() =>
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'The file is larger than 1 MB. Split it into smaller files.',
    ),
  );
  expect(within(dialog).getAllByRole('alert')).toHaveLength(1);
});

test('FV-6: a client-side oversize, empty or unsupported file never sends a request', async () => {
  const dialog = await openImport();
  const big = new File([new Uint8Array(1_048_577)], 'orders.csv');
  pick(dialog, big);
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The file is larger than 1 MB. Split it into smaller files.',
  );
  expect(checkButton(dialog)).toBeDisabled();

  pick(dialog, new File([], 'orders.xlsx'));
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The file is empty.',
  );
  pick(dialog, new File(['x'], 'orders.xls'));
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Choose a .csv or .xlsx file.',
  );
  expect(checkButton(dialog)).toBeDisabled();
  expect(uploads).toHaveLength(0);
});

/* ============ FV-7 — the report ============ */

test('FV-7: the report lists refused first, explains existing numbers and discloses the lines', async () => {
  scenario.entries = [
    entryWire('007300', 'EXISTS', {
      rows: [2],
      work_order_id: 3,
      existing_status: 'OPEN',
      differs_from_file: true,
      lines: [lineWire(2, 'D-400', 9)],
    }),
    entryWire('WO-NEW', 'WILL_CREATE', {
      rows: [3, 4, 5],
      lines: [
        lineWire(3, 'A-100', 5, '2026-07-24', '18112'),
        lineWire(4, 'B-200', 3),
        lineWire(5, 'C-300', 1),
      ],
      new_part_numbers: ['B-200', 'C-300'],
      lines_without_due_date: 2,
    }),
    entryWire('006996', 'EXISTS', {
      rows: [6],
      work_order_id: 4,
      existing_status: 'COMPLETED',
      differs_from_file: true,
      lines: [lineWire(6, 'E-500', 2)],
    }),
    entryWire('WO-BAD', 'REFUSED', {
      rows: [7, 9],
      errors: [
        { row: 9, column: 'Part Number', message: 'Part Number is required.' },
        {
          row: null,
          column: null,
          message:
            'This Work Order could not be created because another change happened at the same time. Check the file again.',
        },
      ],
    }),
  ];
  scenario.extra = {
    file_format: 'XLSX',
    worksheet: 'Orders',
    empty_rows_ignored: 1,
    ignored_columns: ['Revision', 'Column F (no header)'],
    lines_without_due_date: 99,
  };
  const dialog = await openImport();
  pick(dialog, importFile('orders.xlsx', 'PK').file);
  await checkFile(dialog);

  expect(
    within(dialog).getByText('Worksheet read: Orders'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText('7 rows read · 1 empty row ignored'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText('Ignored columns: Revision, Column F (no header)'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText(
      'Imported Work Orders get no Work Order due date — they stay unscheduled.',
    ),
  ).toBeInTheDocument();
  // Only the lines this import creates are counted.
  expect(
    within(dialog).getByText(
      '2 lines have no due date and sort after dated demand.',
    ),
  ).toBeInTheDocument();

  const rows = Array.from(
    dialog.querySelectorAll<HTMLElement>('tr.wo-import-entry'),
  );
  expect(
    rows.map((row) => within(row).getByText(/^(WO-|00)/).textContent),
  ).toEqual(['WO-BAD', 'WO-NEW', '007300', '006996']);

  const refused = rows[0];
  expect(
    within(refused).getByText('Not imported — fix the rows listed'),
  ).toBeInTheDocument();
  expect(within(refused).getByText('7, 9')).toBeInTheDocument();
  expect(
    within(refused).getByText('Row 9 · Part Number — Part Number is required.'),
  ).toBeInTheDocument();
  expect(
    within(refused).getByText(
      'This Work Order could not be created because another change happened at the same time. Check the file again.',
    ),
  ).toBeInTheDocument();

  const created = rows[1];
  expect(within(created).getByText('Will be created')).toBeInTheDocument();
  expect(within(created).getByText('2 new Part Numbers')).toBeInTheDocument();
  expect(within(created).getByText('3–5')).toBeInTheDocument();
  const toggle = within(created).getByRole('button', {
    name: 'Show lines of WO-NEW',
  });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const lines = dialog.querySelector<HTMLElement>('table.wo-import-lines')!;
  expect(within(lines).getByText('Jul 24, 2026')).toBeInTheDocument();
  expect(within(lines).getByText('18112')).toBeInTheDocument();
  // An absent due date and Job Number render as —.
  const undatedRow = within(lines).getByText('B-200').closest('tr')!;
  expect(within(undatedRow).getAllByText('—')).toHaveLength(2);

  expect(
    within(rows[2]).getByText(
      'Differs from this file — open the Work Order to apply changes.',
    ),
  ).toBeInTheDocument();
  expect(
    within(rows[3]).getByText(
      'Differs from this file — this Work Order is completed and is never changed.',
    ),
  ).toBeInTheDocument();
  expect(
    within(rows[3]).getByText(
      'Already in PartFlow — not changed by this import',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getAllByText('Due dates and Job Numbers are not compared.'),
  ).toHaveLength(1);
  // Status is never color alone: each label carries its icon.
  expect(within(refused).getByText('✕')).toBeInTheDocument();
  expect(within(created).getByText('✓')).toBeInTheDocument();
  expect(within(rows[2]).getByText('•')).toBeInTheDocument();
  expect(importButton(dialog)).toHaveTextContent('Import 1 Work Order');
});

test('FV-7: a CSV report names no worksheet, and dated lines need no undated sentence', async () => {
  scenario.entries = [
    entryWire('WO-1', 'WILL_CREATE', {
      lines: [lineWire(2, 'A-100', 5, '2026-07-24')],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);

  expect(within(dialog).queryByText(/^Worksheet read:/)).toBeNull();
  expect(
    within(dialog).getByText(
      'Imported Work Orders get no Work Order due date — they stay unscheduled.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(/no due date and sort/)).toBeNull();
  expect(within(dialog).queryByText(/not compared/)).toBeNull();
  expect(within(dialog).getByText('1 row read')).toBeInTheDocument();
});
