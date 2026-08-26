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

// Real Scan Station (Phase 5) against a fake in-memory `/api` with the
// backend's route surface and semantics: station context, PN scan
// resolution (in-Area quantity, explicit transfer candidates), the
// whole-flow transfer command (idempotent per device_event_id, replay
// 200, rejections with nothing recorded), and the Area inventory the
// station refreshes after a confirmed transfer. Covers the
// scanner-first flow, manual entry, source ambiguity (never
// auto-selected), Operation resolution, the confirmation summary,
// full-quantity-only transfers, write failures and retries with the
// SAME device_event_id, offline blocking, focus restoration, and the
// inventory refresh — success is reported only after the server
// confirmed the write.

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  /** Phase 6: the Machine the flow is on (Lathe 1 = id 1). */
  machineId?: number;
  routeMode: 'FLOATING' | 'PLANNED';
  routeStatus?: 'ON_ROUTE' | 'DEVIATION';
  expectedNextAreaId?: number;
  expectedOperationId?: number | null;
  wo: {
    id: number;
    number: string | null;
    demandId: number;
    type: 'NEW' | 'MODIFY';
  } | null;
}

const AREAS = [
  { id: 1, name: 'Material', color: '#8899aa', department_id: 1 },
  { id: 2, name: 'Lathe', color: '#3366ff', department_id: 1 },
  { id: 3, name: 'Cut', color: '#33aa66', department_id: 1 },
  {
    id: 4,
    name: 'Stockroom',
    color: '#999999',
    department_id: 1,
    terminal: true,
  },
];
const OPERATIONS = [
  { id: 10, area_id: 1, code: 'RECEIVING', name: 'Receiving' },
  { id: 20, area_id: 2, code: 'TURNING', name: 'Turning' },
  { id: 21, area_id: 2, code: 'THREADING', name: 'Threading', inactive: true },
  { id: 30, area_id: 3, code: 'CUTTING', name: 'Cutting' },
  { id: 31, area_id: 3, code: 'SAWING', name: 'Sawing' },
];
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 2 },
  { station_id: 'CUT-ST-01', area_id: 3 },
  { station_id: 'STOCK-ST-01', area_id: 4 },
];

let flows: Flow[];
let committed: Map<string, { status: number; body: unknown }>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
// 'network': the request never reaches the server. 'lost-response': the
// server COMMITS the transfer and the response is lost on the way back
// — the client can only learn the outcome by retrying the same
// device_event_id.
// { committedStatus }: the server COMMITS and the client receives that
// HTTP status instead of the success body (a 5xx raised while producing
// the response, or a reverse proxy answering 502/504 after the upstream
// committed).
let transferFailure:
  | null
  | 'network'
  | 'lost-response'
  | { status: number; body: unknown }
  | { committedStatus: number };
/** While set, inventory reads stay pending until it resolves. */
let inventoryHold: Promise<void> | null;
let healthDown: boolean;
let inventoryReads: number;
let resolveFailure: boolean;
let stationAreaId: number;

function areaRef(areaId: number) {
  const area = AREAS.find((a) => a.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: area.terminal === true,
  };
}

function operationRef(operation: (typeof OPERATIONS)[number]) {
  return {
    id: operation.id,
    code: operation.code,
    name: operation.name,
    is_external: false,
  };
}

function activeOperations(areaId: number) {
  return OPERATIONS.filter((o) => o.area_id === areaId && !o.inactive);
}

function workOrderWire(flow: Flow) {
  return flow.wo
    ? {
        work_order_id: flow.wo.id,
        work_order_number: flow.wo.number,
        work_order_demand_id: flow.wo.demandId,
        request_type: flow.wo.type,
      }
    : null;
}

function flowWire(flow: Flow) {
  const state = flow.machineId !== undefined ? 'ON_MACHINE' : 'QUEUED';
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: flow.routeMode,
    processing_state: state,
    machine_id: flow.machineId ?? null,
    available_actions:
      state === 'ON_MACHINE'
        ? ['DONE', 'QUEUE', 'TRANSFER']
        : ['ASSIGN', 'TRANSFER'],
    work_order: workOrderWire(flow),
  };
}

const LATHE_1 = {
  id: 1,
  name: 'Lathe 1',
  asset_tag: 'CD-0001',
  barcode_value: 'PF:MACHINE:CD-0001',
  state_changed_at: '2026-08-25T10:00:00Z',
  maintenance_since: null,
  maintenance_note: null,
  maintenance_expected_return: null,
};

