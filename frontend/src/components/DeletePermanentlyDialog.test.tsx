import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { RetryFailedLoadsContext } from '../api/use-api-data';
import { formatInstant } from '../views/administration/archive-runs';
import { DeletePermanentlyDialog } from './DeletePermanentlyDialog';
import { DELETION_POLL_MS } from './permanent-deletion';
import type { PermanentDeletionRecord } from './permanent-deletion';

// Delete permanently… (archival design D17, build step 3; GUI_DESIGN
// §11.2 and §14.2): the preview (what goes, the refusals, an open
// request), the reason and the typed confirmation sent as typed, the
// server's 409 and 422 texts shown exactly, the request's state read
// again every few seconds to each final state, and Cancel while the
// request waits. The /api/permanent-deletion surface is faked in memory
// with the contract's routes, shapes and texts.

const E_REASON =
  'Enter the reason for this deletion: 1 to 500 characters of plain text.';
const E_WO_CONFIRMATION =
  'Type the Work Order Number of every Work Order to delete, exactly as PartFlow shows it, to confirm the deletion. Nothing was requested.';
const E_PN_CONFIRMATION =
  'Type the Part Number to delete again to confirm the deletion. Nothing was requested.';
const E_REFUSED =
  'This deletion is refused, so nothing was requested: the data to delete is linked to data that stays, or a record does not exist. The refusals name each link.';
const openText = (id: number, state: string) =>
  `Deletion request ${id} is still open (${state}). Cancel it or wait until it finishes before requesting another deletion.`;
const CHANGED =
  'The data changed after the request; nothing was deleted. Request it again.';

const REQUESTED_AT = '2026-11-18T10:00:00Z';
const STARTED_AT = '2026-11-18T10:05:00Z';
const FINISHED_AT = '2026-11-18T10:06:00Z';
const BACKUP = '20261118T100501Z-pre-purge-deletion-1-20261118T100500Z';

const ROWS = {
  audit_events: 4,
  part_movements: 12,
  quantity_flow_lineage: 1,
  quantity_flows: 2,
  assigned_route_steps: 6,
  assigned_routes: 2,
  work_order_allocations: 1,
  work_order_demands: 3,
  work_orders: 1,
  part_numbers: 0,
};

interface Scope {
  work_order_numbers: string[];
  part_numbers: string[];
}

interface RequestWire {
  id: number;
  state: string;
  scope: Scope;
  reason: string;
  requested_by: { user_id: number; display_name: string } | null;
  requested_by_operator: string | null;
  requested_at: string;
  work_orders: Record<string, unknown>[];
  part_numbers: Record<string, unknown>[];
  rows: Record<string, number>;
  started_at: string | null;
  backup_label: string | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  finished_at: string | null;
  cancelled_by: { user_id: number; display_name: string } | null;
  failure_reason: string | null;
}

type Failure = { status: number; body: Record<string, unknown> } | 'network';

/** The plan's records of a scope (the fake's data). */
function planOf(scope: Scope) {
  if (scope.part_numbers.length > 0) {
    return {
      work_orders: [
        {
          id: 2,
          work_order_number: null,
          internal: true,
          named: false,
          part_numbers: [scope.part_numbers[0]],
          demand_lines: 1,
        },
      ],
      part_numbers: [{ part_number: scope.part_numbers[0], master: true }],
    };
  }
  return {
    work_orders: scope.work_order_numbers.map((number, index) => ({
      id: index + 1,
      work_order_number: number,
      internal: false,
      named: true,
      part_numbers: ['A-100', 'B-200', 'E-500'],
      demand_lines: 3,
    })),
    part_numbers: [],
  };
}

function requestWire(
  id: number,
  scope: Scope,
  extra: Partial<RequestWire> = {},
): RequestWire {
  return {
    id,
    state: 'APPROVED',
    scope,
    reason: 'Test data entered by mistake',
    requested_by: { user_id: 9, display_name: 'Sam Super' },
    requested_by_operator: null,
    requested_at: REQUESTED_AT,
    ...planOf(scope),
    rows: ROWS,
    started_at: null,
    backup_label: null,
    pre_purge_backup: null,
    finished_at: null,
    cancelled_by: null,
    failure_reason: null,
    ...extra,
  };
}

