import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Work Orders regression tests (Phase 4): the view runs against the
// REAL /api/work-orders surface — these tests exercise it against an
// in-memory fake of the backend API with the same route surface and
// semantics. Covered: the Work Order Details modal dialog over the
// always-mounted list (no URL change), the New Work Order modal
// workflow (optional WO Number — a blank number saves a NULL number
// rendered as `—` — and optional due dates, missing-information Save
// Demand confirmation, duplicate numbers resolved on the server and
// opened instead of duplicated), the multi-step Add Part dialog with
// real PN lookup and create-on-first-use, PN-carrying barcodes,
// one-transaction saves that keep the draft on failure, the explicit
// §11.4 release flow (FLOATING / PLANNED, active-quantity
// confirmation, device_event_id idempotency), server-enforced demand
// removal, offline write blocking, and unsaved-change protection.

interface FakeDemand {
  id: number;
  work_order_id: number;
  part_number: string;
  request_type: 'NEW' | 'MODIFY';
  requested_quantity: number;
  allocated_quantity: number;
  due_date: string | null;
  priority_rank: number | null;
  job_numbers: string[];
  requester: string | null;
  reason: string | null;
  notes: string | null;
}

interface FakeWorkOrder {
  id: number;
  work_order_number: string | null;
  received_date: string;
  due_date: string | null;
  status: string;
  demands: FakeDemand[];
}

interface ReleaseCommit {
  body: Record<string, unknown>;
  wire: Record<string, unknown>;
}

interface FakeState {
  workOrders: FakeWorkOrder[];
  partNumbers: string[];
  /** Released quantity per demand id (server knowledge — derived from
   * Movement history on the real backend). A demand may be released in
   * several parts, so this is a running total, never a flag. */
  releasedQuantities: Map<number, number>;
  /** PN → existing ACTIVE distribution (confirmation payload). */
  activeDistribution: Record<
    string,
    {
      quantity_flow_id: number;
      quantity: number;
      route_mode: string;
      current_area_id: number;
      current_area_name: string;
    }[]
  >;
  committedReleases: Map<string, ReleaseCommit>;
  /** Every release body the client sent, in order — the idempotency
   * key of each attempt is observable from here. */
  releaseAttempts: Record<string, unknown>[];
  /** Reject the next release with a server error, committing nothing. */
  failNextRelease: boolean;
  nextWorkOrderId: number;
  nextDemandId: number;
  nextFlowId: number;
  nextMovementId: number;
  nextSnapshotId: number;
  healthDown: boolean;
  failNextWorkOrderWrite: boolean;
  /** Commit the release but drop the response (transport loss). */
  dropNextReleaseResponse: boolean;
  failNextList: boolean;
  calls: string[];
}

const T0 = '2026-08-01T00:00:00.000Z';

function demand(
  id: number,
  work_order_id: number,
  part_number: string,
  requested_quantity: number,
  extra?: Partial<FakeDemand>,
): FakeDemand {
  return {
    id,
    work_order_id,
    part_number,
    request_type: 'NEW',
    requested_quantity,
    allocated_quantity: 0,
    due_date: null,
    priority_rank: null,
    job_numbers: [],
    requester: null,
    reason: null,
    notes: null,
    ...extra,
  };
}

function seedState(): FakeState {
  return {
    workOrders: [
      {
        id: 1,
        work_order_number: '007201',
        received_date: '2026-08-01',
        due_date: '2026-09-10',
        status: 'OPEN',
        demands: [
          demand(101, 1, 'A-100', 25, {
            due_date: '2026-09-10',
            job_numbers: ['18112'],
          }),
          demand(102, 1, 'B-200', 10),
          demand(103, 1, 'E-500', 7, { due_date: '2026-09-10' }),
        ],
      },
      {
        id: 2,
        work_order_number: null,
        received_date: '2026-08-05',
        due_date: null,
        status: 'OPEN',
        demands: [demand(201, 2, 'C-300', 5)],
      },
      // Every demand of this Work Order has release evidence — the
      // derived read status is RELEASED.
      {
        id: 3,
        work_order_number: '007300',
        received_date: '2026-07-25',
        due_date: '2026-08-30',
        status: 'OPEN',
        demands: [demand(301, 3, 'D-400', 8, { due_date: '2026-08-30' })],
      },
    ],
    partNumbers: ['309-127', 'A-100', 'B-200', 'C-300', 'D-400', 'E-500'],
    // Demands 102 (10 pcs) and 301 (8 pcs) are fully released.
    releasedQuantities: new Map([
      [102, 10],
      [301, 8],
    ]),
    activeDistribution: {
      'A-100': [
        {
          quantity_flow_id: 71,
          quantity: 40,
          route_mode: 'FLOATING',
          current_area_id: 2,
          current_area_name: 'Lathe',
        },
      ],
    },
    committedReleases: new Map(),
    releaseAttempts: [],
    failNextRelease: false,
    nextWorkOrderId: 100,
    nextDemandId: 1000,
    nextFlowId: 500,
    nextMovementId: 9000,
    nextSnapshotId: 300,
    healthDown: false,
    failNextWorkOrderWrite: false,
    dropNextReleaseResponse: false,
    failNextList: false,
    calls: [],
  };
}

let state: FakeState;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function detailResponse(message: string, status: number): Response {
  return json({ detail: message }, status);
}

function releasedOf(demandId: number): number {
  return state.releasedQuantities.get(demandId) ?? 0;
}

function remainingOf(demand: FakeDemand): number {
  return Math.max(demand.requested_quantity - releasedOf(demand.id), 0);
}

/** Server-derived read status: RELEASED once EVERY current demand is
 * fully released; the stored column stays OPEN. A partly released
 * line keeps the Work Order Open — its remainder is still releasable. */
function derivedStatus(wo: FakeWorkOrder): string {
  return wo.demands.length > 0 && wo.demands.every((d) => remainingOf(d) === 0)
    ? 'RELEASED'
    : wo.status;
}

function summaryWire(wo: FakeWorkOrder) {
  return {
    id: wo.id,
    work_order_number: wo.work_order_number,
    received_date: wo.received_date,
    due_date: wo.due_date,
    status: derivedStatus(wo),
    demand_line_count: wo.demands.length,
    part_numbers: wo.demands.map((d) => d.part_number),
  };
}

function detailWire(wo: FakeWorkOrder) {
  return {
    id: wo.id,
    work_order_number: wo.work_order_number,
    received_date: wo.received_date,
    due_date: wo.due_date,
    status: derivedStatus(wo),
    created_at: T0,
    updated_at: T0,
    demands: wo.demands.map((d) => ({
      ...d,
      // Server-derived release evidence — never a client-session flag.
      has_released_quantity: releasedOf(d.id) > 0,
      released_quantity: releasedOf(d.id),
      remaining_quantity: remainingOf(d),
    })),
  };
}

const AREAS = [
  {
    id: 1,
    department_id: 1,
    name: 'Material',
    barcode_value: 'PF:AREA:1',
    description: null,
    color: '#4f8cff',
    icon_url: null,
    is_terminal: false,
    is_active: true,
  },
  {
    id: 2,
    department_id: 1,
    name: 'Lathe',
    barcode_value: 'PF:AREA:2',
    description: null,
    color: '#f2a44a',
    icon_url: null,
    is_terminal: false,
    is_active: true,
  },
  {
    id: 3,
    department_id: 1,
    name: 'Stockroom',
    barcode_value: 'PF:AREA:3',
    description: null,
    color: '#7bd88f',
    icon_url: null,
    is_terminal: true,
    is_active: true,
  },
];