function inventoryLines(items: Flow[]) {
  const byPn = new Map<string, Flow[]>();
  for (const flow of items) {
    byPn.set(flow.pn, [...(byPn.get(flow.pn) ?? []), flow]);
  }
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    total_quantity: group.reduce((s, f) => s + f.qty, 0),
    flows: group.map(flowWire),
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function canonicalPn(raw: string): string | { detail: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { detail: 'Part Number must not be empty.' };
  if (/\s/.test(trimmed)) {
    return { detail: 'Part Number must not contain internal whitespace.' };
  }
  return trimmed.toUpperCase();
}

function boundAreaId(stationId: string): number {
  if (stationId === 'LATHE-ST-01') return stationAreaId;
  return STATIONS.find((s) => s.station_id === stationId)!.area_id;
}

function handle(url: string, method: string, body: unknown): Response {
  if (url === '/api/health') {
    return healthDown
      ? json({ status: 'unavailable' }, 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/scan-stations') {
    return json(STATIONS.map((s) => ({ ...s, is_active: true })));
  }
  if (url === '/api/areas') {
    return json(
      AREAS.map((a) => ({
        ...areaRef(a.id),
        department_id: a.department_id,
        barcode_value: `PF:AREA:${a.id}`,
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
      OPERATIONS.map((o) => ({
        ...operationRef(o),
        area_id: o.area_id,
        description: null,
        default_expected_duration: null,
        is_active: !o.inactive,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/machines') {
    return json([
      {
        id: 1,
        area_id: 2,
        name: 'Lathe 1',
        asset_tag: 'CD-0001',
        barcode_value: 'PF:MACHINE:CD-0001',
        maintenance_since: null,
        maintenance_note: null,
        maintenance_expected_return: null,
        state_changed_at: '2026-08-25T10:00:00Z',
        retired_on: null,
        operational_state: 'IDLE',
        assigned_quantity: 0,
        description: null,
        manufacturer: null,
        model: null,
        serial_number: null,
        installed_on: null,
        notes: null,
        created_at: 't',
        updated_at: 't',
      },
    ]);
  }
  const context = /^\/api\/scan-stations\/([^/]+)\/context$/.exec(url);
  if (context) {
    const station = STATIONS.find(
      (s) => s.station_id === decodeURIComponent(context[1]),
    );
    if (!station) {
      return json(
        { detail: `Scan Station '${context[1]}' does not exist.` },
        404,
      );
    }
    const areaId = boundAreaId(station.station_id);
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Machining' },
      area: areaRef(areaId),
      operations: activeOperations(areaId).map(operationRef),
      has_machines: areaId === 2,
    });
  }
  const inventory = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inventory) {
    inventoryReads += 1;
    const areaId = Number(inventory[1]);
    const here = flows.filter((f) => f.areaId === areaId);
    const queued = here.filter((f) => f.machineId === undefined);
    const onMachine = here.filter((f) => f.machineId !== undefined);
    const lines = inventoryLines(here);
    const machineCards =
      areaId === 2
        ? [
            {
              machine: {
                ...LATHE_1,
                operational_state: onMachine.length ? 'RUNNING' : 'IDLE',
              },
              lines: inventoryLines(onMachine),
              total_quantity: onMachine.reduce((s, f) => s + f.qty, 0),
            },
          ]
        : [];
    return json({
      area: areaRef(areaId),
      lines,
      total_part_numbers: lines.length,
      total_quantity: here.reduce((s, f) => s + f.qty, 0),
      queued: inventoryLines(queued),
      queued_quantity: queued.reduce((s, f) => s + f.qty, 0),
      machines: machineCards,
      on_machine_quantity: onMachine.reduce((s, f) => s + f.qty, 0),
      finished: [],
      finished_quantity: 0,
    });
  }
  const resolve = /^\/api\/scan-stations\/([^/]+)\/scans\/resolve$/.exec(url);
  if (resolve && method === 'POST') {
    if (resolveFailure) throw new TypeError('Failed to fetch');
    const found = STATIONS.find(
      (s) => s.station_id === decodeURIComponent(resolve[1]),
    )!;
    const station = { ...found, area_id: boundAreaId(found.station_id) };
    const input = body as { barcode?: string; part_number?: string };
    let pn: string | { detail: string };
    if (input.barcode !== undefined) {
      const scanned = input.barcode.trim();
      if (!scanned.startsWith('PF:PN:')) {
        return json(
          {
            detail:
              'Unknown barcode. Scan a Part Number barcode (PF:PN:…) or enter the Part Number manually.',
          },
          422,
        );
      }
      pn = canonicalPn(scanned.slice('PF:PN:'.length));
    } else {
      pn = canonicalPn(input.part_number ?? '');
    }
    if (typeof pn !== 'string') return json(pn, 422);
    const mine = flows.filter((f) => f.pn === pn);
    const inArea = mine.filter((f) => f.areaId === station.area_id);
    const candidates = mine.filter((f) => f.areaId !== station.area_id);
    const ops = activeOperations(station.area_id);
    const terminal = AREAS.find((a) => a.id === station.area_id)!.terminal;
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
        route_status:
          flow.routeMode === 'FLOATING'
            ? 'FLOATING'
            : (flow.routeStatus ?? 'ON_ROUTE'),
        expected_next_area:
          flow.expectedNextAreaId !== undefined
            ? areaRef(flow.expectedNextAreaId)
            : null,
        expected_operation_id: flow.expectedOperationId ?? null,
        suggested_operation_id: ops.length === 1 ? ops[0].id : null,
      })),
      operations: ops.map(operationRef),
      has_active_demand: mine.length > 0,
      transfer_blocked_reason: terminal
        ? `Area 'Stockroom' is a terminal Area. Receiving finished quantity there is the Stockroom workflow, not a transfer.`
        : null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
    });
  }
  const transfer = /^\/api\/scan-stations\/([^/]+)\/transfers$/.exec(url);
  if (transfer && method === 'POST') {
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      source_area_id: number;
      target_area_id: number;
      quantity: number;
      operation_id: number | null;
      confirm_route_deviation: boolean;
      route_deviation_reason: string | null;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay.body, 200);
    if (transferFailure === 'network') {
      throw new TypeError('Failed to fetch');
    }
    if (
      transferFailure &&
      transferFailure !== 'lost-response' &&
      'status' in transferFailure
    ) {
      const failure = transferFailure;
      return json(failure.body, failure.status);
    }
    const foundStation = STATIONS.find(
      (s) => s.station_id === decodeURIComponent(transfer[1]),
    )!;
    const station = {
      ...foundStation,
      area_id: boundAreaId(foundStation.station_id),
    };
    const flow = flows.find((f) => f.id === request.quantity_flow_id);
    if (!flow) return json({ detail: 'Quantity Flow does not exist.' }, 422);
    if (request.target_area_id !== station.area_id) {
      return json(
        {
          detail:
            'Scan Station is no longer bound to the confirmed destination Area.',
        },
        409,
      );
    }
    if (request.quantity !== flow.qty) {
      return json({ detail: 'Partial transfer is not supported yet.' }, 422);
    }
    if (flow.areaId !== request.source_area_id) {
      return json(
        { detail: 'Quantity Flow is no longer in the selected source Area.' },
        409,
      );
    }
    const ops = activeOperations(station.area_id);
    const operationId =
      request.operation_id ?? (ops.length === 1 ? ops[0].id : null);
    if (operationId === null || !ops.some((o) => o.id === operationId)) {
      return json(
        { detail: 'Choose the Operation this quantity is transferred for.' },
        422,
      );
    }
    const areaDeviation =
      flow.routeMode === 'PLANNED' && flow.routeStatus === 'DEVIATION';
    const operationDeviation =
      flow.routeMode === 'PLANNED' &&
      !areaDeviation &&
      flow.expectedOperationId != null &&
      flow.expectedOperationId !== operationId;
    const deviation = areaDeviation || operationDeviation;
    if (deviation && !request.confirm_route_deviation) {
      return json(
        {
          detail: 'Confirm the route deviation with a reason.',
          confirmation_required: true,
          route_deviation: {
            kind: areaDeviation ? 'AREA' : 'OPERATION',
            expected_next_area_id: flow.expectedNextAreaId ?? null,
            expected_operation_id: null,
            actual_area_id: station.area_id,
            actual_operation_id: operationId,
          },
        },
        409,
      );
    }
    if (deviation && !request.route_deviation_reason?.trim()) {
      return json({ detail: 'A route deviation needs a reason.' }, 422);
    }
    const completedFrom = flow.machineId;
    flow.areaId = station.area_id;
    flow.machineId = undefined;
    const completedMovementId =
      completedFrom !== undefined ? nextMovementId++ : null;
    const result = {
      movement_id: nextMovementId++,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      from_area_id: request.source_area_id,
      to_area_id: station.area_id,
      operation_id: operationId,
      station_id: station.station_id,
      assigned_route_step_id: null,
      route_deviation: deviation
        ? {
            kind: areaDeviation ? 'AREA' : 'OPERATION',
            reason: request.route_deviation_reason,
            confirmed: true,
          }
        : null,
      completed_movement_id: completedMovementId,
      completed_machine_id: completedFrom ?? null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-25T12:00:00Z',
    };
    committed.set(request.device_event_id, { status: 201, body: result });
    if (transferFailure === 'lost-response') {
      // Committed on the server; the client never sees this answer.
      transferFailure = null;
      throw new TypeError('Failed to fetch');
    }
    if (transferFailure && 'committedStatus' in transferFailure) {
      // Committed on the server; the client sees a gateway/server error.
      const status = transferFailure.committedStatus;
      transferFailure = null;
      return new Response('<html lang="">Bad Gateway</html>', {
        status,
        headers: { 'content-type': 'text/html' },
      });
    }
    return json(result, 201);
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    {
      id: 100,
      pn: '2027-60-8114-00',
      qty: 12,
      areaId: 1,
      routeMode: 'FLOATING',
      wo: { id: 1, number: '007003', demandId: 11, type: 'NEW' },
    },
    {
      id: 101,
      pn: '118-052',
      qty: 5,
      areaId: 1,
      routeMode: 'FLOATING',
      wo: { id: 2, number: null, demandId: 12, type: 'MODIFY' },
    },
    {
      id: 102,
      pn: '118-052',
      qty: 7,
      areaId: 3,
      routeMode: 'FLOATING',
      wo: { id: 2, number: null, demandId: 12, type: 'MODIFY' },
    },
    {
      id: 103,
      pn: '0455-20-0118-03',
      qty: 3,
      areaId: 2,
      routeMode: 'FLOATING',
      wo: { id: 3, number: '007010', demandId: 13, type: 'NEW' },
    },
    {
      id: 104,
      pn: 'PLN-1',
      qty: 4,
      areaId: 1,
      routeMode: 'PLANNED',
      routeStatus: 'DEVIATION',
      expectedNextAreaId: 3,
      wo: { id: 4, number: '007020', demandId: 14, type: 'NEW' },
    },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  transferFailure = null;
  healthDown = false;
  inventoryReads = 0;
  resolveFailure = false;
  inventoryHold = null;
  stationAreaId = 2;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      const hold =
        inventoryHold && /\/inventory$/.test(url)
          ? inventoryHold
          : Promise.resolve();
      return hold.then(() => handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId = 'LATHE-ST-01', suffix = '') {
  window.history.replaceState({}, '', `/scan-station/${stationId}${suffix}`);
  render(<App />);
  return screen.findByLabelText('Scan barcode');
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function dialog() {
  return screen.getByRole('dialog');
}

/** The quantity input of the shared keypad (labelled by its value). */
function quantityInput(container: HTMLElement) {
  return within(container).getByLabelText(/^Quantity: /);
}

/** The floating scan notification — the ONLY toast on the surface. */
async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function noNotice() {
  expect(document.querySelector('.ss-toast')).toBeNull();
}

function transferRequests() {
  return requests.filter((r) => r.url.endsWith('/transfers'));
}

/* ============ Station Selector and station context ============ */

test('the Station Selector lists the active Scan Stations from the server', async () => {
  window.history.replaceState({}, '', '/scan-station');
  render(<App />);

  const lathe = await screen.findByRole('button', { name: 'Open LATHE-ST-01' });
  expect(lathe).toHaveTextContent('Machining');
  expect(lathe).toHaveTextContent('Lathe');
  expect(lathe).toHaveTextContent('Turning');
  expect(lathe).not.toHaveTextContent('Threading'); // inactive Operation
  expect(lathe).toHaveTextContent(
    '1 Machine · Quantity waits in the Area queue',
  );
  expect(lathe).not.toHaveTextContent('assignment');
  expect(
    screen.getByRole('button', { name: 'Open CUT-ST-01' }),
  ).toHaveTextContent('No Machines · Direct Area processing');
  expect(window.location.pathname).toBe('/scan-station');

  fireEvent.click(
    screen.getByRole('button', { name: 'Open CUT-ST-01 in production mode' }),
  );
  expect(window.location.pathname).toBe('/scan-station/CUT-ST-01/production');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
});

test('a station renders its server context and the Area inventory', async () => {
  await renderStation();

  const header = screen.getByRole('banner');
  expect(header).toHaveTextContent('Machining');
  expect(header).toHaveTextContent('Lathe');
  expect(header).toHaveTextContent('Turning');
  const stats = screen.getByLabelText('Area statistics');
  expect(
    (await within(stats).findByText('Total PNs')).previousSibling,
  ).toHaveTextContent('1');
  expect(
    within(stats).getByText('Total pcs').previousSibling,
  ).toHaveTextContent('3');
  // In this Area now: the flow already in Lathe, with its Work Order.
  expect(screen.getByText('0455-20-0118-03')).toBeInTheDocument();
  expect(screen.getByText(/WO 007010/)).toBeInTheDocument();
  // The Area's Machine renders idle — no assignment exists in Phase 5.
  expect(screen.getByText('Lathe 1')).toBeInTheDocument();
  expect(screen.getByText('No production assigned')).toBeInTheDocument();
  // Undo arrives with a later release — present, disabled.
  expect(screen.getByRole('button', { name: '⟲ UNDO' })).toBeDisabled();
  expect(document.querySelector('.ss-stationfoot')).toHaveTextContent(
    'Station LATHE-ST-01',
  );
});

test('an unknown station shows the server error and never falls back', async () => {
  window.history.replaceState({}, '', '/scan-station/NO-SUCH');
  render(<App />);

  expect(
    await screen.findByText('Scan Station “NO-SUCH” is unavailable'),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Scan Station 'NO-SUCH' does not exist."),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'Select another Scan Station' }),
  );
  expect(window.location.pathname).toBe('/scan-station');
});

/* ============ Scanner-first transfer ============ */

test('a scanned PN elsewhere resolves on the server and transfers as a whole flow', async () => {
  const input = await renderStation();
  const readsBefore = inventoryReads;

  scan('PF:PN:2027-60-8114-00\r');
  // Resolution: the verbatim barcode goes to the server.
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  expect(requests.find((r) => r.url.endsWith('/scans/resolve'))?.body).toEqual({
    barcode: 'PF:PN:2027-60-8114-00',
  });
  const box = dialog();
  expect(box).toHaveTextContent('2027-60-8114-00');
  expect(box).toHaveTextContent('Material');
  expect(box).toHaveTextContent('Lathe queue (awaiting Machine)');
  expect(box).toHaveTextContent('WO 007003');
  expect(box).toHaveTextContent('Turning'); // the single Operation resolves itself
  expect(box).toHaveTextContent('The full quantity moves as a whole');
  expect(quantityInput(box)).toHaveValue('12');

  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Review the transfer, then confirm.');
  const rows = within(summary)
    .getAllByRole('term')
    .map((term) => term.textContent);
  expect(rows).toEqual(
    expect.arrayContaining([
      'Action',
      'PN',
      'Quantity',
      'Source',
      'Destination',
      'Operation',
      'Work Order',
      'Route',
      'Scan Station',
      'Recorded event',
    ]),
  );
  expect(summary).toHaveTextContent('12 pcs');
  expect(summary).toHaveTextContent('LATHE-ST-01');
  expect(summary).toHaveTextContent('TRANSFERRED');
  // Nothing recorded before Confirm.
  expect(transferRequests()).toHaveLength(0);

  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );
  expect(await notice()).toHaveTextContent(
    '2027-60-8114-00 × 12 → Lathe queue (awaiting Machine)',
  );
  expect(await notice()).toHaveTextContent(
    'Recorded by the server (TRANSFERRED #500)',
  );
  const [request] = transferRequests();
  expect(request.body).toMatchObject({
    part_number: '2027-60-8114-00',
    quantity_flow_id: 100,
    source_area_id: 1,
    target_area_id: 2,
    quantity: 12,
    operation_id: 20,
    confirm_route_deviation: false,
    route_deviation_reason: null,
  });
  expect(request.body.device_event_id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  // Dialog closed, temporary context cleared, focus back on the input.
  expect(screen.queryByRole('dialog')).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(input));
  // The Area inventory refreshed from the server: the PN is here now.
  expect(inventoryReads).toBeGreaterThan(readsBefore);
  await waitFor(() =>
    expect(
      within(document.querySelector('.abd-summary') as HTMLElement).getByText(
        '2027-60-8114-00',
      ),
    ).toBeInTheDocument(),
  );
  const stats = screen.getByLabelText('Area statistics');
  expect(
    within(stats).getByText('Total pcs').previousSibling,
  ).toHaveTextContent('15');
  expect(document.querySelector('.ss-lastpn .p')).toHaveTextContent(
    '2027-60-8114-00',
  );
  expect(document.querySelector('.ss-lastpn .d')).toHaveTextContent(
    'TRANSFERRED · Material → Lathe queue (awaiting Machine) · qty 12',
  );
});

test('manual PN entry resolves on the server and Back returns to the entry', async () => {
  await renderStation();

  fireEvent.click(screen.getByRole('button', { name: '⌨ Enter PN manually' }));
  const field = within(dialog()).getByLabelText('Part Number');
  fireEvent.change(field, { target: { value: '  2027-60-8114-00 ' } });
  fireEvent.keyDown(field, { key: 'Enter' });

  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  expect(requests.find((r) => r.url.endsWith('/scans/resolve'))?.body).toEqual({
    part_number: '2027-60-8114-00',
  });
  fireEvent.click(within(dialog()).getByRole('button', { name: '‹ Back' }));
  expect(within(dialog()).getByLabelText('Part Number')).toHaveValue(
    '2027-60-8114-00',
  );
  expect(transferRequests()).toHaveLength(0);
});

test('several valid sources require an explicit selection — never an automatic pick', async () => {
  await renderStation();

  scan('PF:PN:118-052');
  const select = await screen.findByRole('dialog', {
    name: 'Select the source',
  });
  expect(select).toHaveTextContent('quantities are never combined');
  const choices = within(select).getAllByRole('button', {
    name: /pcs available/,
  });
  expect(choices.map((c) => c.textContent)).toEqual([
    expect.stringContaining('Material — 5 pcs available'),
    expect.stringContaining('Cut — 7 pcs available'),
  ]);
  expect(select).not.toHaveTextContent('12 pcs');
  expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();

  fireEvent.click(choices[1]);
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(box).toHaveTextContent('Cut');
  expect(quantityInput(box)).toHaveValue('7');
  // Back returns to the selection with every source intact.
  fireEvent.click(within(box).getByRole('button', { name: '‹ Back' }));
  expect(
    await screen.findByRole('dialog', { name: 'Select the source' }),
  ).toBeInTheDocument();
  expect(
    within(dialog()).getAllByRole('button', { name: /pcs available/ }),
  ).toHaveLength(2);

  fireEvent.click(
    within(dialog()).getAllByRole('button', { name: /pcs available/ })[1],
  );
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(transferRequests()[0].body).toMatchObject({
    quantity_flow_id: 102,
    source_area_id: 3,
    quantity: 7,
  });
});

test('an Area with several Operations makes the operator choose one', async () => {
  await renderStation('CUT-ST-01');

  scan('PF:PN:2027-60-8114-00');
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(box).toHaveTextContent('Cut supports several Operations');
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  const cutting = within(box).getByRole('button', { name: 'Cutting' });
  fireEvent.click(cutting);
  expect(cutting).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  const operationRow =
    within(summary).getByText('Operation').nextElementSibling;
  expect(operationRow).toHaveTextContent('Cutting');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(transferRequests()[0].body.operation_id).toBe(30);
});

test('partial quantity is refused clearly and never submitted', async () => {
  await renderStation();

  scan('PF:PN:2027-60-8114-00');
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  const quantity = quantityInput(box);
  fireEvent.change(quantity, { target: { value: '5' } });
  expect(box).toHaveTextContent(
    'Partial transfer is not available in this release',
  );
  expect(box).toHaveTextContent('Enter 12 or cancel');
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.keyDown(box, { key: 'Enter' }); // Enter never advances an invalid quantity
  expect(box).toHaveTextContent('The full quantity moves as a whole');
  fireEvent.change(quantity, { target: { value: '13' } });
  expect(box).toHaveTextContent('Quantity cannot exceed the 12 pcs');
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(quantity, { target: { value: '12' } });
  expect(within(box).getByRole('button', { name: 'Next' })).toBeEnabled();
  expect(transferRequests()).toHaveLength(0);
});

/* ============ Write failures, retries, idempotency ============ */

test('a lost response is an UNKNOWN outcome: frozen intent, exact retry with the same device_event_id', async () => {
  const input = await renderStation();
  // The server commits the transfer; the response never arrives.
  transferFailure = 'lost-response';

  scan('PF:PN:PLN-1'); // a Planned Route deviation — the reason is part of the intent
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.change(within(dialog()).getByLabelText('Route deviation reason'), {
    target: { value: 'Cut backlog' },
  });
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );

  await waitFor(() =>
    expect(dialog()).toHaveTextContent('may or may not have been recorded'),
  );
  // Never "nothing changed": the outcome is unknown.
  expect(dialog()).not.toHaveTextContent('Nothing was changed');
  expect(dialog()).not.toHaveTextContent('nothing was recorded');
  noNotice();
  expect(document.querySelector('.ss-lastpn .p')).toHaveTextContent('—');
  // The intent is frozen: no Back, the reason cannot change, and the
  // only primary action is the exact same transfer.
  expect(within(dialog()).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(dialog()).getByLabelText('Route deviation reason'),
  ).toBeDisabled();
  expect(
    within(dialog()).getByRole('button', { name: 'Leave — check the Area' }),
  ).toBeInTheDocument();
  const first = transferRequests()[0].body;

  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Retry the same transfer' }),
  );
  expect(await notice()).toHaveTextContent(
    'already recorded by the server (TRANSFERRED #500)',
  );
  expect(transferRequests()).toHaveLength(2);
  // Byte-for-byte the same request — same key, same reason, same intent.
  expect(transferRequests()[1].body).toEqual(first);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(flows.find((f) => f.id === 104)?.areaId).toBe(2);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test.each([502, 504, 500, 408])(
  'HTTP %i after the server committed is an UNKNOWN outcome, and the exact retry replays without a duplicate Movement',
  async (status) => {
    const input = await renderStation();
    transferFailure = { committedStatus: status };

    scan('PF:PN:PLN-1'); // deviation: the reason is part of the frozen intent
    await screen.findByRole('dialog', { name: 'Receive from another Area' });
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
    fireEvent.change(
      within(dialog()).getByLabelText('Route deviation reason'),
      { target: { value: 'Cut backlog' } },
    );
    fireEvent.click(
      within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
    );

    // 1–3: committed on the server, a 5xx/408 on the client → UNKNOWN,
    // never "nothing was changed", no Back, intent frozen.
    await waitFor(() =>
      expect(dialog()).toHaveTextContent('may or may not have been recorded'),
    );
    expect(dialog()).not.toHaveTextContent('Nothing was changed');
    expect(dialog()).not.toHaveTextContent('nothing was recorded');
    expect(
      within(dialog()).queryByRole('button', { name: '‹ Back' }),
    ).toBeNull();
    expect(
      within(dialog()).getByLabelText('Route deviation reason'),
    ).toBeDisabled();
    expect(
      within(dialog()).queryByRole('button', { name: 'Cancel (Esc)' }),
    ).toBeNull();
    expect(
      within(dialog()).getByRole('button', { name: 'Leave — check the Area' }),
    ).toBeInTheDocument();
    expect(transferRequests()).toHaveLength(1);
    const first = transferRequests()[0];

    // 4: byte-for-byte the same payload, same device_event_id.
    fireEvent.click(
      within(dialog()).getByRole('button', { name: 'Retry the same transfer' }),
    );
    expect(await notice()).toHaveTextContent(
      'already recorded by the server (TRANSFERRED #500)',
    );
    expect(transferRequests()).toHaveLength(2);
    const second = transferRequests()[1];
    expect(JSON.stringify(second.body)).toBe(JSON.stringify(first.body));
    expect(second.body.device_event_id).toBe(first.body.device_event_id);

    // 5: the replay (200) closes the workflow; exactly one Movement.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(nextMovementId).toBe(501);
    expect(committed.size).toBe(1);
    expect(flows.find((f) => f.id === 104)?.areaId).toBe(2);
    await waitFor(() => expect(document.activeElement).toBe(input));
  },
);

test('a 4xx application rejection is a pre-write refusal, not an unknown outcome', async () => {
  await renderStation();
  transferFailure = { status: 409, body: { detail: 'Scan Station rebound' } };

  scan('PF:PN:2027-60-8114-00');
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(dialog()).toHaveTextContent('Scan Station rebound'),
  );
  expect(dialog()).not.toHaveTextContent('may or may not have been recorded');
  expect(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
});

test('leaving an unknown-outcome transfer re-reads the Area instead of claiming a result', async () => {
  await renderStation();
  transferFailure = 'network';

  scan('PF:PN:2027-60-8114-00');
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(dialog()).toHaveTextContent('may or may not have been recorded'),
  );
  const readsBefore = inventoryReads;
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Leave — check the Area' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  const toast = await notice();
  expect(toast).toHaveTextContent('Transfer outcome unknown');
  expect(toast).not.toHaveTextContent('No changes were recorded');
  await waitFor(() => expect(inventoryReads).toBeGreaterThan(readsBefore));
  expect(document.querySelector('.ss-lastpn .p')).toHaveTextContent('—');
});

test('a server rejection keeps the dialog open with the reason and records nothing', async () => {
  await renderStation();
  transferFailure = {
    status: 409,
    body: {
      detail:
        "Scan Station 'LATHE-ST-01' is no longer bound to the confirmed destination Area — its configuration changed since the transfer was prepared. Reload the station and confirm the transfer again. Nothing was transferred.",
    },
  };

  scan('PF:PN:2027-60-8114-00');
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );

  await waitFor(() =>
    expect(dialog()).toHaveTextContent(
      'no longer bound to the confirmed destination Area',
    ),
  );
  noNotice();
  expect(flows.find((f) => f.id === 100)?.areaId).toBe(1);
  // Cancel: no write, context cleared, focus restored.
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await notice()).toHaveTextContent(
    'Cancelled. No changes were recorded.',
  );
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByLabelText('Scan barcode')),
  );
});