const KEPT_ROW = {
  id: 90,
  entity_type: 'WorkOrderDemand',
  entity_id: '5',
  event_type: 'UPDATED',
  occurred_at: '2026-11-17T08:00:00.000000Z',
  kept: 'also concerns other records',
};

let requests: RequestWire[];
/** The refusals of the plan (preview and request). */
let refusals: { code: string; message: string }[];
let keptRows: (typeof KEPT_ROW)[];
let failures: Partial<
  Record<
    'GET preview' | 'GET requests' | 'POST requests' | 'POST cancel',
    Failure
  >
>;
/** Record the request before its answer fails (a lost answer). */
let recordBeforeFailing: boolean;
let writes: { method: string; url: string; body: unknown }[];
let previewReads: number;
let listReads: number;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const openRequest = () =>
  requests.find((r) => r.state === 'APPROVED' || r.state === 'RUNNING') ?? null;

const canonical = (value: string) => value.trim().toUpperCase();

function fail(key: keyof typeof failures): Response | null {
  const failure = failures[key];
  if (failure === undefined) return null;
  if (failure === 'network') throw new TypeError('Failed to fetch');
  return json(failure.body, failure.status);
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body: unknown = init?.body ? JSON.parse(String(init.body)) : undefined;
  if (method !== 'GET') writes.push({ method, url, body });
  const parsed = new URL(url, 'http://x');
  if (parsed.pathname === '/api/permanent-deletion/preview') {
    previewReads += 1;
    const refused = fail('GET preview');
    if (refused) return refused;
    const pn = parsed.searchParams.get('part_number');
    const scope: Scope =
      pn === null
        ? {
            work_order_numbers: parsed.searchParams.getAll('work_order_number'),
            part_numbers: [],
          }
        : { work_order_numbers: [], part_numbers: [canonical(pn)] };
    return json({
      scope,
      ...planOf(scope),
      rows: refusals.length > 0 ? null : ROWS,
      kept_audit_rows: refusals.length > 0 ? [] : keptRows,
      refusals,
      open_request: openRequest(),
    });
  }
  if (parsed.pathname === '/api/permanent-deletion/requests') {
    if (method === 'GET') {
      listReads += 1;
      const refused = fail('GET requests');
      if (refused) return refused;
      const limit = Number(parsed.searchParams.get('limit') ?? 50);
      return json(requests.slice(0, limit));
    }
    const sentBody = body as {
      work_order_numbers?: string[];
      part_number?: string;
      reason: string;
      confirmation: string[];
    };
    const failure = failures['POST requests'];
    if (failure !== undefined && !recordBeforeFailing) {
      return fail('POST requests') as Response;
    }
    const reason = sentBody.reason.trim();
    if (reason.length < 1 || reason.length > 500) {
      return json({ detail: E_REASON }, 422);
    }
    const scope: Scope =
      sentBody.part_number !== undefined
        ? {
            work_order_numbers: [],
            part_numbers: [canonical(sentBody.part_number)],
          }
        : {
            work_order_numbers: sentBody.work_order_numbers ?? [],
            part_numbers: [],
          };
    if (scope.part_numbers.length > 0) {
      if (
        sentBody.confirmation.length !== 1 ||
        canonical(sentBody.confirmation[0]) !== scope.part_numbers[0]
      ) {
        return json({ detail: E_PN_CONFIRMATION }, 422);
      }
    } else if (
      [...new Set(sentBody.confirmation)].sort().join('\n') !==
      [...new Set(scope.work_order_numbers)].sort().join('\n')
    ) {
      return json({ detail: E_WO_CONFIRMATION }, 422);
    }
    const open = openRequest();
    if (open !== null) {
      return json({ detail: openText(open.id, open.state) }, 409);
    }
    if (refusals.length > 0) {
      return json({ detail: E_REFUSED, deletion_refused: true, refusals }, 409);
    }
    const created = requestWire(
      Math.max(0, ...requests.map((r) => r.id)) + 1,
      scope,
      { reason },
    );
    requests = [created, ...requests];
    // A lost answer: the request was recorded all the same.
    const lost = fail('POST requests');
    if (lost) return lost;
    return json(created, 201);
  }
  const cancel = /^\/api\/permanent-deletion\/requests\/(\d+)\/cancel$/.exec(
    parsed.pathname,
  );
  if (cancel && method === 'POST') {
    const refused = fail('POST cancel');
    if (refused) return refused;
    const target = requests.find((r) => r.id === Number(cancel[1]));
    if (!target) {
      return json(
        { detail: `There is no deletion request ${cancel[1]}.` },
        404,
      );
    }
    if (target.state === 'RUNNING') {
      return json(
        {
          detail: `Deletion request ${target.id} is being carried out on the server now and cannot be cancelled. Wait until it finishes.`,
        },
        409,
      );
    }
    if (target.state !== 'APPROVED' && target.state !== 'CANCELLED') {
      return json(
        {
          detail: `Deletion request ${target.id} is finished (${target.state}) and cannot be cancelled.`,
        },
        409,
      );
    }
    if (target.state === 'APPROVED') {
      target.state = 'CANCELLED';
      target.finished_at = FINISHED_AT;
      target.cancelled_by = { user_id: 9, display_name: 'Sam Super' };
    }
    return json(target);
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
}

beforeEach(() => {
  requests = [];
  refusals = [];
  keptRows = [KEPT_ROW];
  failures = {};
  recordBeforeFailing = false;
  writes = [];
  previewReads = 0;
  listReads = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const WORK_ORDER: PermanentDeletionRecord = {
  kind: 'work-order',
  workOrderNumber: '007201',
};
const PART_NUMBER: PermanentDeletionRecord = {
  kind: 'part-number',
  partNumber: 'PN-A',
};

function renderDialog(
  record: PermanentDeletionRecord = WORK_ORDER,
  writeBlocked = false,
) {
  const onClose = vi.fn();
  const onDeleted = vi.fn();
  render(
    <DeletePermanentlyDialog
      record={record}
      writeBlocked={writeBlocked}
      onClose={onClose}
      onDeleted={onDeleted}
    />,
  );
  return { onClose, onDeleted };
}

function dialog(): HTMLElement {
  return screen.getByRole('dialog', { name: /permanently$/ });
}

async function previewLoaded() {
  await waitFor(() =>
    expect(
      screen.queryByRole('status', { name: 'Loading what would be deleted' }),
    ).toBeNull(),
  );
}

function reasonField(): HTMLTextAreaElement {
  return within(dialog()).getByLabelText(/^Reason/) as HTMLTextAreaElement;
}

function confirmationField(): HTMLInputElement {
  return within(dialog()).getByLabelText(/to confirm$/) as HTMLInputElement;
}

function requestButton(): HTMLButtonElement {
  return within(dialog()).getByRole('button', {
    name: /Request deletion|Requesting…/,
  }) as HTMLButtonElement;
}

async function fillAndRequest(reason: string, typed: string) {
  fireEvent.change(reasonField(), { target: { value: reason } });
  fireEvent.change(confirmationField(), { target: { value: typed } });
  fireEvent.click(requestButton());
}

function requestState(id: number): HTMLElement {
  return screen.getByRole('status', { name: `Deletion request ${id}` });
}

async function poll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(DELETION_POLL_MS);
  });
}