const OPERATIONS = [
  {
    id: 11,
    area_id: 1,
    code: 'RECV',
    name: 'Receiving',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
  {
    id: 12,
    area_id: 1,
    code: 'INSP',
    name: 'Inspection',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
  {
    id: 21,
    area_id: 2,
    code: 'TURN',
    name: 'Turning',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
];

const ROUTE_TEMPLATES = [
  {
    id: 1,
    name: 'Standard Flow',
    description: 'Material → Lathe',
    created_at: T0,
    updated_at: T0,
    steps: [
      { id: 1, sequence: 10, area_id: 1, operation_id: 11, instructions: null },
      {
        id: 2,
        sequence: 20,
        area_id: 2,
        operation_id: null,
        instructions: null,
      },
    ],
  },
];

function releaseWire(
  body: Record<string, unknown>,
  flowId: number,
  movementId: number,
  snapshotId: number | null,
) {
  return {
    quantity_flow_id: flowId,
    part_number: body.part_number,
    quantity: body.quantity,
    route_mode: body.route_mode,
    assigned_route_id: snapshotId,
    starting_area_id: body.starting_area_id,
    operation_id: body.operation_id,
    movement_id: movementId,
    device_event_id: body.device_event_id,
    occurred_at: '2026-08-19T12:00:00.000Z',
  };
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  state.calls.push(`${method} ${url}`);
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : {};

  if (url === '/api/health') {
    return state.healthDown
      ? detailResponse('Service unavailable.', 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/areas') return json(AREAS);
  if (url === '/api/operations') return json(OPERATIONS);
  if (url === '/api/route-templates') return json(ROUTE_TEMPLATES);
  if (url.startsWith('/api/part-numbers')) {
    const params = new URLSearchParams(url.split('?')[1] ?? '');
    const number = params.get('number');
    if (number !== null) {
      const canonical = number.trim().toUpperCase();
      return json(
        state.partNumbers
          .filter((pn) => pn === canonical)
          .map((pn) => ({
            part_number: pn,
            barcode_value: `PF:PN:${pn}`,
            created_at: T0,
            updated_at: T0,
          })),
      );
    }
    const search = (params.get('search') ?? '').toUpperCase();
    return json(
      state.partNumbers
        .filter((pn) => !search || pn.toUpperCase().includes(search))
        .sort()
        // The real backend bounds the listing in the query
        // (part_numbers.SEARCH_RESULT_LIMIT) — mirrored so the fake
        // cannot promise the client an unbounded catalog.
        .slice(0, 50)
        .map((pn) => ({
          part_number: pn,
          barcode_value: `PF:PN:${pn}`,
          created_at: T0,
          updated_at: T0,
        })),
    );
  }

  // ---- Release --------------------------------------------------------
  const releaseMatch =
    /^\/api\/work-orders\/(\d+)\/demands\/(\d+)\/release$/.exec(url);
  if (releaseMatch && method === 'POST') {
    const deviceEventId = String(body.device_event_id);
    state.releaseAttempts.push(body);
    const replay = state.committedReleases.get(deviceEventId);
    if (replay) return json(replay.wire, 200);
    if (state.failNextRelease) {
      state.failNextRelease = false;
      return detailResponse('Release rejected by the server.', 409);
    }
    const pn = String(body.part_number);
    const distribution = state.activeDistribution[pn];
    if (distribution && body.confirm_active_quantity !== true) {
      return json(
        {
          detail: `Part Number '${pn}' already has active production quantity. Review the existing distribution and confirm the intent to release a separate Quantity Flow — existing quantity is never merged.`,
          confirmation_required: true,
          existing_active_quantity: distribution,
        },
        409,
      );
    }
    const snapshotId =
      body.route_mode === 'PLANNED' ? state.nextSnapshotId++ : null;
    const wire = releaseWire(
      body,
      state.nextFlowId++,
      state.nextMovementId++,
      snapshotId,
    );
    const releasedDemandId = Number(releaseMatch[2]);
    state.committedReleases.set(deviceEventId, { body, wire });
    state.releasedQuantities.set(
      releasedDemandId,
      releasedOf(releasedDemandId) + Number(body.quantity),
    );
    if (state.dropNextReleaseResponse) {
      state.dropNextReleaseResponse = false;
      throw new TypeError('Failed to fetch');
    }
    return json(wire, 201);
  }

  // ---- Demand removal -------------------------------------------------
  const demandMatch = /^\/api\/work-orders\/(\d+)\/demands\/(\d+)$/.exec(url);
  if (demandMatch && method === 'DELETE') {
    const wo = state.workOrders.find((w) => w.id === Number(demandMatch[1]));
    if (!wo) return detailResponse('Work Order not found.', 404);
    const demandId = Number(demandMatch[2]);
    if (releasedOf(demandId) > 0) {
      return detailResponse(
        'Cannot remove: production quantity has already been released.',
        409,
      );
    }
    if (wo.demands.length <= 1) {
      return detailResponse(
        'Cannot remove the last demand line — a Work Order contains one or more demand records.',
        409,
      );
    }
    wo.demands = wo.demands.filter((d) => d.id !== demandId);
    return new Response(null, { status: 204 });
  }

  // ---- Work Orders ----------------------------------------------------
  if (url.startsWith('/api/work-orders?')) {
    const params = new URLSearchParams(url.split('?')[1]);
    const number = params.get('number');
    if (number !== null) {
      return json(
        state.workOrders
          .filter((w) => w.work_order_number === number)
          .map(summaryWire),
      );
    }
    return json(state.workOrders.map(summaryWire));
  }
  if (url === '/api/work-orders' && method === 'GET') {
    if (state.failNextList) {
      state.failNextList = false;
      return detailResponse('The database is unavailable.', 500);
    }
    return json(state.workOrders.map(summaryWire));
  }
  if (url === '/api/work-orders' && method === 'POST') {
    if (state.failNextWorkOrderWrite) {
      state.failNextWorkOrderWrite = false;
      return detailResponse('The save failed on the server.', 500);
    }
    const number = body.work_order_number as string | null;
    if (
      number !== null &&
      state.workOrders.some((w) => w.work_order_number === number)
    ) {
      return detailResponse(
        `Work Order Number '${number}' already exists.`,
        409,
      );
    }
    const wo: FakeWorkOrder = {
      id: state.nextWorkOrderId++,
      work_order_number: number,
      received_date: String(body.received_date),
      due_date: (body.due_date as string | null) ?? null,
      status: 'OPEN',
      demands: [],
    };
    for (const line of body.lines as Record<string, unknown>[]) {
      const pn = String(line.part_number).trim().toUpperCase();
      if (!state.partNumbers.includes(pn)) state.partNumbers.push(pn);
      wo.demands.push(
        demand(
          state.nextDemandId++,
          wo.id,
          pn,
          Number(line.requested_quantity),
          {
            request_type: (line.request_type as 'NEW' | 'MODIFY') ?? 'NEW',
            due_date: (line.due_date as string | null) ?? null,
            job_numbers: (line.job_numbers as string[]) ?? [],
            notes: (line.notes as string | null) ?? null,
          },
        ),
      );
    }
    state.workOrders.unshift(wo);
    return json(detailWire(wo), 201);
  }
  const workOrderMatch = /^\/api\/work-orders\/(\d+)$/.exec(url);
  if (workOrderMatch) {
    const wo = state.workOrders.find((w) => w.id === Number(workOrderMatch[1]));
    if (!wo) return detailResponse('Work Order not found.', 404);
    if (method === 'GET') return json(detailWire(wo));
    if (method === 'PATCH') {
      if (state.failNextWorkOrderWrite) {
        state.failNextWorkOrderWrite = false;
        return detailResponse('The save failed on the server.', 500);
      }
      if ('work_order_number' in body) {
        // Verbatim semantics: the entered string is stored exactly;
        // uniqueness spans non-null numbers.
        const nextNumber = body.work_order_number as string | null;
        if (
          nextNumber !== null &&
          nextNumber !== wo.work_order_number &&
          state.workOrders.some((w) => w.work_order_number === nextNumber)
        ) {
          return detailResponse(
            `Work Order '${nextNumber}' already exists. Open the existing Work Order instead of creating a duplicate.`,
            409,
          );
        }
        wo.work_order_number = nextNumber;
      }
      if ('due_date' in body) wo.due_date = body.due_date as string | null;
      for (const edit of (body.line_edits ?? []) as Record<string, unknown>[]) {
        const target = wo.demands.find((d) => d.id === Number(edit.id));
        if (!target) return detailResponse('Demand not found.', 404);
        if ('request_type' in edit) {
          if (edit.request_type === null) {
            return detailResponse('request_type must not be null.', 422);
          }
          target.request_type = edit.request_type as 'NEW' | 'MODIFY';
        }
        if ('requested_quantity' in edit) {
          target.requested_quantity = Number(edit.requested_quantity);
        }
        if ('due_date' in edit)
          target.due_date = edit.due_date as string | null;
        if ('job_numbers' in edit) {
          target.job_numbers = edit.job_numbers as string[];
        }
        if ('notes' in edit) target.notes = edit.notes as string | null;
      }
      for (const line of (body.new_lines ?? []) as Record<string, unknown>[]) {
        const pn = String(line.part_number).trim().toUpperCase();
        if (!state.partNumbers.includes(pn)) state.partNumbers.push(pn);
        wo.demands.push(
          demand(
            state.nextDemandId++,
            wo.id,
            pn,
            Number(line.requested_quantity),
            {
              request_type: (line.request_type as 'NEW' | 'MODIFY') ?? 'NEW',
              due_date: (line.due_date as string | null) ?? null,
              job_numbers: (line.job_numbers as string[]) ?? [],
              notes: (line.notes as string | null) ?? null,
            },
          ),
        );
      }
      return json(detailWire(wo));
    }
  }

  return detailResponse(`Unhandled fake route: ${method} ${url}`, 500);
}

beforeEach(() => {
  state = seedState();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderWorkOrders() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
  await screen.findByText('007201');
}

function openNewWorkOrderDialog() {
  const button = screen.getByRole('button', { name: '＋ New Work Order' });
  // A real click focuses the button first; jsdom needs this explicitly
  // so focus restoration on close can be asserted.
  button.focus();
  fireEvent.click(button);
  return screen.getByRole('dialog', { name: 'New Work Order' });
}

function scanBarcode(barcode: string) {
  const scan = screen.getByLabelText('Scan PN barcode');
  fireEvent.change(scan, { target: { value: barcode } });
  fireEvent.keyDown(scan, { key: 'Enter' });
  return scan;
}

function workOrderRow(workOrderNumber: string) {
  return screen.getByRole('button', { name: new RegExp(workOrderNumber) });
}

async function openWorkOrderDetail(workOrderNumber: string, waitForPn: string) {
  const row = workOrderRow(workOrderNumber);
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  // The dialog loads the real Work Order — wait for its demand lines.
  await within(dialog).findByText(waitForPn);
  return dialog;
}

function releaseCalls(): string[] {
  return state.calls.filter((call) => call.includes('/release'));
}

/* ============ Work Order Details modal ============ */

test('clicking a Work Order row opens the Work Order Details dialog over the list', async () => {
  await renderWorkOrders();

  const dialog = await openWorkOrderDetail('007201', 'A-100');

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(dialog).toHaveTextContent('007201');
  expect(window.location.pathname).toBe('/management/work-orders');
  // The list stays mounted behind the overlay.
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('a clean Work Order Details dialog closes directly and focus returns to its row', async () => {
  await renderWorkOrders();
  const row = workOrderRow('007201');
  await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(row);
});

test('closing a dirty Work Order Details dialog requires explicit discard confirmation', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  fireEvent.keyDown(dialog, { key: 'Escape' });

  const confirm = screen.getByRole('dialog', {
    name: 'Discard unsaved demand changes?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for A-100')).toHaveValue('30');

  fireEvent.keyDown(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
    { key: 'Escape' },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('Escape and backdrop on the Add Part child dialog close only the child', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  const addPart = await screen.findByRole('dialog', {
    name: /Add Part — step 1/,
  });

  fireEvent.keyDown(addPart, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('the detail dialog shows a real error state with Retry when the load fails', async () => {
  await renderWorkOrders();
  state.failNextList = false;
  // Fail the detail GET once: temporarily remove the Work Order.
  const [wo] = state.workOrders;
  state.workOrders = state.workOrders.filter((w) => w.id !== wo.id);
  const row = workOrderRow('007201');
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('The Work Order could not be loaded.');

  state.workOrders = [wo, ...state.workOrders];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
  await within(dialog).findByText('A-100');
});

/* ============ New Work Order dialog ============ */

test('＋ New Work Order opens a dialog over the Work Order list without changing the URL', async () => {
  await renderWorkOrders();

  const dialog = openNewWorkOrderDialog();

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
  expect(screen.getByText('007201')).toBeInTheDocument();
});

test('Cancel (Esc) closes a clean dialog and focus returns to ＋ New Work Order', async () => {
  await renderWorkOrders();
  const newWorkOrderButton = screen.getByRole('button', {
    name: '＋ New Work Order',
  });
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(newWorkOrderButton);
});

test('Escape and backdrop close a clean dialog', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const dialog2 = openNewWorkOrderDialog();
  fireEvent.mouseDown(dialog2.parentElement!);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('closing a dirty New Work Order form requires confirmation and preserves entries', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Nothing is silently discarded: an explicit confirmation appears.
  expect(
    screen.getByRole('dialog', { name: 'Discard this New Work Order?' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/^WO Number/)).toHaveValue('007482');

  fireEvent.keyDown(screen.getByRole('dialog', { name: 'New Work Order' }), {
    key: 'Escape',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Discard entries' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a complete save flow (number + due entered) commits ONE POST and reloads from the server', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });

  // Barcode scanning remains available as a secondary method (the PN
  // resolution is a real server lookup, so the line arrives async).
  scanBarcode('PF:PN:78-04-0031');
  const qty = await screen.findByLabelText('Quantity for 78-04-0031');
  expect(document.activeElement).toBe(qty);
  fireEvent.change(qty, { target: { value: '5' } });
  fireEvent.keyDown(qty, { key: 'Enter' });
  expect(document.activeElement).toBe(screen.getByLabelText('Scan PN barcode'));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // The toast states the business rule; the dialog closed only after
  // the server committed, and the list shows the server's row.
  await screen.findByText(/007482 saved — business demand only/);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(await screen.findByText('007482')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/management/work-orders');

  const saved = state.workOrders.find((w) => w.work_order_number === '007482');
  expect(saved).toBeDefined();
  expect(saved!.due_date).toBe('2026-09-01');
  expect(saved!.demands).toHaveLength(1);
  expect(saved!.demands[0].part_number).toBe('78-04-0031');
  expect(saved!.demands[0].requested_quantity).toBe(5);
  // The line inherited the WO due date.
  expect(saved!.demands[0].due_date).toBe('2026-09-01');
  // Save demand touched only the intake surface — never the release
  // endpoint.
  expect(releaseCalls()).toEqual([]);
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(1);
});

test('a failed New Work Order save keeps the whole draft and every entered value', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007483' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:78-04-0031');
  const qty = await screen.findByLabelText('Quantity for 78-04-0031');
  fireEvent.change(qty, { target: { value: '5' } });

  state.failNextWorkOrderWrite = true;
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // No success is shown before the server commit; the draft is kept.
  await screen.findByText(/The save failed on the server/);
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/^WO Number/)).toHaveValue('007483');
  expect(screen.getByLabelText('Quantity for 78-04-0031')).toHaveValue('5');
  expect(state.workOrders.some((w) => w.work_order_number === '007483')).toBe(
    false,
  );

  // The retry with the intact draft succeeds.
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007483 saved — business demand only/);
  expect(state.workOrders.some((w) => w.work_order_number === '007483')).toBe(
    true,
  );
});

/* ============ Barcodes and PN identity ============ */

test('a non-PN barcode is rejected and adds nothing', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:MACHINE:L2');

  expect(
    screen.getByText(/Unknown barcode “PF:MACHINE:L2” — nothing added/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No demand lines yet — add the first Part/),
  ).toBeInTheDocument();
});

test('a PN barcode carries the PN itself; an unknown PN is created on first use', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  // Arbitrary non-empty suffix — no format, no opaque id mapping. The
  // PN has no master yet, so the line is marked as a new PN.
  scanBarcode('PF:PN:NEW-PLATE-9');

  expect(await screen.findByText('NEW-PLATE-9')).toBeInTheDocument();
  expect(
    screen.getByText(/NEW-PLATE-9 added — Request Type NEW/),
  ).toBeInTheDocument();

  // Case-insensitive identity: a re-scan in different casing focuses
  // the existing line instead of duplicating it.
  scanBarcode('PF:PN:new-plate-9');
  await waitFor(() =>
    expect(screen.getAllByLabelText('Quantity for NEW-PLATE-9')).toHaveLength(
      1,
    ),
  );
});

test('scanned lines reflect the real master lookup — existing PN reuse vs. create-on-first-use', async () => {
  await renderWorkOrders();
  // The Work Order Details table renders each line's barcode note.
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:309-127');
  expect(
    await within(dialog).findByText('existing PN · barcode PF:PN:309-127'),
  ).toBeInTheDocument();

  scanBarcode('PF:PN:NEW-PLATE-9');
  expect(
    await within(dialog).findByText('new PN — barcode PF:PN:NEW-PLATE-9'),
  ).toBeInTheDocument();
});

test('scanning a duplicate PN focuses the existing line instead of adding one', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:78-04-0031');
  await screen.findByLabelText('Quantity for 78-04-0031');
  scanBarcode('PF:PN:78-04-0031');

  const qtyFields = screen.getAllByLabelText('Quantity for 78-04-0031');
  expect(qtyFields).toHaveLength(1);
  await waitFor(() => expect(document.activeElement).toBe(qtyFields[0]));
});

test('search matches hyphenated PNs and internal Work Orders through the PN preview', async () => {
  await renderWorkOrders();

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'A-100' },
  });
  expect(screen.getByText('007201')).toBeInTheDocument();

  // An internal Work Order without an external number is found through
  // its PN preview and displays `—` as its number.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'C-300' },
  });
  expect(screen.queryByText('007201')).not.toBeInTheDocument();
  const internalRow = screen
    .getByText('internal Work Order — no external number yet')
    .closest('tr');
  expect(internalRow?.querySelector('.wo')?.textContent).toBe('—');
});

/* ============ Optional header + Save Demand confirmation ============ */

test('missing WO Number and due dates open a confirmation and save NULLs, never placeholders', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // Omissions are summarized and explicitly confirmed — they are never
  // validation errors and nothing is saved yet.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  expect(confirm).toHaveTextContent(/internal Work Order without an external/);
  expect(confirm).toHaveTextContent(/remains unscheduled/);
  expect(confirm).toHaveTextContent(/1.*demand line.*has.*no.*due date/s);
  expect(state.workOrders).toHaveLength(3);

  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));

  await screen.findByText(/WO — saved — business demand only/);
  const saved = state.workOrders[0];
  // Nullable fields persist NULL — no temporary number, no fake date.
  expect(saved.work_order_number).toBeNull();
  expect(saved.due_date).toBeNull();
  expect(saved.demands[0].due_date).toBeNull();
  // The internal Work Order renders `—` plus its label in the list.
  expect(
    screen.getAllByText('internal Work Order — no external number yet').length,
  ).toBeGreaterThanOrEqual(2);
});

