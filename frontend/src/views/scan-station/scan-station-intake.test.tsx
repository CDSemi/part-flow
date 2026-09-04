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

// Real Scan Station `Receive Quantity` (Phase 10.5 — GUI_DESIGN §4.7
// item 1, PROJECT_PROFILE §14) against a fake in-memory `/api` with the
// backend's semantics: the PN resolution reports `intake_available`,
// `part_number_known` and the internal blank-number MODIFY Work Orders
// a receipt may reuse, and `POST …/receipts` records the confirmed
// receipt (201 fresh, 200 replay, 409 `selection_required` listing the
// candidates). Covers: the three approved views settings → quantity →
// confirmation with the editable MODIFY/FLOATING defaults, the known
// and the new-PN copy, the Planned Route selection offered only for
// PLANNED and only for Routes starting at this Area, the optional due
// date and reason, the internal Work Order behavior with the explicit
// selection when several are plausible (never a guess), `Confirm
// receipt` as the only write point, the post-write refresh and focus
// restoration, the receipt NOT becoming the Undo target, the server
// refusal with nothing recorded, the lost response retried under the
// SAME device_event_id, and the offline write block.

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: 'QUEUED' | 'PROCESSING';
}

interface InternalWorkOrder {
  work_order_id: number;
  work_order_demand_id: number;
  received_date: string;
  due_date: string | null;
  requested_quantity: number;
  job_numbers: string[];
}

const AREAS = [
  { id: 2, name: 'Milling', color: '#3366ff' },
  { id: 6, name: 'Deburr', color: '#aa33aa' },
];
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'MILL', name: 'Milling', is_external: false },
  { id: 21, area_id: 2, code: 'DEBURR', name: 'Deburr', is_external: false },
  { id: 60, area_id: 6, code: 'FINISH', name: 'Finish', is_external: false },
];
const STATIONS = [
  { station_id: 'MILL-ST-01', area_id: 2 },
  { station_id: 'DEBURR-ST-01', area_id: 6 },
];
const ROUTES = [
  {
    id: 7,
    name: 'Bracket std v3',
    description: null,
    steps: [
      {
        id: 71,
        sequence: 10,
        area_id: 2,
        operation_id: 20,
        instructions: null,
      },
      {
        id: 72,
        sequence: 20,
        area_id: 6,
        operation_id: null,
        instructions: null,
      },
    ],
  },
  {
    id: 8,
    name: 'Finish only v1',
    description: null,
    steps: [
      {
        id: 81,
        sequence: 10,
        area_id: 6,
        operation_id: null,
        instructions: null,
      },
    ],
  },
];

let flows: Flow[];
let knownPartNumbers: Set<string>;
let internalWorkOrders: InternalWorkOrder[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let nextFlowId: number;
let writeFailure: null | 'lost-response' | { status: number; body: unknown };
// The station's Area has Machines (received quantity queues) or not
// (it enters direct processing).
let areaHasMachines: boolean;
// The station's Area configures a second Operation: the receipt then
// takes an explicit Operation choice (one resolves itself).
let secondOperation: boolean;
let healthDown: boolean;

function areaRef(areaId: number) {
  const area = AREAS.find((item) => item.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: false,
  };
}

function operationsOf(areaId: number) {
  return OPERATIONS.filter(
    (item) => item.area_id === areaId && (item.id !== 21 || secondOperation),
  );
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: {
      ...OPERATIONS.find((item) => item.area_id === flow.areaId)!,
      is_active: true,
    },
    processing_state: flow.state,
    machine_id: null,
    available_actions: ['TRANSFER', 'SCRAP'],
    work_order: null,
  };
}