function rowsShown(): Record<string, string> {
  return Object.fromEntries(
    Array.from(dialog().querySelectorAll('.pdel-rows > div'), (row) => [
      row.querySelector('dt')?.textContent ?? '',
      row.querySelector('dd')?.textContent ?? '',
    ]),
  );
}

test('DP-1: the preview says what goes and that the records leave the database after a pre-purge backup', async () => {
  renderDialog();
  expect(dialog()).toHaveAccessibleName('Delete Work Order permanently');
  await within(dialog()).findByText('What is deleted');

  expect(dialog().textContent).toContain(
    'This removes Work Order 007201 and all its data from the database, as if it had never been entered. The server takes a pre-purge backup first.',
  );
  expect(within(dialog()).getByText('Work Order 007201')).toBeInTheDocument();
  expect(
    within(dialog()).getByText('3 demand lines: A-100, B-200, E-500'),
  ).toBeInTheDocument();
  expect(rowsShown()).toEqual({
    'Work Orders': '1',
    'Demand lines': '3',
    Allocations: '1',
    'Quantity Flows': '2',
    'Lineage links': '1',
    Movements: '12',
    'Assigned Routes': '2',
    'Assigned Route steps': '6',
    'Part Number details': '0',
    'Audit rows': '4',
  });
  const kept = dialog().querySelector('.pdel-kept') as HTMLElement;
  expect(within(kept).getByText('Audit rows kept: 1')).toBeInTheDocument();
  expect(kept.textContent).toContain(
    `WorkOrderDemand 5 · UPDATED · ${formatInstant(KEPT_ROW.occurred_at)}`,
  );
  expect(kept.textContent).toContain('Kept: also concerns other records');

  // The reason and the Work Order Number typed again; nothing is sent
  // until both are entered.
  expect(reasonField()).toHaveAccessibleName('Reason (required)');
  expect(confirmationField()).toHaveAccessibleName(
    'Type 007201 (Work Order Number) to confirm',
  );
  expect(requestButton()).toBeDisabled();
  fireEvent.change(reasonField(), { target: { value: 'Test data' } });
  expect(requestButton()).toBeDisabled();
  fireEvent.change(confirmationField(), { target: { value: '   ' } });
  expect(requestButton()).toBeDisabled();
  fireEvent.change(confirmationField(), { target: { value: '007201' } });
  expect(requestButton()).toBeEnabled();
  expect(writes).toEqual([]);
});