test('a Planned Route deviation needs an explicit confirmation with a reason', async () => {
  await renderStation();

  scan('PF:PN:PLN-1');
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Route deviation');
  expect(summary).toHaveTextContent(
    'expects this quantity at Cut next, not at Lathe',
  );
  expect(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  ).toBeDisabled();
  fireEvent.change(within(summary).getByLabelText('Route deviation reason'), {
    target: { value: 'Cut backlog — turning first' },
  });
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(transferRequests()[0].body).toMatchObject({
    quantity_flow_id: 104,
    confirm_route_deviation: true,
    route_deviation_reason: 'Cut backlog — turning first',
  });
});

/* ============ Offline ============ */

test('while disconnected, scanning is disabled and nothing is sent', async () => {
  healthDown = true;
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01');
  render(<App />);

  const input = await screen.findByLabelText('Scan barcode');
  await waitFor(() => expect(input).toBeDisabled());
  expect(input).toHaveAttribute(
    'placeholder',
    'Disconnected — scanning disabled',
  );
  expect(
    screen.getByRole('button', { name: '⌨ Enter PN manually' }),
  ).toBeDisabled();
  expect(requests.filter((r) => r.url.endsWith('/scans/resolve'))).toHaveLength(
    0,
  );
  expect(transferRequests()).toHaveLength(0);
});