test('an invalid quantity still blocks save and keeps entered values', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '0' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    await screen.findByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  expect(document.activeElement).toBe(qty);
  expect(qty).toHaveValue('0');
  // Nothing traveled: no create POST happened.
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(0);
});

test('an existing WO Number is opened instead of duplicated', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  // Without entered lines the existing Work Order opens directly.
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(dialog).toHaveTextContent('007201');
  expect(
    screen.getByText(/007201 already exists — opening the existing Work Order/),
  ).toBeInTheDocument();
  // Nothing was created.
  expect(state.workOrders).toHaveLength(3);
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  // With entered lines, opening the existing Work Order (discarding
  // them) is confirmed explicitly first.
  openNewWorkOrderDialog();
  scanBarcode('PF:PN:NEW-PLATE-9');
  await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const confirm = await screen.findByRole('dialog', {
    name: '007201 already exists',
  });
  expect(confirm).toHaveTextContent(/never duplicated/);
  fireEvent.click(
    screen.getByRole('button', { name: 'Open existing Work Order' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(state.workOrders).toHaveLength(3);
});

/* ============ Add Part flow ============ */

test('the Add Part flow steps through PN, quantity, due date and metadata', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  const dialog = screen.getByRole('dialog', { name: /Add Part — step 1/ });

  // Step 1: search and select an existing PN (a real server search).
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '309' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /309-127/ }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // Step 2: same keypad + physical-keyboard interaction as Scan Station
  // (a real focusable numeric input, focused on entry).
  fireEvent.keyDown(dialog, { key: '5' });
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();

  // Back preserves entered values.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Search PartNumber')).toHaveValue('309');
  fireEvent.click(await screen.findByRole('button', { name: /309-127/ }));
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 3: `No due date` is an explicit, valid choice.
  fireEvent.click(screen.getByRole('radio', { name: /No due date/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 4: optional metadata, then finish.
  fireEvent.change(screen.getByLabelText('Job Numbers'), {
    target: { value: '18777' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add Part' }));

  // The flow created an editable draft row — nothing saved yet.
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for 309-127')).toHaveValue('5');
  const lineDue = screen.getByLabelText('Due date for 309-127');
  expect(lineDue).toHaveValue('');
  expect(state.calls.filter((call) => call.startsWith('POST'))).toHaveLength(0);

  // The explicit `No due date` line never inherits a later WO due date…
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-15' },
  });
  expect(lineDue).toHaveValue('');
});

test('a one-character PN that already exists is found, not offered as new', async () => {
  // The Add Part minimum search length bounds the contains-SEARCH; it
  // is not a rule about what a Part Number may be. A short existing PN
  // must resolve exactly, or the operator would be told to create a PN
  // that is already in the master.
  state.partNumbers.push('X');
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'x' },
  });

  // Offered as the existing master, with its derived barcode...
  const match = await screen.findByRole('button', { name: /PF:PN:X/ });
  expect(match).toBeInTheDocument();
  // ...and never as a new PN.
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Type at least 2 characters/),
  ).not.toBeInTheDocument();

  fireEvent.click(match);
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
});