test('DP-2: a refused deletion lists every refusal as the server words it and offers no request', async () => {
  refusals = [
    {
      code: 'on_hot_list',
      message: 'Demand line 102 of Work Order 007201 is on the Hot list.',
    },
    {
      code: 'lineage_link',
      message: 'Flow 4 is linked to flow 9 of Work Order 007300, which stays.',
    },
  ];
  const { onClose } = renderDialog();
  const refused = await screen.findByRole('region', { name: 'Refusals' });
  expect(within(refused).getByText('This deletion is refused')).toBeVisible();
  expect(
    within(refused)
      .getAllByRole('listitem')
      .map((item) => item.textContent),
  ).toEqual(refusals.map((refusal) => refusal.message));
  expect(screen.queryByText('What is deleted')).toBeNull();
  expect(within(dialog()).queryByLabelText(/^Reason/)).toBeNull();
  expect(
    within(dialog()).queryByRole('button', { name: 'Request deletion' }),
  ).toBeNull();
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Close (Esc)' }),
  );
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('DP-3: another open request blocks a new one until it ends; Check again reads the preview again', async () => {
  requests = [
    requestWire(4, { work_order_numbers: ['009999'], part_numbers: [] }),
  ];
  renderDialog();
  const open = await screen.findByRole('region', {
    name: 'Open deletion request',
  });
  expect(open.textContent).toContain('Deletion request 4 is still open');
  expect(open.textContent).toContain('Waiting for the server');
  expect(open.textContent).toContain(
    `Work Order 009999 · requested ${formatInstant(REQUESTED_AT)} by Sam Super.`,
  );
  expect(open.textContent).toContain(
    'Nothing can be requested until it finishes or is cancelled.',
  );
  expect(within(dialog()).queryByLabelText(/^Reason/)).toBeNull();

  requests[0].state = 'CANCELLED';
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Check again' }),
  );
  await within(dialog()).findByLabelText(/^Reason/);
  expect(previewReads).toBe(2);
  expect(
    screen.queryByRole('region', { name: 'Open deletion request' }),
  ).toBeNull();
});