function lines(items: Flow[]) {
  const byPn = new Map<string, Flow[]>();
  for (const flow of items) {
    byPn.set(flow.pn, [...(byPn.get(flow.pn) ?? []), flow]);
  }
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    total_quantity: group.reduce((sum, flow) => sum + flow.qty, 0),
    flows: group.map(flowWire),
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stationOf(url: string) {
  const id = decodeURIComponent(/\/scan-stations\/([^/]+)\//.exec(url)![1]);
  return STATIONS.find((item) => item.station_id === id)!;
}

function inventory(areaId: number) {
  const here = flows.filter((flow) => flow.areaId === areaId);
  const sum = (items: Flow[]) =>
    items.reduce((total, flow) => total + flow.qty, 0);
  const queued = here.filter((flow) => flow.state === 'QUEUED');
  const processing = here.filter((flow) => flow.state === 'PROCESSING');
  return json({
    area: areaRef(areaId),
    has_machines: areaHasMachines,
    lines: lines(here),
    total_part_numbers: lines(here).length,
    total_quantity: sum(here),
    queued: lines(queued),
    queued_quantity: sum(queued),
    machines: [],
    on_machine_quantity: 0,
    processing: lines(processing),
    processing_quantity: sum(processing),
    finished: [],
    finished_quantity: 0,
  });
}

function handle(url: string, method: string, body: unknown): Response {
  if (url === '/api/health') {
    return healthDown ? json({ detail: 'down' }, 503) : json({ status: 'ok' });
  }
  if (url === '/api/scan-stations') {
    return json(STATIONS.map((item) => ({ ...item, is_active: true })));
  }
  if (url === '/api/areas') {
    return json(
      AREAS.map((item) => ({
        ...areaRef(item.id),
        department_id: 1,
        barcode_value: `PF:AREA:${item.id}`,
        icon_url: null,
        is_active: true,
      })),
    );
  }
  if (url === '/api/departments') {
    return json([{ id: 1, name: 'Machining', is_active: true }]);
  }
  if (url === '/api/operations') {
    return json(
      OPERATIONS.map((item) => ({
        ...item,
        description: null,
        default_expected_duration: null,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/machines') return json([]);
  if (url === '/api/route-templates') return json(ROUTES);
  if (/\/context$/.test(url)) {
    const station = stationOf(url);
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Machining' },
      area: areaRef(station.area_id),
      operations: operationsOf(station.area_id),
      has_machines: areaHasMachines,
    });
  }
  const inv = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inv) return inventory(Number(inv[1]));

  if (/\/scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const input = body as { barcode?: string; part_number?: string };
    const scanned = input.barcode ?? `PF:PN:${input.part_number}`;
    const pn = scanned.slice('PF:PN:'.length).toUpperCase();
    const mine = flows.filter((flow) => flow.pn === pn);
    const inArea = mine.filter((flow) => flow.areaId === station.area_id);
    const candidates = mine.filter((flow) => flow.areaId !== station.area_id);
    return json({
      part_number: pn,
      station_id: station.station_id,
      area: areaRef(station.area_id),
      resolution: inArea.length
        ? 'ALREADY_IN_AREA'
        : candidates.length
          ? 'TRANSFER_SOURCE_AVAILABLE'
          : 'NO_TRANSFERABLE_QUANTITY',
      in_area: inArea.map(flowWire),
      candidates: candidates.map((flow) => ({
        ...flowWire(flow),
        current_area: areaRef(flow.areaId),
        route_status: 'FLOATING',
        expected_next_area: null,
        expected_operation_id: null,
        suggested_operation_id: operationsOf(station.area_id)[0].id,
        repair_available: false,
      })),
      operations: operationsOf(station.area_id),
      has_active_demand: false,
      // The SERVER judges the entry condition of `Receive Quantity`.
      intake_available: mine.length === 0,
      part_number_known: knownPartNumbers.has(pn),
      internal_work_orders: mine.length === 0 ? internalWorkOrders : [],
      transfer_blocked_reason: null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
      combine_groups: [],
      scrapped_quantity: 0,
      stocked_quantity: 0,
      available_stocked_quantity: 0,
      stock_available: false,
    });
  }

  if (/\/receipts$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity: number;
      request_type: 'NEW' | 'MODIFY';
      route_mode: 'FLOATING' | 'PLANNED';
      route_template_id: number | null;
      operation_id: number | null;
      due_date: string | null;
      reason: string | null;
      work_order_id: number | null;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    if (writeFailure === 'lost-response') {
      writeFailure = null;
      // The server COMMITTED and the response was lost: the retry of
      // the same id must replay it.
      const result = record(station, request);
      committed.set(request.device_event_id, result);
      throw new TypeError('Failed to fetch');
    }
    if (writeFailure && typeof writeFailure === 'object') {
      const failure = writeFailure;
      writeFailure = null;
      return json(failure.body, failure.status);
    }
    if (request.request_type === 'MODIFY' && internalWorkOrders.length > 1) {
      if (
        request.work_order_id === null ||
        !internalWorkOrders.some(
          (item) => item.work_order_id === request.work_order_id,
        )
      ) {
        return json(
          {
            detail: 'Select the internal Work Order this quantity belongs to.',
            selection_required: true,
            work_orders: internalWorkOrders,
          },
          409,
        );
      }
    }
    const result = record(station, request);
    committed.set(request.device_event_id, result);
    return json(result, 201);
  }

  if (/\/undo-preview\//.test(url)) {
    return json({
      device_event_id: /undo-preview\/([^/]+)$/.exec(url)![1],
      part_number: 'X',
      command_kind: 'INTAKE',
      recorded_at: '2026-09-03T10:00:00Z',
      station_id: 'MILL-ST-01',
      movements: [],
      restored: [],
      eligible: false,
      ineligible_reason:
        'This action received new quantity into production and created the Work Order Demand behind it.',
    });
  }
  throw new Error(`unexpected request ${method} ${url}`);
}

function record(
  station: { station_id: string; area_id: number },
  request: {
    part_number: string;
    quantity: number;
    request_type: 'NEW' | 'MODIFY';
    route_mode: 'FLOATING' | 'PLANNED';
    route_template_id: number | null;
    operation_id: number | null;
    reason: string | null;
    work_order_id: number | null;
    device_event_id: string;
  },
) {
  const state = areaHasMachines ? 'QUEUED' : 'PROCESSING';
  const flow: Flow = {
    id: nextFlowId++,
    pn: request.part_number,
    qty: request.quantity,
    areaId: station.area_id,
    state,
  };
  flows = [...flows, flow];
  knownPartNumbers.add(request.part_number);
  return {
    movement_id: nextMovementId++,
    quantity_flow_id: flow.id,
    part_number: request.part_number,
    quantity: request.quantity,
    request_type: request.request_type,
    route_mode: request.route_mode,
    assigned_route_id: request.route_template_id === null ? null : 900,
    area_id: station.area_id,
    operation_id: request.operation_id ?? operationsOf(station.area_id)[0].id,
    processing_state: state,
    work_order_id: request.work_order_id ?? 500,
    work_order_demand_id: 600,
    work_order_reused: request.work_order_id !== null,
    reason: request.reason,
    station_id: station.station_id,
    device_event_id: request.device_event_id,
    occurred_at: '2026-09-03T10:00:00Z',
  };
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [];
  knownPartNumbers = new Set(['118-052']);
  internalWorkOrders = [];
  committed = new Map();
  requests = [];
  nextMovementId = 700;
  nextFlowId = 300;
  writeFailure = null;
  areaHasMachines = true;
  secondOperation = false;
  healthDown = false;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const requestBody = init?.body
        ? JSON.parse(String(init.body))
        : undefined;
      if (!url.endsWith('/api/health')) {
        requests.push({ url, method, body: requestBody });
      }
      return Promise.resolve().then(() => handle(url, method, requestBody));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId = 'MILL-ST-01') {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  return screen.findByLabelText('Scan barcode');
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function receiptRequests() {
  return requests.filter((item) => item.url.endsWith('/receipts'));
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function quantityInput(container: HTMLElement) {
  return within(container).getByLabelText(/^Quantity: /);
}

/** Walk the three approved views to the confirmation. */
async function toConfirmation(quantity: string, pn = 'NEW-PN-1') {
  scan(`PF:PN:${pn}`);
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: quantity } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByRole('button', { name: 'Confirm receipt' });
  return box;
}

/* ============ Step 1 — settings ============ */

test('a first-seen PN opens the settings view with the editable defaults', async () => {
  await renderStation();

  scan('PF:PN:new-pn-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });

  // The scanner value is canonicalized by the SERVER.
  expect(box).toHaveTextContent('NEW-PN-1');
  expect(box).toHaveTextContent('New Part Number. Verify it carefully');
  expect(within(box).getByLabelText('Request Type')).toHaveValue('MODIFY');
  expect(within(box).getByLabelText('Route Mode')).toHaveValue('FLOATING');
  // No Planned Route field while FLOATING, and no quantity on step 1.
  expect(within(box).queryByLabelText('Planned Route')).toBeNull();
  expect(within(box).queryByLabelText(/^Quantity: /)).toBeNull();
  expect(box).toHaveTextContent(
    'Creates an internal Work Order without an external number',
  );
  expect(receiptRequests()).toHaveLength(0);
});

test('a known PN without active demand reads as a known Part Number', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });

  expect(box).toHaveTextContent(
    'This Part Number has no active production demand',
  );
});

test('PLANNED offers only the Planned Routes that start at this Area', async () => {
  await renderStation();

  scan('PF:PN:NEW-PN-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  fireEvent.change(within(box).getByLabelText('Route Mode'), {
    target: { value: 'PLANNED' },
  });

  const routes = await within(box).findByLabelText('Planned Route');
  const options = within(routes as HTMLElement).getAllByRole('option');
  // `Finish only v1` starts in Deburr — it is never offered here.
  expect(options.map((option) => option.textContent)).toEqual([
    'Select a Planned Route…',
    'Bracket std v3',
  ]);
  // Next stays blocked until a Route is chosen.
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
});

/* ============ Step 3 — the only write point ============ */

test('Confirm receipt is the only write point and reports the server result', async () => {
  await renderStation();

  const box = await toConfirmation('7');
  expect(box).toHaveTextContent('Receive Quantity');
  expect(box).toHaveTextContent('7 pcs');
  expect(box).toHaveTextContent('MODIFY');
  expect(box).toHaveTextContent('FLOATING — actual trace');
  expect(box).toHaveTextContent('RECEIVED');
  expect(receiptRequests()).toHaveLength(0);

  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  const toast = await notice();
  expect(toast).toHaveTextContent('NEW-PN-1 × 7 received into Milling queue');
  const request = receiptRequests()[0].body;
  expect(request.part_number).toBe('NEW-PN-1');
  expect(request.quantity).toBe(7);
  expect(request.request_type).toBe('MODIFY');
  expect(request.route_mode).toBe('FLOATING');
  expect(request.route_template_id).toBeNull();
  expect(request.work_order_id).toBeNull();
  // The station context and the Area inventory are re-read from the
  // server, and the barcode input regains focus.
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByLabelText('Scan barcode'));
  });
  expect(
    requests.filter((item) => /\/inventory$/.test(item.url)).length,
  ).toBeGreaterThan(1);
});