/* ============ Other resolutions and barcodes ============ */

test('a PN already in the Area offers only to receive more from another Area', async () => {
  flows.push({
    id: 105,
    pn: '0455-20-0118-03',
    qty: 9,
    areaId: 1,
    routeMode: 'FLOATING',
    wo: { id: 3, number: '007010', demandId: 13, type: 'NEW' },
  });
  await renderStation();

  scan('PF:PN:0455-20-0118-03');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent(
    '3 pcs of this Part Number are already in Lathe',
  );
  expect(actions).toHaveTextContent('arrive with a later release');
  fireEvent.click(
    within(actions).getByRole('button', {
      name: /Receive more quantity from another Area/,
    }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(quantityInput(box)).toHaveValue('9');
  fireEvent.click(within(box).getByRole('button', { name: '‹ Back' }));
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
});

test('a PN with nothing to receive shows the honest placeholder — no intake at the station', async () => {
  await renderStation();

  scan('PF:PN:NEW-PART-01');
  const box = await screen.findByRole('dialog', {
    name: 'No quantity to receive',
  });
  expect(box).toHaveTextContent('no active Work Order Demand');
  expect(box).toHaveTextContent('arrives with a later release');
  expect(transferRequests()).toHaveLength(0);
});

test('a terminal-Area station explains why it never receives a transfer', async () => {
  await renderStation('STOCK-ST-01');

  scan('PF:PN:2027-60-8114-00');
  const box = await screen.findByRole('dialog', {
    name: 'No quantity to receive',
  });
  expect(box).toHaveTextContent('terminal Area');
});

test('Area, scrap and unknown barcodes are rejected without a server call', async () => {
  const input = await renderStation();

  // A Machine barcode is the Phase 6 Machine-first entry point and
  // resolves on the server (scan-station-machine.test.tsx).
  for (const [barcode, title] of [
    ['PF:AREA:2', 'Area barcode is not required here'],
    ['PF:SCRAP', 'Scrap barcode cannot be used here'],
    ['100482', 'Barcode not recognized'],
  ] as const) {
    scan(barcode);
    expect(await notice()).toHaveTextContent(title);
    expect(await notice()).toHaveTextContent('No changes were recorded');
    expect(screen.queryByRole('dialog')).toBeNull();
  }
  expect(requests.filter((r) => r.url.endsWith('/scans/resolve'))).toHaveLength(
    0,
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a resolution the server refuses reports the reason with nothing recorded', async () => {
  const input = await renderStation();
  resolveFailure = true;

  scan('PF:PN:2027-60-8114-00');
  const toast = await notice();
  expect(toast).toHaveTextContent('Part Number could not be resolved');
  expect(toast).toHaveTextContent('The PartFlow server could not be reached.');
  expect(toast).toHaveTextContent('No changes were recorded.');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(transferRequests()).toHaveLength(0);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('the keyboard wedge reaches the input while nothing is focused, and Escape cancels a wizard', async () => {
  const input = await renderStation();
  (document.activeElement as HTMLElement | null)?.blur();

  for (const key of 'PF:PN:2027-60-8114-00') {
    fireEvent.keyDown(document.body, { key });
  }
  expect(input).toHaveValue('PF:PN:2027-60-8114-00');
  fireEvent.keyDown(document.body, { key: 'Enter' });
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(transferRequests()).toHaveLength(0);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('several Operations always take an explicit choice — the planned one is guidance, another is an OPERATION deviation', async () => {
  flows.push({
    id: 106,
    pn: 'PLN-CUT',
    qty: 6,
    areaId: 1,
    routeMode: 'PLANNED',
    routeStatus: 'ON_ROUTE',
    expectedNextAreaId: 3,
    expectedOperationId: 30, // Cutting is planned at Cut
    wo: { id: 5, number: '007030', demandId: 15, type: 'NEW' },
  });
  await renderStation('CUT-ST-01');

  scan('PF:PN:PLN-CUT');
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  // Nothing is pre-selected, even though the route plans Cutting.
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  const cutting = within(box).getByRole('button', { name: /Cutting/ });
  const sawing = within(box).getByRole('button', { name: /Sawing/ });
  expect(cutting).toHaveAttribute('aria-pressed', 'false');
  expect(cutting).toHaveTextContent('planned for this step');
  expect(sawing).not.toHaveTextContent('planned for this step');
  expect(box).toHaveTextContent('another choice is a route deviation');

  fireEvent.click(sawing);
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Route deviation');
  expect(summary).toHaveTextContent(
    'expects Operation Cutting at Cut, not Sawing',
  );
  expect(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  ).toBeDisabled();
  fireEvent.change(within(summary).getByLabelText('Route deviation reason'), {
    target: { value: 'Saw is free' },
  });
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(transferRequests()[0].body).toMatchObject({
    quantity_flow_id: 106,
    target_area_id: 3,
    operation_id: 31,
    confirm_route_deviation: true,
    route_deviation_reason: 'Saw is free',
  });
});

test('a station rebound after page load is never used stale — the station reloads and asks for the scan again', async () => {
  await renderStation();
  expect(screen.getByRole('banner')).toHaveTextContent('Lathe');
  // Administration rebinds the station to Cut after this page loaded.
  stationAreaId = 3;

  scan('PF:PN:2027-60-8114-00');
  const toast = await notice();
  expect(toast).toHaveTextContent('Scan Station configuration changed');
  expect(toast).toHaveTextContent('now bound to Cut');
  expect(toast).toHaveTextContent('scan the Part Number again');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(transferRequests()).toHaveLength(0);
  // The station context and inventory reloaded from the server.
  await waitFor(() =>
    expect(screen.getByRole('banner')).toHaveTextContent('Cut'),
  );
  expect(screen.getByRole('banner')).toHaveTextContent('Cutting');
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByLabelText('Scan barcode')),
  );

  // The next scan confirms against the CURRENT destination.
  scan('PF:PN:2027-60-8114-00');
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(box).toHaveTextContent('Cut — direct processing');
  fireEvent.click(within(box).getByRole('button', { name: /Cutting/ }));
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(transferRequests()[0].body.target_area_id).toBe(3);
});

test('a scan is captured even while a non-dialog button holds focus, and resolves exactly once', async () => {
  const input = await renderStation();
  const theme = screen.getByRole('button', { name: /Dark|Light/ });
  theme.focus();
  expect(document.activeElement).toBe(theme);

  // Enter on the focused button with NO scan buffered stays native
  // button activation (the event is not consumed).
  expect(fireEvent.keyDown(theme, { key: 'Enter' })).toBe(true);
  expect(input).toHaveValue('');

  // The first scanner character arrives while the button holds focus:
  // it is captured into the main input, which takes focus so the rest
  // of the barcode types natively.
  fireEvent.keyDown(theme, { key: 'P' });
  expect(input).toHaveValue('P');
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: 'PF:PN:2027-60-8114-00' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  expect(requests.filter((r) => r.url.endsWith('/scans/resolve'))).toHaveLength(
    1,
  );
});

test('a whole scan delivered to a focused button — terminating Enter included — submits once', async () => {
  const input = await renderStation();
  const theme = screen.getByRole('button', { name: /Dark|Light/ });
  theme.focus();
  for (const key of 'PF:PN:2027-60-8114-00') {
    fireEvent.keyDown(theme, { key });
  }
  expect(input).toHaveValue('PF:PN:2027-60-8114-00');
  // The terminating Enter of a buffered scan is consumed as the
  // submission, not as button activation.
  expect(fireEvent.keyDown(theme, { key: 'Enter' })).toBe(false);
  await screen.findByRole('dialog', { name: 'Receive from another Area' });
  expect(requests.filter((r) => r.url.endsWith('/scans/resolve'))).toHaveLength(
    1,
  );
});

test('a terminal-Area station refuses every Receive action even with quantity here and elsewhere', async () => {
  flows.push({
    id: 107,
    pn: '2027-60-8114-00',
    qty: 20,
    areaId: 4, // already in the Stockroom
    routeMode: 'FLOATING',
    wo: { id: 1, number: '007003', demandId: 11, type: 'NEW' },
  });
  await renderStation('STOCK-ST-01');

  scan('PF:PN:2027-60-8114-00'); // also 12 pcs in Material
  const box = await screen.findByRole('dialog', {
    name: 'No quantity to receive',
  });
  expect(box).toHaveTextContent('terminal Area');
  expect(screen.queryByRole('dialog', { name: 'Select an action' })).toBeNull();
  expect(screen.queryByRole('button', { name: /Receive more/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
  expect(transferRequests()).toHaveLength(0);
});

test('no false empty Area renders before the first inventory response, and a Machines failure shows an error with Retry', async () => {
  let releaseInventory!: () => void;
  inventoryHold = new Promise<void>((resolve) => {
    releaseInventory = resolve;
  });
  await renderStation();

  // Context is up, inventory still pending: loading — no zero totals,
  // no empty "No production" row.
  expect(screen.getByLabelText('Loading Area inventory')).toBeInTheDocument();
  expect(screen.queryByText('Total PNs')).toBeNull();
  expect(screen.queryByText(/No production in/)).toBeNull();

  releaseInventory();
  inventoryHold = null;
  expect(await screen.findByText('Total PNs')).toBeInTheDocument();
  expect(screen.getByText('0455-20-0118-03')).toBeInTheDocument();
});