test('DP-4: a request waits for the server, then shows Deleting… and Deleted, read again every few seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { onClose, onDeleted } = renderDialog();
  await previewLoaded();
  await fillAndRequest('  Test data entered by mistake ', '007201');

  const state = await screen.findByRole('status', {
    name: 'Deletion request 1',
  });
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/permanent-deletion/requests',
      body: {
        work_order_numbers: ['007201'],
        reason: '  Test data entered by mistake ',
        confirmation: ['007201'],
      },
    },
  ]);
  expect(within(state).getByText('Waiting for the server')).toBeVisible();
  expect(state.textContent).toContain(
    `Requested ${formatInstant(REQUESTED_AT)} by Sam Super. The server's history archive task carries it out, normally within 5 minutes.`,
  );
  expect(within(dialog()).queryByLabelText(/^Reason/)).toBeNull();
  expect(
    within(dialog()).getByRole('button', { name: 'Cancel request' }),
  ).toBeEnabled();

  // The host job starts it.
  Object.assign(requests[0], {
    state: 'RUNNING',
    started_at: STARTED_AT,
    backup_label: 'deletion-1-20261118T100500Z',
  });
  await poll();
  await waitFor(() =>
    expect(requestState(1).textContent).toContain('Deleting…'),
  );
  expect(requestState(1).textContent).toContain(
    `Started ${formatInstant(STARTED_AT)}.`,
  );
  expect(
    within(dialog()).queryByRole('button', { name: 'Cancel request' }),
  ).toBeNull();

  // … and deletes it after the pre-purge backup.
  Object.assign(requests[0], {
    state: 'COMPLETED',
    pre_purge_backup: { name: BACKUP, dump_started_at: '2026-11-18T10:05:01Z' },
    finished_at: FINISHED_AT,
  });
  await poll();
  await waitFor(() => expect(requestState(1).textContent).toContain('Deleted'));
  expect(requestState(1).textContent).toContain(
    'Work Order 007201 no longer exists.',
  );
  expect(requestState(1).textContent).toContain(BACKUP);
  const reads = listReads;
  await poll();
  await poll();
  expect(listReads).toBe(reads);

  // Every close now hands back: the Work Order no longer exists.
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  expect(onDeleted).toHaveBeenCalledTimes(1);
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Back to Work Orders' }),
  );
  expect(onDeleted).toHaveBeenCalledTimes(2);
  expect(onDeleted.mock.calls[0][0]).toMatchObject({
    id: 1,
    state: 'COMPLETED',
  });
  expect(onClose).not.toHaveBeenCalled();
});

test('DP-5: a failed request shows the reason as the server gives it and may be requested again', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data', '007201');
  await screen.findByRole('status', { name: 'Deletion request 1' });

  Object.assign(requests[0], {
    state: 'FAILED',
    started_at: STARTED_AT,
    backup_label: 'deletion-1-20261118T100500Z',
    finished_at: FINISHED_AT,
    failure_reason: CHANGED,
  });
  await poll();
  await waitFor(() =>
    expect(requestState(1).textContent).toContain(
      'Failed — nothing was deleted',
    ),
  );
  expect(within(requestState(1)).getByText(CHANGED)).toBeVisible();
  expect(requestState(1).textContent).toContain(
    'You may request the deletion again.',
  );
  const reads = listReads;
  await poll();
  expect(listReads).toBe(reads);

  // Preview again: the form returns, the reason kept and the
  // confirmation to be typed again.
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Preview again' }),
  );
  await within(dialog()).findByLabelText(/^Reason/);
  expect(previewReads).toBe(2);
  expect(reasonField()).toHaveValue('Test data');
  expect(confirmationField()).toHaveValue('');
  expect(
    screen.queryByRole('status', { name: 'Deletion request 1' }),
  ).toBeNull();
});

test('DP-6: Cancel request cancels a waiting request; a request the server works on is not cancelled', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data', '007201');
  await screen.findByRole('status', { name: 'Deletion request 1' });

  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel request' }),
  );
  await waitFor(() =>
    expect(requestState(1).textContent).toContain('Cancelled'),
  );
  expect(requestState(1).textContent).toContain(
    'Cancelled by Sam Super. Nothing was deleted.',
  );
  expect(writes.at(-1)).toEqual({
    method: 'POST',
    url: '/api/permanent-deletion/requests/1/cancel',
    body: undefined,
  });
  const reads = listReads;
  await poll();
  expect(listReads).toBe(reads);
  cleanup();

  // A second request: the job starts it before the cancel arrives.
  renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data again', '007201');
  await screen.findByRole('status', { name: 'Deletion request 2' });
  Object.assign(requests[0], { state: 'RUNNING', started_at: STARTED_AT });
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel request' }),
  );
  expect(await within(dialog()).findByRole('alert')).toHaveTextContent(
    'Deletion request 2 is being carried out on the server now and cannot be cancelled. Wait until it finishes.',
  );
  await poll();
  await waitFor(() =>
    expect(requestState(2).textContent).toContain('Deleting…'),
  );
});