test('a one-character PN that does not exist is still creatable', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'q' },
  });

  const create = await screen.findByRole('button', {
    name: /Create new PN “Q”/,
  });
  expect(create).toBeInTheDocument();
});

test('Add Part canonicalizes manual entry like the backend — trim + uppercase, internal whitespace rejected', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  // Internal whitespace is invalid — never silently removed.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'PL 77' },
  });
  expect(
    await screen.findByText(/cannot contain spaces or other whitespace/),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Create new PN/ })).toBeNull();

  // Surrounding whitespace trims; the canonical PN is UPPERCASE.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '  pl-77  ' },
  });
  const create = await screen.findByRole('button', {
    name: '＋ Create new PN “PL-77”',
  });
  fireEvent.click(create);
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
  expect(screen.getByText('new Part Number')).toBeInTheDocument();
});

test('the Add Part flow rejects a PN already on the Work Order', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  scanBarcode('PF:PN:78-04-0031');
  await screen.findByLabelText('Quantity for 78-04-0031');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '78-04-0031' },
  });
  // Scope to the Add Part dialog: the scanned demand line behind it
  // exposes its own control whose accessible name carries the same PN.
  const addPartDialog = screen.getByRole('dialog', { name: /Add Part/ });
  fireEvent.click(
    await within(addPartDialog).findByRole('button', {
      name: '＋ Create new PN “78-04-0031”',
    }),
  );

  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByText(/78-04-0031 is already on this Work Order/),
  ).toBeInTheDocument();
  expect(screen.getAllByLabelText('Quantity for 78-04-0031')).toHaveLength(1);
});

