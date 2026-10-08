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
import type { Permission } from '../../api/roles';

// Import Work Orders dialog (Phase 15 slices 1–2 — GUI_DESIGN §11.7),
// driven through the real Work Orders view against an in-memory fake of
// the import routes that answers the exact wire contract: the preview
// returns the SHA-256 check token of the bytes it received, the
// `update_token` of its change list and the permissions its content
// needs; the Import refuses a missing token (422) or a token of other
// bytes (409), a malformed confirmation (422 C5) and a missing content
// permission (403), and changes an existing Work Order only when the
// confirmation equals its own change list's token — otherwise every
// change is refused per Work Order (U4) while creates proceed, exactly
// like the server. Covered: the whole flow and the list reload, the
// same-bytes rule, every reason Import is disabled, the in-flight state
// (no close, navigation and unload guarded), the lost-outcome state,
// sign-in and permission refusals, file-level refusals, focus after a
// failed step, the report presentation, and the typed confirmation of
// changes to existing Work Orders.

const C3 = 'Check the file before importing it.';
const C4 =
  'This is not the file that was checked. Check the file again before importing.';
const UNKNOWN_COPY =
  'The import may be partly saved. Check the file again: Work Orders already in PartFlow are never duplicated, and changes already saved are not listed again.';
const C5 =
  'The import confirmation is not valid. Check the file again and confirm the changes.';
const U3 =
  'This Work Order changed after the file was checked, so nothing was changed on it. Check the file again.';
const U4 =
  'Work Orders in this file changed after the changes were confirmed, so this Work Order was not changed. Check the file again and confirm the new changes.';
const STALE_LINE =
  'Some Work Orders were not changed because they changed after the check. Check the file again to see and confirm the current changes.';
const A1 =
  'You are not signed in, or your sign-in has ended. Sign in to continue.';
const A3 = 'You do not have permission to do this.';
const CSV_TEXT =
  'Work Order Number,Part Number,Requested Quantity\r\nWO-1,A-100,5\r\nWO-2,B-200,3\r\n';

type Wire = Record<string, unknown>;