test('DP-7: the 422 texts of the confirmation and the reason are shown exactly, the entries kept', async () => {
  renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data', '7201');
  expect(await within(dialog()).findByRole('alert')).toHaveTextContent(
    E_WO_CONFIRMATION,
  );
  expect(reasonField()).toHaveValue('Test data');
  expect(confirmationField()).toHaveValue('7201');

  // The confirmation is sent as typed: a Work Order Number is matched
  // exactly by the server.
  fireEvent.change(confirmationField(), { target: { value: '007201 ' } });
  fireEvent.click(requestButton());
  await waitFor(() =>
    expect(writes.at(-1)?.body).toMatchObject({ confirmation: ['007201 '] }),
  );
  expect(within(dialog()).getByRole('alert')).toHaveTextContent(
    E_WO_CONFIRMATION,
  );

  fireEvent.change(confirmationField(), { target: { value: '007201' } });
  fireEvent.change(reasonField(), { target: { value: 'x'.repeat(501) } });
  fireEvent.click(requestButton());
  await waitFor(() =>
    expect(within(dialog()).getByRole('alert')).toHaveTextContent(E_REASON),
  );
  expect(requests).toEqual([]);
});

test('DP-8: the 409 texts — a refused plan with its refusals, another open request — are shown exactly', async () => {
  renderDialog();
  await previewLoaded();
  // The plan refuses by the time the request arrives.
  refusals = [
    {
      code: 'part_number_used_by_work_order',
      message:
        "Part Number 'A-100' is used by demand line 7 of Work Order '007300', which is not being deleted.",
    },
  ];
  await fillAndRequest('Test data', '007201');
  const alert = await within(dialog()).findByRole('alert');
  expect(alert.firstElementChild?.textContent).toBe(E_REFUSED);
  expect(
    within(alert)
      .getAllByRole('listitem')
      .map((item) => item.textContent),
  ).toEqual([refusals[0].message]);
  expect(requestButton()).toBeDisabled();
  cleanup();

  // Another request was made after the preview.
  refusals = [];
  renderDialog();
  await previewLoaded();
  requests = [
    requestWire(2, { work_order_numbers: ['009999'], part_numbers: [] }),
  ];
  await fillAndRequest('Test data', '007201');
  expect(await within(dialog()).findByRole('alert')).toHaveTextContent(
    openText(2, 'APPROVED'),
  );
  expect(reasonField()).toHaveValue('Test data');
  expect(requestButton()).toBeEnabled();
});

test('DP-9: the open request of exactly this record is followed: its state, Cancel and the deletion', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  requests = [
    requestWire(
      3,
      { work_order_numbers: ['007201'], part_numbers: [] },
      { requested_by: null, requested_by_operator: 'ops-anna' },
    ),
  ];
  const { onDeleted } = renderDialog();
  const state = await screen.findByRole('status', {
    name: 'Deletion request 3',
  });
  expect(state.textContent).toContain('Waiting for the server');
  expect(state.textContent).toContain(
    `Requested ${formatInstant(REQUESTED_AT)} by ops-anna (operator).`,
  );
  expect(within(dialog()).queryByLabelText(/^Reason/)).toBeNull();
  expect(
    screen.queryByRole('region', { name: 'Open deletion request' }),
  ).toBeNull();
  expect(
    within(dialog()).getByRole('button', { name: 'Cancel request' }),
  ).toBeEnabled();

  Object.assign(requests[0], {
    state: 'COMPLETED',
    started_at: STARTED_AT,
    pre_purge_backup: { name: BACKUP, dump_started_at: STARTED_AT },
    finished_at: FINISHED_AT,
  });
  await poll();
  fireEvent.click(
    await within(dialog()).findByRole('button', {
      name: 'Back to Work Orders',
    }),
  );
  expect(onDeleted).toHaveBeenCalledTimes(1);
});