/* ============ Dates ============ */

test('editable date fields are calendar inputs (type="date")', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  expect(screen.getByLabelText('Received date')).toHaveAttribute(
    'type',
    'date',
  );
  expect(screen.getByLabelText(/^WO due date/)).toHaveAttribute('type', 'date');

  scanBarcode('PF:PN:78-04-0031');
  expect(
    await screen.findByLabelText('Due date for 78-04-0031'),
  ).toHaveAttribute('type', 'date');
});

test('new lines inherit the WO due date; edited lines keep their own date', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:AAA-1');
  await screen.findByLabelText('Due date for AAA-1');
  scanBarcode('PF:PN:BBB-2');
  await screen.findByLabelText('Due date for BBB-2');

  expect(screen.getByLabelText('Due date for AAA-1')).toHaveValue('2026-09-01');

  // Edit one line's date — it becomes user-owned.
  fireEvent.change(screen.getByLabelText('Due date for BBB-2'), {
    target: { value: '2026-09-20' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-10-01' },
  });

  expect(screen.getByLabelText('Due date for AAA-1')).toHaveValue('2026-10-01');
  expect(screen.getByLabelText('Due date for BBB-2')).toHaveValue('2026-09-20');
});

test('a line whose due date is cleared is user-edited and stops inheriting', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:AAA-1');
  const lineDue = await screen.findByLabelText('Due date for AAA-1');
  fireEvent.change(lineDue, { target: { value: '' } });

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-10-01' },
  });
  expect(lineDue).toHaveValue('');
});

test('a Work Order without a due date displays cleanly in list and detail', async () => {
  await renderWorkOrders();

  // The internal seeded Work Order has no due date: the list shows `—`
  // for both the number and the due date.
  const internalRow = screen
    .getByText('internal Work Order — no external number yet')
    .closest('tr')!;
  expect(
    within(internalRow as HTMLElement).getAllByText('—').length,
  ).toBeGreaterThanOrEqual(2);

  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');
  expect(within(dialog).getByText('no due date')).toBeInTheDocument();
});

/* ============ OPEN Work Order editing ============ */

test('scanning a new PN on an OPEN Work Order adds a draft line and marks unsaved', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');

  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  expect(within(dialog).getByText('Draft (unsaved)')).toBeInTheDocument();
});

test('a duplicate PN on an OPEN Work Order focuses the existing line', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:A-100');

  expect(
    await screen.findByText(/A-100 is already on this Work Order/),
  ).toBeInTheDocument();
  const qty = within(dialog).getByLabelText('Quantity for A-100');
  await waitFor(() => expect(document.activeElement).toBe(qty));
});

test('an unsaved draft line is removed immediately without a confirmation dialog', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line EXTRA-77' }),
  );

  expect(screen.queryByRole('dialog', { name: /Remove/ })).toBeNull();
  expect(within(dialog).queryByText('EXTRA-77')).toBeNull();
  expect(
    screen.getByText('✕ Draft line removed — it had never been saved.'),
  ).toBeInTheDocument();
  // No server call happened for a draft-only removal.
  expect(state.calls.filter((call) => call.startsWith('DELETE'))).toHaveLength(
    0,
  );
});

test('removing a saved unreleased line requires confirmation and commits on the server', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line A-100' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Remove A-100 from 007201?',
  });
  expect(confirm).toHaveTextContent(/never deletes the PartNumber master/);

  fireEvent.click(within(confirm).getByRole('button', { name: 'Remove line' }));

  await screen.findByText('✕ A-100 removed from 007201.');
  expect(within(dialog).queryByText('A-100')).toBeNull();
  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([102, 103]);
  expect(
    state.calls.filter((call) =>
      call.startsWith('DELETE /api/work-orders/1/demands/101'),
    ),
  ).toHaveLength(1);
});

test('a release committed after the view loaded still blocks removal — server 409 with the explanation', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // The stale-view race: A-100 released elsewhere AFTER this dialog
  // loaded. The UI still offers removal, but the backend rule is the
  // authority — the 409 removes nothing and explains why.
  state.releasedQuantities.set(101, 25);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line A-100' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));

  expect(
    await screen.findByText(
      /Cannot remove: production quantity has already been released\./,
    ),
  ).toBeInTheDocument();
  // Nothing was removed.
  expect(within(dialog).getByText('A-100')).toBeInTheDocument();
  expect(state.workOrders[0].demands).toHaveLength(3);
});

test('the last demand line of a Work Order cannot be removed', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line C-300' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));

  expect(
    await screen.findByText(/Cannot remove the last demand line/),
  ).toBeInTheDocument();
  expect(state.workOrders[1].demands).toHaveLength(1);
});

test('Save demand PATCHes only the diff, refreshes from the server, and never releases', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  await screen.findByText(/007201 demand updated — business demand only/);
  // Server state took the edit; the other line traveled no edit.
  const wo = state.workOrders.find((w) => w.id === 1)!;
  expect(wo.demands.find((d) => d.id === 101)!.requested_quantity).toBe(30);
  const patchCalls = state.calls.filter(
    (call) => call === 'PATCH /api/work-orders/1',
  );
  expect(patchCalls).toHaveLength(1);
  // Saving demand never touches the release surface.
  expect(releaseCalls()).toEqual([]);
  // The dialog refreshed from server state and is clean again.
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
});

test('a failed Save demand keeps the draft and shows the server message', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  state.failNextWorkOrderWrite = true;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  await screen.findByText(/The save failed on the server/);
  // The draft is intact, still dirty, and nothing changed server-side.
  expect(within(dialog).getByLabelText('Quantity for A-100')).toHaveValue('30');
  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  expect(
    state.workOrders[0].demands.find((d) => d.id === 101)!.requested_quantity,
  ).toBe(25);
});

test('saving an OPEN Work Order with an incomplete row is blocked, not filtered', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  expect(
    await screen.findByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  // The incomplete row is still there — never silently dropped — and
  // nothing traveled.
  expect(within(dialog).getByText('EXTRA-77')).toBeInTheDocument();
  expect(state.calls.filter((call) => call.startsWith('PATCH'))).toHaveLength(
    0,
  );
});