test('the receipt records the due date, the reason and the chosen Operation', async () => {
  secondOperation = true;
  await renderStation();

  scan('PF:PN:NEW-PN-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  fireEvent.change(within(box).getByLabelText(/^Due date/), {
    target: { value: '2026-10-15' },
  });
  fireEvent.change(within(box).getByLabelText('Reason / notes'), {
    target: { value: 'customer return for rework' },
  });
  // The Area has two Operations: the choice is explicit.
  fireEvent.click(within(box).getByRole('button', { name: 'Deburr' }));
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: '4' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  expect(box).toHaveTextContent('Oct 15, 2026');
  expect(box).toHaveTextContent('customer return for rework');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  const request = receiptRequests()[0].body;
  expect(request.due_date).toBe('2026-10-15');
  expect(request.reason).toBe('customer return for rework');
  expect(request.operation_id).toBe(21);
});

test('a PLANNED receipt sends the selected Planned Route', async () => {
  await renderStation();

  scan('PF:PN:NEW-PN-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  fireEvent.change(within(box).getByLabelText('Route Mode'), {
    target: { value: 'PLANNED' },
  });
  const routes = await within(box).findByLabelText('Planned Route');
  fireEvent.change(routes, { target: { value: '7' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: '2' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  expect(box).toHaveTextContent('PLANNED — Bracket std v3');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  expect(receiptRequests()[0].body.route_mode).toBe('PLANNED');
  expect(receiptRequests()[0].body.route_template_id).toBe(7);
});

test('an Area without Machines receives into direct processing', async () => {
  areaHasMachines = false;
  await renderStation();

  const box = await toConfirmation('3');
  expect(box).toHaveTextContent('Milling — direct processing');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  expect(await notice()).toHaveTextContent(
    'NEW-PN-1 × 3 received into Milling',
  );
});

/* ============ Internal Work Order behavior (§14) ============ */

test('one applicable internal Work Order is named and reused', async () => {
  internalWorkOrders = [
    {
      work_order_id: 41,
      work_order_demand_id: 91,
      received_date: '2026-08-20',
      due_date: null,
      requested_quantity: 4,
      job_numbers: ['17555'],
    },
  ];
  await renderStation();

  const box = await toConfirmation('5');
  expect(box).toHaveTextContent(
    'Adds the quantity to the internal Work Order without an external number',
  );
  expect(box).toHaveTextContent('Received Aug 20, 2026');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  expect(receiptRequests()[0].body.work_order_id).toBe(41);
});

test('several plausible internal Work Orders require an explicit selection', async () => {
  internalWorkOrders = [
    {
      work_order_id: 41,
      work_order_demand_id: 91,
      received_date: '2026-08-20',
      due_date: null,
      requested_quantity: 4,
      job_numbers: [],
    },
    {
      work_order_id: 42,
      work_order_demand_id: 92,
      received_date: '2026-08-28',
      due_date: '2026-09-30',
      requested_quantity: 6,
      job_numbers: [],
    },
  ];
  await renderStation();

  scan('PF:PN:NEW-PN-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  expect(box).toHaveTextContent(
    'Several internal Work Orders without an external number could take this quantity',
  );
  // Nothing is chosen for the operator: Next stays blocked.
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();

  fireEvent.click(
    within(box).getByRole('button', { name: /Received Aug 28, 2026/ }),
  );
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: '2' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  expect(receiptRequests()[0].body.work_order_id).toBe(42);
});

test('a NEW receipt never reuses an internal Work Order', async () => {
  internalWorkOrders = [
    {
      work_order_id: 41,
      work_order_demand_id: 91,
      received_date: '2026-08-20',
      due_date: null,
      requested_quantity: 4,
      job_numbers: [],
    },
  ];
  await renderStation();

  scan('PF:PN:NEW-PN-1');
  const box = await screen.findByRole('dialog', { name: 'Receive Quantity' });
  fireEvent.change(within(box).getByLabelText('Request Type'), {
    target: { value: 'NEW' },
  });
  expect(box).toHaveTextContent(
    'Creates an internal Work Order without an external number',
  );
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: '2' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  expect(receiptRequests()[0].body.request_type).toBe('NEW');
  expect(receiptRequests()[0].body.work_order_id).toBeNull();
});

/* ============ Refusals, retries and the write block ============ */

test('a refused receipt keeps the wizard open and records nothing', async () => {
  writeFailure = {
    status: 409,
    body: { detail: 'Part Number now has active Work Order Demand.' },
  };
  await renderStation();

  const box = await toConfirmation('4');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await within(box).findByText(/now has active Work Order Demand/);
  // Nothing recorded: no success notice, and the wizard offers a retry
  // rather than reporting a receipt.
  expect(document.querySelector('.ss-toast')).toBeNull();
  expect(
    within(box).getByRole('button', { name: 'Retry receipt' }),
  ).toBeInTheDocument();
});

test('a selection-required refusal returns to the settings view with the candidates', async () => {
  writeFailure = {
    status: 409,
    body: {
      detail: 'Select the internal Work Order this quantity belongs to.',
      selection_required: true,
      work_orders: [
        {
          work_order_id: 41,
          work_order_demand_id: 91,
          received_date: '2026-08-20',
          due_date: null,
          requested_quantity: 4,
          job_numbers: [],
        },
        {
          work_order_id: 42,
          work_order_demand_id: 92,
          received_date: '2026-08-28',
          due_date: null,
          requested_quantity: 6,
          job_numbers: [],
        },
      ],
    },
  };
  await renderStation();

  const box = await toConfirmation('4');
  const first = receiptRequests().length;
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  // Back on the settings view with the server's candidates, nothing
  // recorded and no guess made.
  await within(box).findByText(/Select the internal Work Order/);
  expect(box).toHaveTextContent(
    'Several internal Work Orders without an external number could take this quantity',
  );
  expect(receiptRequests()).toHaveLength(first + 1);
  const firstEventId = receiptRequests()[first].body.device_event_id;

  fireEvent.click(
    within(box).getByRole('button', { name: /Received Aug 28, 2026/ }),
  );
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.change(quantityInput(box), { target: { value: '4' } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  await notice();
  const retry = receiptRequests()[receiptRequests().length - 1].body;
  expect(retry.work_order_id).toBe(42);
  // A refused request wrote nothing, so the corrected intent is a NEW
  // one and never replays the refused id.
  expect(retry.device_event_id).not.toBe(firstEventId);
});

test('a lost response is retried under the SAME device_event_id — never twice', async () => {
  writeFailure = 'lost-response';
  await renderStation();

  const box = await toConfirmation('6');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));

  const retry = await within(box).findByRole('button', {
    name: 'Retry the same receipt',
  });
  expect(box).toHaveTextContent('The server did not answer');
  const firstEventId = receiptRequests()[0].body.device_event_id;

  fireEvent.click(retry);

  const toast = await notice();
  expect(toast).toHaveTextContent('already recorded by the server');
  expect(receiptRequests()).toHaveLength(2);
  expect(receiptRequests()[1].body.device_event_id).toBe(firstEventId);
  expect(flows.filter((flow) => flow.pn === 'NEW-PN-1')).toHaveLength(1);
});

test('a disconnected station cannot confirm a receipt', async () => {
  await renderStation();
  const box = await toConfirmation('4');
  healthDown = true;

  await waitFor(() => {
    expect(
      within(box).getByRole('button', { name: 'Confirm receipt' }),
    ).toBeDisabled();
  });
  expect(receiptRequests()).toHaveLength(0);
});

test('Cancel abandons the receipt with nothing recorded', async () => {
  await renderStation();

  const box = await toConfirmation('4');
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));

  const toast = await notice();
  expect(toast).toHaveTextContent('Cancelled. No changes were recorded.');
  expect(receiptRequests()).toHaveLength(0);
});

/* ============ After the receipt ============ */

test('a recorded receipt is not offered as the Undo target', async () => {
  await renderStation();

  const box = await toConfirmation('5');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));
  await notice();

  // The receipt also created the Work Order Demand behind the
  // quantity, which a Movement-level reversal never rewrites: Undo
  // stays disabled and the Last Scanned PN shows nothing to reverse.
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /UNDO/ })).toBeDisabled();
  });
});

test('the received quantity appears in the Area after the server confirmed', async () => {
  await renderStation();

  const box = await toConfirmation('9');
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm receipt' }));
  await notice();

  await waitFor(() => {
    expect(screen.getByText('NEW-PN-1')).toBeInTheDocument();
  });
  expect(flows).toHaveLength(1);
  expect(flows[0]).toMatchObject({ pn: 'NEW-PN-1', qty: 9, state: 'QUEUED' });
});