test('DP-10: a request whose answer was lost reads the preview again, which shows the recorded request', async () => {
  failures = {
    'POST requests': { status: 502, body: { detail: 'Bad gateway' } },
  };
  recordBeforeFailing = true;
  renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data', '007201');

  expect(
    await screen.findByText(
      'The server did not answer, so the deletion may or may not have been requested. What would be deleted is read again above, with the request if there is one.',
    ),
  ).toBeVisible();
  const state = await screen.findByRole('status', {
    name: 'Deletion request 1',
  });
  expect(state.textContent).toContain('Waiting for the server');
  expect(previewReads).toBe(2);
  expect(requests).toHaveLength(1);
});

test('DP-11: a Part Number is typed again and canonicalized by the server; its deletion hands back to the Part Numbers', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { onDeleted } = renderDialog(PART_NUMBER);
  expect(dialog()).toHaveAccessibleName('Delete Part Number permanently');
  await within(dialog()).findByText('What is deleted');
  expect(dialog().textContent).toContain(
    'This removes Part Number PN-A with all its data, including its saved details and its production history, from the database, as if it had never been entered. The server takes a pre-purge backup first.',
  );
  expect(within(dialog()).getByText('Part Number PN-A')).toBeInTheDocument();
  expect(within(dialog()).getByText('With its saved details')).toBeVisible();
  expect(
    within(dialog()).getByText('Internal Work Order · 1 demand line: PN-A'),
  ).toBeVisible();
  expect(confirmationField()).toHaveAccessibleName(
    'Type PN-A (Part Number) to confirm',
  );

  await fillAndRequest('Test part', 'pn-x');
  expect(await within(dialog()).findByRole('alert')).toHaveTextContent(
    E_PN_CONFIRMATION,
  );
  fireEvent.change(confirmationField(), { target: { value: ' pn-a ' } });
  fireEvent.click(requestButton());
  await screen.findByRole('status', { name: 'Deletion request 1' });
  expect(writes.at(-1)).toEqual({
    method: 'POST',
    url: '/api/permanent-deletion/requests',
    body: {
      part_number: 'PN-A',
      reason: 'Test part',
      confirmation: [' pn-a '],
    },
  });

  Object.assign(requests[0], {
    state: 'COMPLETED',
    started_at: STARTED_AT,
    pre_purge_backup: { name: BACKUP, dump_started_at: STARTED_AT },
    finished_at: FINISHED_AT,
  });
  await poll();
  await waitFor(() =>
    expect(requestState(1).textContent).toContain(
      'Part Number PN-A and all its data no longer exist.',
    ),
  );
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Back to Part Numbers' }),
  );
  expect(onDeleted).toHaveBeenCalledTimes(1);
});

test('DP-12: entered input asks before it is discarded; offline blocks the request and the cancellation; a failed preview retries', async () => {
  const { onClose } = renderDialog();
  await previewLoaded();
  // Unchanged: closes at once.
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);

  fireEvent.change(reasonField(), { target: { value: 'Test data' } });
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  const ask = screen.getByRole('dialog', {
    name: 'Discard this deletion request?',
  });
  fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
  expect(reasonField()).toHaveValue('Test data');
  expect(onClose).toHaveBeenCalledTimes(1);
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Discard this deletion request?' }),
    ).getByRole('button', { name: 'Discard input' }),
  );
  expect(onClose).toHaveBeenCalledTimes(2);
  cleanup();

  // Offline: reading works, requesting and cancelling do not.
  renderDialog(WORK_ORDER, true);
  await previewLoaded();
  fireEvent.change(reasonField(), { target: { value: 'Test data' } });
  fireEvent.change(confirmationField(), { target: { value: '007201' } });
  expect(requestButton()).toBeDisabled();
  cleanup();
  requests = [
    requestWire(5, { work_order_numbers: ['007201'], part_numbers: [] }),
  ];
  renderDialog(WORK_ORDER, true);
  await screen.findByRole('status', { name: 'Deletion request 5' });
  expect(
    within(dialog()).getByRole('button', { name: 'Cancel request' }),
  ).toBeDisabled();
  cleanup();

  // A preview that cannot be read: its message and Retry.
  requests = [];
  failures = {
    'GET preview': {
      status: 403,
      body: {
        detail:
          'You need the Delete Work Orders and Part Numbers permanently permission.',
      },
    },
  };
  renderDialog();
  expect(
    await within(dialog()).findByText(
      'What would be deleted could not be loaded.',
    ),
  ).toBeVisible();
  expect(dialog().textContent).toContain(
    'You need the Delete Work Orders and Part Numbers permanently permission.',
  );
  failures = {};
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Retry' }));
  await within(dialog()).findByText('What is deleted');
});