/* ============ Release to production (§11.4) ============ */

async function openRelease(pn: string, woNumber = '007201') {
  const dialog = await openWorkOrderDetail(woNumber, pn);
  const row = within(dialog).getByText(pn).closest('tr')!;
  fireEvent.click(
    within(row as HTMLElement).getByRole('button', {
      name: 'Release to production…',
    }),
  );
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  // The release choices load from the real environment surfaces.
  await within(release).findByLabelText('Release quantity');
  return release;
}

test('release FLOATING confirms quantity, Area and Operation, and reports the committed result', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  // The quantity defaults to the COMMITTED requested demand quantity;
  // FLOATING is the default Route Mode and needs no Route.
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('7');
  expect(within(release).getByLabelText('Route Mode')).toHaveValue('FLOATING');

  // The explicit starting Area + Operation confirmation.
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  expect(release).toHaveTextContent(
    /starts in Material \(Operation Receiving\)/,
  );

  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  // The committed result: Quantity Flow id, route mode, Area, quantity
  // and the appended RECEIVED Movement.
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent('Quantity Flow');
  expect(result).toHaveTextContent('#500');
  expect(result).toHaveTextContent('× 7 pcs');
  expect(result).toHaveTextContent(/FLOATING — actual route derives/);
  expect(result).toHaveTextContent('Material');
  expect(result).toHaveTextContent('RECEIVED · movement #9000');

  fireEvent.click(within(result).getByRole('button', { name: 'Done' }));
  await screen.findByText(/E-500 released to production × 7/);

  // The fake committed exactly one FLOATING release for the demand.
  expect(state.committedReleases.size).toBe(1);
  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.route_mode).toBe('FLOATING');
  expect(commit.body.route_template_id).toBeNull();
  expect(commit.body.starting_area_id).toBe(1);
  expect(commit.body.operation_id).toBe(11);

  // The released state comes back from the SERVER (the dialog
  // reloaded the demand lines): read-only status, release and removal
  // disabled with the explanation. The mixed Work Order stays OPEN.
  const detailDialog = screen.getByRole('dialog', {
    name: 'Work Order Details',
  });
  const row = await waitFor(() => {
    const releasedRow = within(detailDialog)
      .getByText('E-500')
      .closest('tr') as HTMLElement;
    expect(within(releasedRow).getByText('Released')).toBeInTheDocument();
    return releasedRow;
  });
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
  expect(
    within(row).getByRole('button', { name: 'Remove line E-500' }),
  ).toBeDisabled();
  expect(
    within(detailDialog).getAllByText(
      'Cannot remove: production quantity has already been released.',
    ).length,
  ).toBeGreaterThan(0);
  expect(within(detailDialog).getByText('Open')).toBeInTheDocument();
});

test('release PLANNED requires an existing active Planned Route and fixes the starting step', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  fireEvent.change(within(release).getByLabelText('Route Mode'), {
    target: { value: 'PLANNED' },
  });
  fireEvent.change(within(release).getByLabelText('Planned Route'), {
    target: { value: '1' },
  });

  // The Route's first step fixes the starting Area and its Operation —
  // never a silent adjustment.
  expect(release).toHaveTextContent(
    /Material — fixed by the Route's first step/,
  );
  expect(release).toHaveTextContent(
    /Receiving — fixed by the Route's first step/,
  );
  expect(release).toHaveTextContent(/snapshot is taken on commit/);

  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent(/PLANNED — route snapshot/);
  expect(result).toHaveTextContent('#300');

  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.route_mode).toBe('PLANNED');
  expect(commit.body.route_template_id).toBe(1);
  expect(commit.body.starting_area_id).toBe(1);
  expect(commit.body.operation_id).toBe(11);
});

test('existing active quantity demands explicit confirmation before a separate flow is created', async () => {
  await renderWorkOrders();
  const release = await openRelease('A-100');

  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  // The backend answered 409 + distribution — nothing was created.
  expect(
    await within(release).findByText(/already has active quantity/),
  ).toBeInTheDocument();
  expect(release).toHaveTextContent(/Lathe × 40 \(flow #71\)/);
  expect(release).toHaveTextContent(/never automatically adds to or merges/);
  expect(state.committedReleases.size).toBe(0);

  // The confirm action stays disabled until the intent is explicit.
  const confirmButton = within(release).getByRole('button', {
    name: 'Confirm release',
  });
  expect(confirmButton).toBeDisabled();
  fireEvent.click(
    within(release).getByRole('checkbox', {
      name: /Release a separate Quantity Flow anyway/,
    }),
  );
  fireEvent.click(confirmButton);

  await screen.findByRole('dialog', { name: 'Release committed' });
  expect(state.committedReleases.size).toBe(1);

  // Both submissions used the SAME device_event_id — the confirmation
  // resubmission continues the submission, it never starts a new one.
  const releaseBodies = state.calls.filter((call) => call.includes('/release'));
  expect(releaseBodies).toHaveLength(2);
  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.confirm_active_quantity).toBe(true);
});

test('a transport retry reuses the device_event_id and can only replay the committed release', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });

  // The commit succeeds server-side but the response is lost.
  state.dropNextReleaseResponse = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  expect(
    await within(release).findByText(
      /The PartFlow server could not be reached/,
    ),
  ).toBeInTheDocument();
  expect(release).toHaveTextContent(
    /a retry of this submission can never create a second Quantity Flow/i,
  );
  expect(state.committedReleases.size).toBe(1);

  // Retry: the SAME device_event_id replays the original result.
  fireEvent.click(
    within(release).getByRole('button', { name: 'Retry release' }),
  );
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent(/already committed/);
  expect(result).toHaveTextContent('#500');
  // Still exactly ONE committed flow.
  expect(state.committedReleases.size).toBe(1);
  expect(releaseCalls()).toHaveLength(2);
});

test('a demand released in parts keeps offering the remaining quantity', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500'); // demand 103, requested 7

  // The quantity defaults to what is LEFT to release (all of it here).
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('7');
  fireEvent.change(within(release).getByLabelText('Release quantity'), {
    target: { value: '3' },
  });
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent('× 3 pcs');
  fireEvent.click(within(result).getByRole('button', { name: 'Done' }));

  // The line states what is actually released, the Work Order stays
  // Open, and the release action is still available for the remainder.
  const detailDialog = screen.getByRole('dialog', {
    name: 'Work Order Details',
  });
  const row = await waitFor(() => {
    const partial = within(detailDialog)
      .getByText('E-500')
      .closest('tr') as HTMLElement;
    expect(within(partial).getByText('Released 3/7')).toBeInTheDocument();
    return partial;
  });
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeEnabled();
  expect(within(detailDialog).getByText('Open')).toBeInTheDocument();
  // Removal stays blocked — quantity has been released (§13).
  expect(
    within(row).getByRole('button', { name: 'Remove line E-500' }),
  ).toBeDisabled();

  // Reopening offers exactly the remainder and refuses more than that.
  fireEvent.click(
    within(row).getByRole('button', { name: 'Release to production…' }),
  );
  const second = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  await within(second).findByLabelText('Release quantity');
  expect(within(second).getByLabelText('Release quantity')).toHaveValue('4');
  expect(second).toHaveTextContent(/already released 3/);
  expect(second).toHaveTextContent(/remaining 4/);

  fireEvent.change(within(second).getByLabelText('Release quantity'), {
    target: { value: '5' },
  });
  expect(second).toHaveTextContent(/Only 4 pcs remain to release/);
  expect(
    within(second).getByRole('button', { name: 'Confirm release' }),
  ).toBeDisabled();
});