interface Upload {
  url: string;
  contentType: string;
  checkHeader: string | null;
  confirmHeader: string | null;
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
let sessionPermissions: readonly Permission[];
/** Work Order Numbers whose update the commit refuses as changed after
 * the check (U3), as a concurrent edit would. */
let changedAfterCheck: Set<string>;
/** Work Order Numbers someone else creates between the check and the
 * Import: the commit's create falls back to the never-compared EXISTS
 * entry (`lines_not_in_file` null), as the server does. */
let createdAfterCheck: Set<string>;

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

function editWire(
  row: number,
  partNumber: string,
  demandId: number,
  extra?: Wire,
): Wire {
  return {
    kind: 'EDIT_LINE',
    row,
    part_number: partNumber,
    demand_id: demandId,
    new_part_number: false,
    requested_quantity: null,
    due_date: null,
    job_numbers: null,
    leaves_hot_list: false,
    ...extra,
  };
}

function addWire(
  row: number,
  partNumber: string,
  quantity: number,
  extra?: Wire,
): Wire {
  return {
    kind: 'ADD_LINE',
    row,
    part_number: partNumber,
    demand_id: null,
    new_part_number: false,
    requested_quantity: { before: null, after: quantity },
    due_date: { before: null, after: null },
    job_numbers: { before: [], after: [] },
    leaves_hot_list: false,
    ...extra,
  };
}

/** A WILL_UPDATE entry of an existing active Work Order. */
function updateEntry(
  number: string,
  id: number,
  status: 'OPEN' | 'RELEASED',
  changes: Wire[],
  extra?: Wire,
): Wire {
  return entryWire(number, 'WILL_UPDATE', {
    rows: changes.map((c) => c.row as number),
    lines: changes.map((c) =>
      lineWire(c.row as number, c.part_number as string, 1),
    ),
    changes,
    completes_work_order: false,
    lines_not_in_file: [],
    work_order_id: id,
    existing_status: status,
    ...extra,
  });
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

/** The digest of the change list (what the typed confirmation binds). */
function updateToken(entries: Wire[]): string | null {
  const updates = entries.filter((e) => e.outcome === 'WILL_UPDATE');
  if (updates.length === 0) return null;
  return sha256(new TextEncoder().encode(JSON.stringify(updates)));
}

/** Create → Create and edit Work Orders; change → Edit Work Order
 * Demand (sorted keys). */
function requiredPermissions(entries: Wire[]): string[] {
  const keys: string[] = [];
  if (count(entries, 'WILL_UPDATE') > 0) keys.push('EDIT_WORK_ORDER_DEMAND');
  if (count(entries, 'WILL_CREATE') > 0) keys.push('MANAGE_WORK_ORDERS');
  return keys;
}

function reportWire(
  dryRun: boolean,
  token: string,
  entries: Wire[],
  required: string[] = requiredPermissions(entries),
): Wire {
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
    update_token: dryRun ? updateToken(entries) : null,
    required_permissions: required,
    work_orders: entries,
    unassigned_rows: [],
    summary: dryRun
      ? {
          will_create: count(entries, 'WILL_CREATE'),
          will_update: count(entries, 'WILL_UPDATE'),
          existing: count(entries, 'EXISTS'),
          refused: count(entries, 'REFUSED'),
        }
      : {
          created: count(entries, 'CREATED'),
          updated: count(entries, 'UPDATED'),
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
      permissions: sessionPermissions,
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
    confirmHeader: headers['X-PartFlow-Import-Confirm'] ?? null,
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
  const confirm = headers['X-PartFlow-Import-Confirm'];
  if (confirm !== undefined && !/^[0-9a-f]{64}$/.test(confirm)) {
    return detail(C5, 422);
  }
  // The commit plans the file again over the current data.
  const required = requiredPermissions(scenario.entries);
  const missing = required.filter(
    (key) => !sessionPermissions.includes(key as Permission),
  );
  if (missing.length > 0) {
    return json(
      {
        detail: A3,
        permission_denied: true,
        required_permissions: required,
      },
      403,
    );
  }
  const planned = updateToken(scenario.entries);
  const confirmed = planned === null || confirm === planned;
  const refused = (e: Wire, message: string): Wire => ({
    ...e,
    outcome: 'REFUSED',
    lines: [],
    changes: null,
    completes_work_order: null,
    lines_not_in_file: null,
    lines_without_due_date: 0,
    new_part_numbers: [],
    errors: [{ row: null, column: null, message }],
  });
  const committed = scenario.entries.map((e) => {
    if (e.outcome === 'WILL_UPDATE') {
      if (!confirmed) return refused(e, U4);
      if (changedAfterCheck.has(e.work_order_number as string)) {
        return refused(e, U3);
      }
      return { ...e, outcome: 'UPDATED' };
    }
    if (e.outcome !== 'WILL_CREATE') return e;
    if (createdAfterCheck.has(e.work_order_number as string)) {
      return {
        ...e,
        outcome: 'EXISTS',
        new_part_numbers: [],
        lines_without_due_date: 0,
        work_order_id: nextWorkOrderId++,
        existing_status: 'OPEN',
        differs_from_file: true,
      };
    }
    const id = nextWorkOrderId++;
    listRows.push(summaryWire(id, e.work_order_number as string));
    return { ...e, outcome: 'CREATED', work_order_id: id };
  });
  return json(reportWire(false, token, committed, required));
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
  sessionPermissions = PERMISSIONS;
  changedAfterCheck = new Set();
  createdAfterCheck = new Set();
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
  return within(dialog).getByRole('button', {
    name: /^(Import|Change|Create) .*Work Order/,
  });
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
      'Will create 2 · Will change 0 · Already in PartFlow 0 · Not imported 0',
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
      'Created 2 · Changed 0 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getAllByText('Created')).toHaveLength(2);
  expect(uploadsTo(COMMIT)).toHaveLength(1);
  expect(uploadsTo(COMMIT)[0].checkHeader).toBe(
    sha256(new TextEncoder().encode(CSV_TEXT)),
  );
  // A create-only file needs no typed confirmation and sends none.
  expect(uploadsTo(COMMIT)[0].confirmHeader).toBeNull();
  expect(
    within(dialog).queryByRole('button', {
      name: /^(Import|Change|Create) .*Work Order/,
    }),
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

test('FV-3: nothing to create or change keeps Import disabled', async () => {
  scenario.entries = [
    entryWire('007201', 'EXISTS', {
      work_order_id: 1,
      existing_status: 'OPEN',
      differs_from_file: false,
      lines_not_in_file: [],
      lines: [lineWire(2, 'A-100', 25)],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeDisabled();
  expect(importButton(dialog)).toHaveTextContent('Import Work Orders');
  expect(
    within(dialog).getByText('Nothing to create or change.'),
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
    // The Import button is gone: focus moves to the alert, inside the dialog.
    expect(within(dialog).getByRole('alert')).toHaveFocus();
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
  await waitFor(() => expect(alert).toHaveFocus());
  expect(within(dialog).queryByText(UNKNOWN_COPY)).toBeNull();
  expect(importButton(dialog)).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(importButton(dialog)).toBeEnabled();
  expect(within(dialog).queryByRole('alert')).toBeNull();
});

test('FV-5: a 401 on Import asks for the sign-in and keeps the checked report; Import again sends the same token', async () => {
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  nextCommitFailure = json({ detail: A1, authentication_required: true }, 401);
  fireEvent.click(importButton(dialog));

  expect(await within(dialog).findByRole('alert')).toHaveTextContent(A1);
  const signIn = await screen.findByRole('dialog', { name: 'Sign in' });
  // The dialog keeps its state: nothing ran, so the report still stands.
  expect(
    within(dialog).getByRole('heading', { name: 'Check result' }),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(UNKNOWN_COPY)).toBeNull();

  fireEvent.change(within(signIn).getByLabelText('Login name'), {
    target: { value: 'mia' },
  });
  fireEvent.change(within(signIn).getByLabelText('Password'), {
    target: { value: 'secret-password' },
  });
  fireEvent.click(within(signIn).getByRole('button', { name: 'Sign in' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull(),
  );

  await waitFor(() => expect(importButton(dialog)).toBeEnabled());
  fireEvent.click(importButton(dialog));
  await within(dialog).findByRole('heading', { name: 'Import result' });
  const commits = uploadsTo(COMMIT);
  expect(commits).toHaveLength(2);
  expect(commits[1].checkHeader).toBe(commits[0].checkHeader);
  expect(commits[1].text).toBe(commits[0].text);
  expect(uploadsTo(PREVIEW)).toHaveLength(1);
});

for (const [name, body] of [
  [
    'a permission refusal',
    {
      detail: A3,
      permission_denied: true,
      required_permissions: ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_WORK_ORDERS'],
      any_permission: true,
    },
  ],
  ['a refused CSRF check', { detail: A3, csrf_rejected: true }],
] as const) {
  test(`FV-5: ${name} (403) on Import shows the detail, keeps the report and focus, and Import again sends the same token`, async () => {
    const dialog = await openImport();
    pick(dialog, importFile('orders.csv').file);
    await checkFile(dialog);
    nextCommitFailure = json(body, 403);
    fireEvent.click(importButton(dialog));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent(A3);
    await waitFor(() => expect(alert).toHaveFocus());
    expect(
      within(dialog).getByRole('heading', { name: 'Check result' }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(UNKNOWN_COPY)).toBeNull();
    expect(importButton(dialog)).toBeEnabled();

    fireEvent.click(importButton(dialog));
    await within(dialog).findByRole('heading', { name: 'Import result' });
    const commits = uploadsTo(COMMIT);
    expect(commits).toHaveLength(2);
    expect(commits[1].checkHeader).toBe(commits[0].checkHeader);
    expect(uploadsTo(PREVIEW)).toHaveLength(1);
  });
}

for (const [name, failure] of [
  ['401', json({ detail: A1, authentication_required: true }, 401)],
  [
    '403',
    json(
      {
        detail: A3,
        permission_denied: true,
        required_permissions: ['MANAGE_WORK_ORDERS'],
      },
      403,
    ),
  ],
] as const) {
  test(`FV-6: a ${name} on Check file shows the detail and keeps the chosen file`, async () => {
    const dialog = await openImport();
    pick(dialog, importFile('orders.csv').file);
    nextPreviewFailure = failure;
    fireEvent.click(checkButton(dialog));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent(name === '401' ? A1 : A3);
    expect(within(dialog).getByText('orders.csv')).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('heading', { name: 'Check result' }),
    ).toBeNull();
    expect(importButton(dialog)).toBeDisabled();
    if (name === '401') {
      expect(
        await screen.findByRole('dialog', { name: 'Sign in' }),
      ).toBeInTheDocument();
    } else {
      await waitFor(() => expect(alert).toHaveFocus());
    }
  });
}

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
  await waitFor(() => expect(alert).toHaveFocus());
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
      lines_not_in_file: ['K-900'],
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
  // The server's total: only the lines this import writes.
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
    within(rows[2]).getByText('Already in PartFlow — nothing to change'),
  ).toBeInTheDocument();
  expect(
    within(rows[2]).getByText('Kept, not in this file: K-900'),
  ).toBeInTheDocument();
  expect(within(rows[2]).queryByText(/open the Work Order/)).toBeNull();
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
    within(dialog).getAllByText(
      'For completed Work Orders, due dates and Job Numbers are not compared.',
    ),
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

/* ============ FV-9 – FV-12 — changes to existing Work Orders ============ */

/** Two Open/Released Work Orders the file changes (3 changes). */
function updateScenario(): Wire[] {
  return [
    updateEntry(
      '007201',
      1,
      'OPEN',
      [
        editWire(2, 'A-100', 101, {
          requested_quantity: { before: 10, after: 6 },
          leaves_hot_list: true,
        }),
        addWire(3, 'N-1', 4, {
          new_part_number: true,
          due_date: { before: null, after: '2026-07-24' },
          job_numbers: { before: [], after: ['18112'] },
        }),
      ],
      {
        new_part_numbers: ['N-1'],
        lines_not_in_file: ['K-9'],
      },
    ),
    updateEntry(
      '007300',
      3,
      'RELEASED',
      [
        editWire(4, 'C-300', 301, {
          due_date: { before: '2026-07-01', after: '2026-08-03' },
          job_numbers: { before: ['J1'], after: ['J1', 'J2'] },
        }),
      ],
      { completes_work_order: true },
    ),
  ];
}

const CHANGE_TEXTS = [
  'Row 2 · A-100 · Qty 10 → 6 · leaves the Hot list',
  'Row 3 · Add N-1 · Qty 4 · Due Jul 24, 2026 · Job 18112 · new Part Number',
  'Row 4 · C-300 · Due Jul 01, 2026 → Aug 03, 2026 · Job Numbers J1 → J1, J2',
];
const COMPLETES =
  'Completes the Work Order — every line becomes fully allocated.';

function typedDialog(name: string) {
  return screen.getByRole('dialog', { name });
}

function typeConfirmation(typed: HTMLElement, value: string) {
  fireEvent.change(within(typed).getByRole('textbox'), {
    target: { value },
  });
}

test('FV-9: an update file lists the changes, Import asks for the typed confirmation of every change and sends both tokens', async () => {
  scenario.entries = updateScenario();
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);

  expect(
    within(dialog).getByText(
      'Will create 0 · Will change 2 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
  const rows = Array.from(
    dialog.querySelectorAll<HTMLElement>('tr.wo-import-entry'),
  );
  expect(
    within(rows[0]).getByText('Will change — 2 changes'),
  ).toBeInTheDocument();
  expect(within(rows[0]).getByText('1 new Part Number')).toBeInTheDocument();
  expect(within(rows[0]).getByText('✓')).toBeInTheDocument();
  expect(
    within(rows[1]).getByText('Will change — 1 change'),
  ).toBeInTheDocument();

  // Show changes discloses the change list of one Work Order.
  const toggle = within(rows[0]).getByRole('button', {
    name: 'Show changes of 007201',
  });
  expect(toggle).toHaveTextContent('Show changes');
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const list = dialog.querySelector<HTMLElement>('ul.wo-import-changes')!;
  expect(within(list).getByText(CHANGE_TEXTS[0])).toBeInTheDocument();
  expect(within(list).getByText(CHANGE_TEXTS[1])).toBeInTheDocument();
  expect(
    within(list).getByText('Kept, not in this file: K-9'),
  ).toBeInTheDocument();

  const importIt = importButton(dialog);
  expect(importIt).toHaveTextContent('Change 2 Work Orders…');
  expect(importIt).toBeEnabled();
  importIt.focus();
  fireEvent.click(importIt);
  // Nothing is sent before the confirmation.
  expect(uploadsTo(COMMIT)).toHaveLength(0);

  const typed = typedDialog('Change 2 existing Work Orders?');
  expect(
    within(typed).getByText(
      /These Work Orders are already in PartFlow\. Import applies every change below; each Work Order is saved on its own\./,
    ),
  ).toBeInTheDocument();
  expect(within(typed).queryByText(/It also creates/)).toBeNull();
  const region = within(typed).getByRole('region', {
    name: 'Changes to existing Work Orders',
  });
  expect(region).toHaveAttribute('tabindex', '0');
  expect(
    within(region).getByRole('heading', { name: 'WO 007201 · Open' }),
  ).toBeInTheDocument();
  expect(
    within(region).getByRole('heading', { name: 'WO 007300 · Released' }),
  ).toBeInTheDocument();
  for (const text of CHANGE_TEXTS) {
    expect(within(region).getByText(text)).toBeInTheDocument();
  }
  expect(within(region).getByText(COMPLETES)).toBeInTheDocument();
  expect(
    within(region).getByText('Kept, not in this file: K-9'),
  ).toBeInTheDocument();
  expect(within(typed).getByText('CHANGE 2')).toBeInTheDocument();
  expect(
    within(typed).getByText('(Work Orders to change)'),
  ).toBeInTheDocument();

  const confirm = within(typed).getByRole('button', {
    name: 'Import and change 2 Work Orders',
  });
  expect(confirm).toBeDisabled();
  typeConfirmation(typed, 'change 1');
  expect(confirm).toBeDisabled();
  typeConfirmation(typed, '  change 2 ');
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);

  const result = await within(dialog).findByRole('heading', {
    name: 'Import result',
  });
  expect(result).toBeInTheDocument();
  expect(
    screen.queryByRole('dialog', { name: 'Change 2 existing Work Orders?' }),
  ).toBeNull();
  const [commit] = uploadsTo(COMMIT);
  expect(commit.checkHeader).toBe(sha256(new TextEncoder().encode(CSV_TEXT)));
  expect(commit.confirmHeader).toBe(updateToken(scenario.entries));
  expect(commit.confirmHeader).toMatch(/^[0-9a-f]{64}$/);
  expect(
    within(dialog).getByText(
      'Created 0 · Changed 2 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByText('Changed — 2 changes')).toBeInTheDocument();
  expect(within(dialog).getByText('Changed — 1 change')).toBeInTheDocument();

  // A change was saved: closing reloads the list.
  const callsBefore = listCalls;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(listCalls).toBeGreaterThan(callsBefore));
});

test('FV-9: one Work Order to change, plus Work Orders to create — singular labels and the create sentence', async () => {
  scenario.entries = [
    updateScenario()[1],
    entryWire('WO-NEW', 'WILL_CREATE', {
      rows: [5],
      lines: [lineWire(5, 'A-100', 2, '2026-07-24')],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toHaveTextContent(
    'Create 1 Work Order, change 1 Work Order…',
  );
  fireEvent.click(importButton(dialog));

  const typed = typedDialog('Change 1 existing Work Order?');
  expect(
    within(typed).getByText(/It also creates 1 new Work Order\./),
  ).toBeInTheDocument();
  expect(within(typed).getByText('CHANGE 1')).toBeInTheDocument();
  typeConfirmation(typed, 'CHANGE 1');
  fireEvent.click(
    within(typed).getByRole('button', {
      name: 'Import and change 1 Work Order',
    }),
  );
  await within(dialog).findByRole('heading', { name: 'Import result' });
  expect(
    within(dialog).getByText(
      'Created 1 · Changed 1 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
});

test('FV-10: Cancel and Esc in the typed confirmation send nothing and return to the check result', async () => {
  scenario.entries = updateScenario();
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  const importIt = importButton(dialog);
  importIt.focus();

  fireEvent.click(importIt);
  let typed = typedDialog('Change 2 existing Work Orders?');
  typeConfirmation(typed, 'CHANGE 2');
  fireEvent.click(within(typed).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.queryByRole('dialog', { name: 'Change 2 existing Work Orders?' }),
  ).toBeNull();
  expect(document.activeElement).toBe(importIt);

  // Esc closes only the topmost dialog.
  fireEvent.click(importIt);
  typed = typedDialog('Change 2 existing Work Orders?');
  // The typed value is not kept: a new confirmation starts empty.
  expect(within(typed).getByRole('textbox')).toHaveValue('');
  fireEvent.keyDown(within(typed).getByRole('textbox'), { key: 'Escape' });
  expect(
    screen.queryByRole('dialog', { name: 'Change 2 existing Work Orders?' }),
  ).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Import Work Orders' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('heading', { name: 'Check result' }),
  ).toBeInTheDocument();
  expect(uploadsTo(COMMIT)).toHaveLength(0);
});

test('FV-10: disconnected disables the typed confirmation even when the value is typed', async () => {
  scenario.entries = updateScenario();
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  fireEvent.click(importButton(dialog));
  const typed = typedDialog('Change 2 existing Work Orders?');
  typeConfirmation(typed, 'CHANGE 2');
  const confirm = within(typed).getByRole('button', {
    name: 'Import and change 2 Work Orders',
  });
  expect(confirm).toBeEnabled();

  healthDown = true;
  await waitFor(() => expect(confirm).toBeDisabled(), { timeout: 8000 });
  fireEvent.click(confirm);
  expect(uploadsTo(COMMIT)).toHaveLength(0);
  // Cancel is never affected.
  expect(
    within(typed).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeEnabled();
});

test('FV-11: confirmed changes that no longer match are refused per Work Order while creates commit; Check file again needs a new typed confirmation', async () => {
  scenario.entries = [
    ...updateScenario(),
    entryWire('WO-NEW', 'WILL_CREATE', {
      rows: [6],
      lines: [lineWire(6, 'A-100', 2, '2026-07-24')],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  const firstToken = updateToken(scenario.entries);

  // Someone changes a planned line after the check: the commit's own
  // change list differs from the confirmed one.
  scenario.entries = [
    updateEntry('007201', 1, 'OPEN', [
      editWire(2, 'A-100', 101, {
        requested_quantity: { before: 12, after: 6 },
      }),
    ]),
    updateScenario()[1],
    scenario.entries[2],
  ];

  fireEvent.click(importButton(dialog));
  let typed = typedDialog('Change 2 existing Work Orders?');
  typeConfirmation(typed, 'CHANGE 2');
  fireEvent.click(
    within(typed).getByRole('button', {
      name: 'Import and change 2 Work Orders',
    }),
  );
  await within(dialog).findByRole('heading', { name: 'Import result' });
  expect(uploadsTo(COMMIT)[0].confirmHeader).toBe(firstToken);
  expect(
    within(dialog).getByText(
      'Created 1 · Changed 0 · Already in PartFlow 0 · Not imported 2',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByText(STALE_LINE)).toBeInTheDocument();
  expect(within(dialog).getAllByText(U4)).toHaveLength(2);

  // Check file again (WO-NEW was created and is left out here): the
  // current changes, a new token, a new typed confirmation.
  scenario.entries = scenario.entries.slice(0, 2);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(uploadsTo(PREVIEW)).toHaveLength(2);
  expect(within(dialog).queryByText(STALE_LINE)).toBeNull();
  fireEvent.click(importButton(dialog));
  typed = typedDialog('Change 2 existing Work Orders?');
  expect(within(typed).getByRole('textbox')).toHaveValue('');
  expect(
    within(typed).getByRole('button', {
      name: 'Import and change 2 Work Orders',
    }),
  ).toBeDisabled();
  expect(
    within(typed).getByText('Row 2 · A-100 · Qty 12 → 6'),
  ).toBeInTheDocument();
  typeConfirmation(typed, 'change 2');
  fireEvent.click(
    within(typed).getByRole('button', {
      name: 'Import and change 2 Work Orders',
    }),
  );
  await within(dialog).findByRole('heading', { name: 'Import result' });
  const commits = uploadsTo(COMMIT);
  expect(commits).toHaveLength(2);
  expect(commits[1].confirmHeader).toBe(updateToken(scenario.entries));
  expect(commits[1].confirmHeader).not.toBe(firstToken);
  expect(
    within(dialog).getByText(
      'Created 0 · Changed 2 · Already in PartFlow 0 · Not imported 0',
    ),
  ).toBeInTheDocument();
});

test('FV-11: a Work Order changed after the check is listed with its reason and the stale line', async () => {
  scenario.entries = updateScenario();
  changedAfterCheck.add('007300');
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  fireEvent.click(importButton(dialog));
  const typed = typedDialog('Change 2 existing Work Orders?');
  typeConfirmation(typed, 'CHANGE 2');
  fireEvent.click(
    within(typed).getByRole('button', {
      name: 'Import and change 2 Work Orders',
    }),
  );
  await within(dialog).findByRole('heading', { name: 'Import result' });

  const rows = Array.from(
    dialog.querySelectorAll<HTMLElement>('tr.wo-import-entry'),
  );
  // Refused first, then the changed one.
  expect(
    within(rows[0]).getByText('Not imported — fix the rows listed'),
  ).toBeInTheDocument();
  expect(within(rows[0]).getByText('007300')).toBeInTheDocument();
  expect(within(rows[0]).getByText(U3)).toBeInTheDocument();
  expect(within(rows[1]).getByText('Changed — 2 changes')).toBeInTheDocument();
  expect(within(dialog).getByText(STALE_LINE)).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  ).toBeEnabled();
});

test('FV-11: a 422 refused confirmation shows the message and Check file again', async () => {
  scenario.entries = updateScenario();
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  nextCommitFailure = detail(C5, 422);
  fireEvent.click(importButton(dialog));
  const typed = typedDialog('Change 2 existing Work Orders?');
  typeConfirmation(typed, 'CHANGE 2');
  fireEvent.click(
    within(typed).getByRole('button', {
      name: 'Import and change 2 Work Orders',
    }),
  );

  const alert = await within(dialog).findByRole('alert');
  expect(alert).toHaveTextContent(C5);
  await waitFor(() => expect(alert).toHaveFocus());
  expect(
    within(dialog).queryByRole('heading', { name: 'Check result' }),
  ).toBeNull();
  expect(importButton(dialog)).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(importButton(dialog)).toBeEnabled();
});

test('FV-11: a Work Order created by someone else after the check is never reported as "nothing to change"', async () => {
  scenario.entries = [
    entryWire('WO-RACE', 'WILL_CREATE', {
      lines: [lineWire(2, 'Y-100', 10, '2026-07-24')],
    }),
  ];
  createdAfterCheck.add('WO-RACE');
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  fireEvent.click(importButton(dialog));
  await within(dialog).findByRole('heading', { name: 'Import result' });

  expect(
    within(dialog).getByText(
      'Already in PartFlow — not changed by this import',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByText('Already in PartFlow — nothing to change'),
  ).toBeNull();
  expect(
    within(dialog).getByText(
      'Differs from this file — check the file again to see the changes.',
    ),
  ).toBeInTheDocument();
});

test('FV-11: a content permission refusal on Import drops the outdated report and offers Check file again', async () => {
  // Checked as two new Work Orders by a creator without Edit Work Order
  // Demand; before Import someone else creates WO-1, so the commit's own
  // plan now changes it and needs that key.
  scenario.entries = [
    entryWire('WO-1', 'WILL_CREATE', {
      lines: [lineWire(2, 'A-100', 2, '2026-07-24')],
    }),
    entryWire('WO-2', 'WILL_CREATE', {
      rows: [3],
      lines: [lineWire(3, 'B-200', 3, '2026-07-24')],
    }),
  ];
  sessionPermissions = ['MANAGE_WORK_ORDERS'];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeEnabled();
  scenario.entries = [...updateScenario().slice(0, 1), scenario.entries[1]];
  fireEvent.click(importButton(dialog));

  const alert = await within(dialog).findByRole('alert');
  expect(alert).toHaveTextContent(
    'Nothing was imported: this file now needs a permission your account does not have. Check the file again to see what it needs.',
  );
  await waitFor(() => expect(alert).toHaveFocus());
  expect(
    within(dialog).queryByRole('heading', { name: 'Check result' }),
  ).toBeNull();
  expect(within(dialog).queryByText(UNKNOWN_COPY)).toBeNull();
  expect(uploadsTo(COMMIT)).toHaveLength(1);

  // Checking again shows what the file now needs; Import stays disabled.
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(uploadsTo(PREVIEW)).toHaveLength(2);
  expect(importButton(dialog)).toBeDisabled();
  expect(
    within(dialog).getByText(
      'Changing existing Work Orders needs the "Edit Work Order Demand" permission.',
    ),
  ).toBeInTheDocument();
});

test('FV-12: Import is disabled with one line per missing permission', async () => {
  scenario.entries = [
    ...updateScenario(),
    entryWire('WO-NEW', 'WILL_CREATE', {
      lines: [lineWire(6, 'A-100', 2, '2026-07-24')],
    }),
  ];
  sessionPermissions = ['EDIT_WORK_ORDER_DEMAND'];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toHaveTextContent(
    'Create 1 Work Order, change 2 Work Orders…',
  );
  expect(importButton(dialog)).toBeDisabled();
  expect(
    within(dialog).getByText(
      'Creating Work Orders needs the "Create and edit Work Orders" permission.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByText(/^Changing existing Work Orders/),
  ).toBeNull();
  fireEvent.click(importButton(dialog));
  expect(
    screen.queryByRole('dialog', { name: /existing Work Order/ }),
  ).toBeNull();
  expect(uploadsTo(COMMIT)).toHaveLength(0);
});

test('FV-12: without Edit Work Order Demand a file that changes Work Orders cannot be imported', async () => {
  scenario.entries = updateScenario();
  sessionPermissions = ['MANAGE_WORK_ORDERS'];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(importButton(dialog)).toBeDisabled();
  expect(
    within(dialog).getByText(
      'Changing existing Work Orders needs the "Edit Work Order Demand" permission.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(/^Creating Work Orders/)).toBeNull();
});

test('FV-12: existing Work Orders — kept lines, the completed copy and its note only with a completed differing row', async () => {
  scenario.entries = [
    entryWire('007201', 'EXISTS', {
      work_order_id: 1,
      existing_status: 'OPEN',
      differs_from_file: true,
      lines_not_in_file: ['K-1', 'K-2'],
      lines: [lineWire(2, 'A-100', 25)],
    }),
    entryWire('007300', 'EXISTS', {
      rows: [3],
      work_order_id: 3,
      existing_status: 'RELEASED',
      differs_from_file: false,
      lines_not_in_file: [],
      lines: [lineWire(3, 'C-300', 5)],
    }),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(
    within(dialog).getAllByText('Already in PartFlow — nothing to change'),
  ).toHaveLength(2);
  expect(
    within(dialog).getByText('Kept, not in this file: K-1, K-2'),
  ).toBeInTheDocument();
  // Only active rows: no "not compared" note.
  expect(within(dialog).queryByText(/not compared/)).toBeNull();
  expect(
    within(dialog).getByText('Nothing to create or change.'),
  ).toBeInTheDocument();

  // A completed Work Order equal to the file: still no note.
  scenario.entries = [
    entryWire('006996', 'EXISTS', {
      work_order_id: 4,
      existing_status: 'COMPLETED',
      differs_from_file: false,
      lines: [lineWire(2, 'E-500', 2)],
    }),
  ];
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Check file again' }),
  );
  await waitFor(() => expect(uploadsTo(PREVIEW)).toHaveLength(2));
  await within(dialog).findByRole('heading', { name: 'Check result' });
  expect(
    within(dialog).getByText(
      'Already in PartFlow — not changed by this import',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(/not compared/)).toBeNull();
});

test('FV-12: an update-only preview states the undated added lines, never the Work Order due date line', async () => {
  scenario.entries = [
    updateEntry(
      '007201',
      1,
      'OPEN',
      [
        addWire(2, 'N-1', 4),
        addWire(3, 'N-2', 1),
        editWire(4, 'A-100', 101, {
          requested_quantity: { before: 10, after: 12 },
        }),
      ],
      { lines_without_due_date: 2 },
    ),
  ];
  const dialog = await openImport();
  pick(dialog, importFile('orders.csv').file);
  await checkFile(dialog);
  expect(
    within(dialog).getByText(
      '2 lines have no due date and sort after dated demand.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(/get no Work Order due date/)).toBeNull();
  expect(importButton(dialog)).toHaveTextContent('Change 1 Work Order…');
});