test('DP-13: a read of the state refused for the sign-in pauses the reading until the sign-in is usable again; other failures keep reading', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  requests = [
    requestWire(6, { work_order_numbers: ['007201'], part_numbers: [] }),
  ];
  const props = {
    record: WORK_ORDER,
    writeBlocked: false,
    onClose: vi.fn(),
    onDeleted: vi.fn(),
  };
  const view = render(
    <RetryFailedLoadsContext.Provider value={0}>
      <DeletePermanentlyDialog {...props} />
    </RetryFailedLoadsContext.Provider>,
  );
  await screen.findByRole('status', { name: 'Deletion request 6' });

  // A failure that does not last: the reading goes on.
  failures = {
    'GET requests': { status: 503, body: { detail: 'Service unavailable.' } },
  };
  await poll();
  await waitFor(() =>
    expect(requestState(6).textContent).toContain(
      "The request's state could not be read just now: Service unavailable. PartFlow tries again every few seconds.",
    ),
  );
  const afterOutage = listReads;
  await poll();
  expect(listReads).toBe(afterOutage + 1);

  // The sign-in ended: the reading pauses.
  failures = {
    'GET requests': {
      status: 401,
      body: { detail: 'Sign in to continue.', authentication_required: true },
    },
  };
  await poll();
  await waitFor(() =>
    expect(requestState(6).textContent).toContain(
      "The request's state could not be read: Sign in to continue. It is read again once you are signed in again.",
    ),
  );
  const paused = listReads;
  await poll();
  await poll();
  expect(listReads).toBe(paused);

  // The same user signed in again: the reading resumes and finds the
  // request deleted.
  failures = {};
  Object.assign(requests[0], {
    state: 'COMPLETED',
    started_at: STARTED_AT,
    pre_purge_backup: { name: BACKUP, dump_started_at: STARTED_AT },
    finished_at: FINISHED_AT,
  });
  view.rerender(
    <RetryFailedLoadsContext.Provider value={1}>
      <DeletePermanentlyDialog {...props} />
    </RetryFailedLoadsContext.Provider>,
  );
  await poll();
  await waitFor(() =>
    expect(requestState(6).textContent).toContain(
      'Work Order 007201 no longer exists.',
    ),
  );
  expect(requestState(6).textContent).not.toContain('could not be read');
});

test('DP-14: a request cancelled elsewhere is read as Cancelled, the reading ends, and the deletion may be requested again', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { onDeleted } = renderDialog();
  await previewLoaded();
  await fillAndRequest('Test data', '007201');
  await screen.findByRole('status', { name: 'Deletion request 1' });

  // Another holder of the key cancels it in Administration.
  Object.assign(requests[0], {
    state: 'CANCELLED',
    finished_at: FINISHED_AT,
    cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
  });
  await poll();
  await waitFor(() =>
    expect(requestState(1).textContent).toContain(
      'Cancelled by Ada Admin. Nothing was deleted.',
    ),
  );
  expect(
    within(dialog()).queryByRole('button', { name: 'Cancel request' }),
  ).toBeNull();
  const reads = listReads;
  await poll();
  expect(listReads).toBe(reads);

  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Preview again' }),
  );
  await within(dialog()).findByLabelText(/^Reason/);
  expect(reasonField()).toHaveValue('Test data');
  // The kept reason asks before it is discarded; nothing was deleted.
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Discard this deletion request?' }),
  ).toBeInTheDocument();
  expect(onDeleted).not.toHaveBeenCalled();
});