test('a fully released demand line closes its release action', async () => {
  await renderWorkOrders();
  // Demand 102 (B-200) is fully released in the seeded server state.
  const detailDialog = await openWorkOrderDetail('007201', 'B-200');
  const row = within(detailDialog).getByText('B-200').closest('tr')!;

  expect(within(row as HTMLElement).getByText('Released')).toBeInTheDocument();
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Release to production…',
    }),
  ).toBeDisabled();
});

test('a changed release intent gets a new device_event_id; a plain retry keeps it', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });

  state.failNextRelease = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  await within(release).findByText(/Release rejected by the server\./);

  // An unchanged retry is the SAME submission — same key.
  state.failNextRelease = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Retry release' }),
  );
  await waitFor(() => expect(state.releaseAttempts).toHaveLength(2));
  expect(state.releaseAttempts[1].device_event_id).toBe(
    state.releaseAttempts[0].device_event_id,
  );

  // Editing a field the server fingerprint covers is a NEW intent: it
  // gets a fresh key, so the corrected release is never rejected as an
  // idempotency conflict (SLICE1 §14).
  fireEvent.change(within(release).getByLabelText('Release quantity'), {
    target: { value: '4' },
  });
  expect(
    within(release).getByRole('button', { name: 'Confirm release' }),
  ).toBeEnabled();
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  await screen.findByRole('dialog', { name: 'Release committed' });
  expect(state.releaseAttempts).toHaveLength(3);
  expect(state.releaseAttempts[2].quantity).toBe(4);
  expect(state.releaseAttempts[2].device_event_id).not.toBe(
    state.releaseAttempts[0].device_event_id,
  );
});

test('cancelling the release creates nothing', async () => {
  await renderWorkOrders();
  const release = await openRelease('C-300', '—');

  fireEvent.click(
    within(release).getByRole('button', { name: 'Cancel (Esc)' }),
  );

  expect(
    await screen.findByText('✕ Release cancelled — nothing was created.'),
  ).toBeInTheDocument();
  expect(releaseCalls()).toEqual([]);
  expect(state.committedReleases.size).toBe(0);
});

/* ============ View states ============ */

test('the list shows a real error state with Retry', async () => {
  state.failNextList = true;
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);

  await screen.findByText('Work Orders could not be loaded.');
  expect(screen.getByText('The database is unavailable.')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('007201')).toBeInTheDocument();
});

test('an empty server list shows the empty state', async () => {
  state.workOrders = [];
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);

  expect(
    await screen.findByText(
      'No Work Orders yet — create the first one with ＋ New Work Order.',
    ),
  ).toBeInTheDocument();
});

test('the development ?state=long preview appends the dense-table fixture', async () => {
  window.history.replaceState({}, '', '/management/work-orders?state=long');
  render(<App />);

  expect(
    await screen.findByText('007099-SUPPLEMENTAL-AMENDMENT-2026-REV-B'),
  ).toBeInTheDocument();
  // The real server rows render too.
  expect(screen.getByText('007201')).toBeInTheDocument();
});

test('offline blocks every write action while reading stays available', async () => {
  state.healthDown = true;
  await renderWorkOrders();

  // Reading works: the list rendered. Open the details dialog.
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Save demand' }),
    ).toBeDisabled(),
  );
  expect(
    within(dialog).getByRole('button', { name: '＋ Add Part manually' }),
  ).toBeDisabled();
  expect(within(dialog).getByLabelText('Scan PN barcode')).toBeDisabled();
  const row = within(dialog).getByText('A-100').closest('tr')!;
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Release to production…',
    }),
  ).toBeDisabled();
  // A saved line's removal is a server write — blocked offline too.
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Remove line A-100',
    }),
  ).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));

  // The New Work Order dialog blocks its save as well.
  openNewWorkOrderDialog();
  expect(screen.getByRole('button', { name: 'Save demand' })).toBeDisabled();
});

/* ============ Unsaved-change protection ============ */

async function makeDetailDirty() {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  return dialog;
}

test('cancelling the discard confirmation keeps the user on the dirty Work Order', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalledWith(
    'Work Orders has unsaved changes. Discard them and leave this view?',
  );
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('confirming the discard allows top-level navigation away', async () => {
  await makeDetailDirty();
  vi.spyOn(window, 'confirm').mockReturnValue(true);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(window.location.pathname).toBe('/scan-station');
});

test('Management sub-navigation is guarded too', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'PN Tracking' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

test('browser back is guarded while the Work Order detail is dirty', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.popState(window);

  expect(confirmSpy).toHaveBeenCalled();
});

test('reload and tab close are guarded through beforeunload while dirty', async () => {
  await makeDetailDirty();

  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);

  // A clean view never blocks unload.
  fireEvent.keyDown(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
    { key: 'Escape' },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  const cleanEvent = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(cleanEvent);
  expect(cleanEvent.defaultPrevented).toBe(false);
});

test('a dirty New Work Order dialog also guards top-level navigation', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007999' },
  });
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

/* ============ Server-derived released state ============ */

test('a demand released in an earlier session loads Released and read-only before any local action', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // B-200's release evidence comes from the SERVER response — no
  // release ever happened in this browser session.
  const row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
  expect(
    within(row).getByRole('button', { name: 'Remove line B-200' }),
  ).toBeDisabled();
  expect(
    within(row).getByText(
      'Cannot remove: production quantity has already been released.',
    ),
  ).toBeInTheDocument();

  // A duplicate scan of the released PN announces the read-only line.
  scanBarcode('PF:PN:B-200');
  expect(
    await screen.findByText(
      /B-200 is already on this Work Order and its production quantity is released — the line is read-only here\./,
    ),
  ).toBeInTheDocument();
  // Still exactly one B-200 line — nothing was added.
  expect(within(dialog).getAllByText('B-200')).toHaveLength(1);
});

test('the released state survives a full page reload', async () => {
  await renderWorkOrders();
  let dialog = await openWorkOrderDetail('007201', 'A-100');
  let row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();

  // Full reload: a brand-new application instance, same server state.
  cleanup();
  await renderWorkOrders();
  dialog = await openWorkOrderDetail('007201', 'A-100');
  row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
});

test('the WO list derives Open for a mixed Work Order and Released for a fully released one', async () => {
  await renderWorkOrders();

  // 007201 holds released AND unreleased demand — canonical OPEN.
  const mixedRow = workOrderRow('007201').closest('tr') as HTMLElement;
  expect(within(mixedRow).getByText('Open')).toBeInTheDocument();
  // 007300's every demand carries release evidence — RELEASED.
  const releasedRow = workOrderRow('007300').closest('tr') as HTMLElement;
  expect(within(releasedRow).getByText('Released')).toBeInTheDocument();
});

test('a fully released Work Order opens read-only', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007300', 'D-400');

  expect(dialog).toHaveTextContent('demand lines are read-only');
  expect(within(dialog).getAllByText('Released').length).toBeGreaterThan(0);
  expect(
    within(dialog).queryByRole('button', { name: 'Save demand' }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Release to production…' }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: '＋ Add Part manually' }),
  ).toBeNull();
});

/* ============ Release vs. unsaved demand edits ============ */

test('unsaved demand changes disable Release until saved; release then uses the committed quantity', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // Edit E-500's requested quantity — the draft is now dirty.
  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '9' },
  });
  const releasedButtonOf = (pn: string) =>
    within(within(dialog).getByText(pn).closest('tr') as HTMLElement).getByRole(
      'button',
      { name: 'Release to production…' },
    );
  expect(releasedButtonOf('E-500')).toBeDisabled();
  expect(releasedButtonOf('A-100')).toBeDisabled();
  expect(releasedButtonOf('E-500')).toHaveAttribute(
    'title',
    'Save or discard demand changes before releasing.',
  );
  expect(dialog).toHaveTextContent(
    'Save or discard demand changes before releasing.',
  );

  // Save demand commits the edit; Release re-enables.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  const enabledButton = await waitFor(() => {
    const button = releasedButtonOf('E-500');
    expect(button).not.toBeDisabled();
    return button;
  });

  // The release flow opens with the COMMITTED requested quantity.
  fireEvent.click(enabledButton);
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  await within(release).findByLabelText('Release quantity');
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('9');
  expect(release).toHaveTextContent(/requested 9/);
});

/* ============ Work Order Number verbatim ============ */

test('an entered WO Number travels and resolves verbatim — surrounding whitespace included', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '  WO-77  ' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/saved — business demand only/);

  // The duplicate lookup used the ORIGINAL string, never a trimmed one.
  expect(
    state.calls.filter(
      (call) =>
        call ===
        `GET /api/work-orders?number=${encodeURIComponent('  WO-77  ')}`,
    ),
  ).toHaveLength(1);
  // The stored number is byte-for-byte the entered value.
  expect(
    state.workOrders.some((w) => w.work_order_number === '  WO-77  '),
  ).toBe(true);
  expect(state.workOrders.some((w) => w.work_order_number === 'WO-77')).toBe(
    false,
  );
});

test('a whitespace-only WO Number saves NULL — trimming only DETECTS blank', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '   ' },
  });
  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  // Blank counts as "no number entered": no duplicate lookup runs and
  // the omission confirmation appears.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );
  await screen.findByText(/WO — saved — business demand only/);

  expect(state.workOrders[0].work_order_number).toBeNull();
  expect(
    state.calls.some((call) => call.includes('/api/work-orders?number=')),
  ).toBe(false);
});

/* ============ Audited external-number edit (internal WO) ============ */

test('an internal Work Order receives its external number through the audited edit and it persists', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  // Verbatim: the entered value keeps its surrounding whitespace.
  fireEvent.change(within(dialog).getByLabelText(/External WO Number/), {
    target: { value: ' WO-500 ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  // The Work Order has no due date — the omission confirmation stays.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );
  await screen.findByText(/demand updated — business demand only/);

  expect(state.workOrders[1].work_order_number).toBe(' WO-500 ');
  expect(dialog).toHaveTextContent('WO-500');
  // The entry disappeared — the Work Order is no longer internal.
  expect(within(dialog).queryByLabelText(/External WO Number/)).toBeNull();

  // Full reload: the exact entered number persists on the server.
  cleanup();
  await renderWorkOrders();
  expect(screen.getByText('WO-500')).toBeInTheDocument();
  expect(
    screen.queryByText('internal Work Order — no external number yet'),
  ).toBeNull();
});

test('an external-number conflict keeps the draft and shows the server message', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  fireEvent.change(within(dialog).getByLabelText(/External WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );

  // 409: nothing overwritten, the draft and the message stay.
  expect(
    await screen.findByText(/Work Order '007201' already exists/),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText(/External WO Number/)).toHaveValue(
    '007201',
  );
  expect(state.workOrders[1].work_order_number).toBeNull();
});

/* ============ Printable PN barcode label ============ */

test('the Add Part intake offers a printable Code 128 label for a new canonical PN', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '  pl-88  ' },
  });
  fireEvent.click(
    await screen.findByRole('button', { name: '＋ Create new PN “PL-88”' }),
  );
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // The label derives from the canonical PN identity — no master
  // record exists for PL-88 and none is needed.
  fireEvent.click(screen.getByRole('button', { name: 'Barcode label…' }));
  const label = await screen.findByRole('dialog', { name: 'PN barcode label' });
  expect(
    within(label).getByRole('img', { name: 'Barcode PF:PN:PL-88' }),
  ).toBeInTheDocument();
  expect(label).toHaveTextContent('PL-88 · PF:PN:PL-88');
  expect(
    within(label).getByRole('button', { name: 'Print Label' }),
  ).toBeInTheDocument();

  // Closing the label returns to the Add Part flow, values intact.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
});

test('an entered demand line offers the label for an existing PN master too', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:309-127');
  await screen.findByLabelText('Quantity for 309-127');

  fireEvent.click(screen.getByRole('button', { name: 'Barcode label…' }));
  const label = await screen.findByRole('dialog', { name: 'PN barcode label' });
  expect(
    within(label).getByRole('img', { name: 'Barcode PF:PN:309-127' }),
  ).toBeInTheDocument();
  expect(label).toHaveTextContent('309-127 · PF:PN:309-127');
});

/* ============ Saved-line removal vs. unsaved demand edits ============ */

test('unsaved demand changes disable saved-line removal; after Save the removals commit and the WO derives RELEASED', async () => {
  await renderWorkOrders();
  // 007201: B-200 released (server evidence), A-100 and E-500 saved
  // and unreleased.
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const removeButtonOf = (pn: string) =>
    within(dialog).getByRole('button', { name: `Remove line ${pn}` });

  // Make the draft dirty with a header edit (the WO due date).
  fireEvent.change(within(dialog).getByLabelText('WO due date'), {
    target: { value: '2026-09-20' },
  });

  // Saved-line removal is a committed server action — blocked while
  // unsaved edits are in flight, with the stated explanation.
  expect(removeButtonOf('A-100')).toBeDisabled();
  expect(removeButtonOf('A-100')).toHaveAttribute(
    'title',
    'Save or discard demand changes before removing a saved line.',
  );
  expect(dialog).toHaveTextContent(
    'Save or discard demand changes before removing a saved line.',
  );
  // An unsaved draft line stays removable locally — it IS the draft.
  scanBarcode('PF:PN:TEMP-1');
  await within(dialog).findByText('TEMP-1');
  fireEvent.click(removeButtonOf('TEMP-1'));
  expect(
    await screen.findByText('✕ Draft line removed — it had never been saved.'),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText('TEMP-1')).toBeNull();
  expect(removeButtonOf('A-100')).toBeDisabled();

  // Save the draft — nothing was auto-saved or auto-discarded; the
  // saved-line removal re-enables.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  expect(state.workOrders[0].due_date).toBe('2026-09-20');
  const enabledRemove = await waitFor(() => {
    const button = removeButtonOf('A-100');
    expect(button).not.toBeDisabled();
    return button;
  });

  // Confirmed removal of both unreleased lines: afterwards every
  // remaining persisted demand carries release evidence, so the Work
  // Order derives RELEASED — with no unsaved draft stranded or lost.
  fireEvent.click(enabledRemove);
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  await screen.findByText('✕ A-100 removed from 007201.');
  fireEvent.click(removeButtonOf('E-500'));
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  await screen.findByText('✕ E-500 removed from 007201.');

  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([102]);
  // The reloaded server detail derives RELEASED: the dialog is
  // read-only and shows no unsaved marker.
  await waitFor(() =>
    expect(dialog).toHaveTextContent('demand lines are read-only'),
  );
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Save demand' }),
  ).toBeNull();

  // The list derives RELEASED for the Work Order too.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const listRow = workOrderRow('007201').closest('tr') as HTMLElement;
  await waitFor(() =>
    expect(within(listRow).getByText('Released')).toBeInTheDocument(),
  );
});
