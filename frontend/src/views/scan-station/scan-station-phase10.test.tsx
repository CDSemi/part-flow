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

import { App } from '../../App';

// Real Scan Station at a Stockroom station (Phase 10 — GUI_DESIGN §10,
// PROJECT_PROFILE §18) against a fake in-memory `/api` with the
// backend's Phase 10 semantics: at a station bound to a terminal Area
// the PN resolution marks the candidates as stock sources
// (`stock_available`), `POST …/stockings` records the STOCKED arrival
// (partial via the server's split, the flow closing as
// manufacturing-complete), `GET /api/allocations/suggestion` returns
// the canonical suggestion for the stocked quantity and
// `POST /api/allocations` records the confirmed allocation — refusing
// lines that do not add up to the explicit `allocation_quantity` and
// an allocation quantity the available stock no longer covers.
// Covers: full and partial receiving, the explicit source selection,
// the suggestion rows, manual adjustment with Confirm disabled while
// the total differs, the request carrying the stocked quantity as
// `allocation_quantity` (never the whole available stock), the stale
// stock refusal keeping the dialog open with a refreshed suggestion,
// offline write-blocking, the lost-response retry under the same
// device_event_id, leaving the quantity in stock, refresh and focus.

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  stocked?: boolean;
}

interface Demand {
  id: number;
  woId: number;
  woNumber: string;
  received: string;
  due: string | null;
  rank: number | null;
  pn: string;
  requested: number;
  allocated: number;
}

const AREAS = [
  { id: 1, name: 'Material', color: '#8899aa', terminal: false },
  { id: 3, name: 'Cut', color: '#33aa66', terminal: false },
  { id: 4, name: 'Stockroom', color: '#999999', terminal: true },
];
const OPERATIONS = [
  { id: 10, area_id: 1, code: 'RECEIVING', name: 'Receiving' },
  { id: 30, area_id: 3, code: 'CUTTING', name: 'Cutting' },
  { id: 40, area_id: 4, code: 'STORE', name: 'Store' },
];
const STATIONS = [
  { station_id: 'MAT-ST-01', area_id: 1 },
  { station_id: 'STOCK-ST-01', area_id: 4 },
];

let flows: Flow[];
let demands: Demand[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextId: number;
let stockFailure: null | 'lost-response' | { status: number; body: unknown };
let allocationFailure:
  null | 'lost-response' | { status: number; body: unknown };
let healthDown: boolean;
/** Active allocation not tracked per line: the PN-level stock taken by
 * "someone else" meanwhile (the stale-stock scenario). */
let allocatedElsewhere: Map<string, number>;

function areaRef(areaId: number) {
  const area = AREAS.find((a) => a.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: area.terminal,
  };
}

function operationRef(op: (typeof OPERATIONS)[number]) {
  return { id: op.id, code: op.code, name: op.name, is_external: false };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: {
      ...operationRef(OPERATIONS.find((o) => o.area_id === flow.areaId)!),
      is_active: true,
    },
    processing_state: 'PROCESSING',
    machine_id: null,
    available_actions: ['DONE', 'TRANSFER', 'SCRAP'],
    work_order: null,
  };
}

function inventoryLines(items: Flow[]) {
  const byPn = new Map<string, Flow[]>();
  for (const flow of items)
    byPn.set(flow.pn, [...(byPn.get(flow.pn) ?? []), flow]);
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    total_quantity: group.reduce((s, f) => s + f.qty, 0),
    flows: group.map(flowWire),
  }));
}

function stockedOf(pn: string) {
  return flows
    .filter((f) => f.pn === pn && f.stocked)
    .reduce((s, f) => s + f.qty, 0);
}

function allocatedOf(pn: string) {
  return (
    demands.filter((d) => d.pn === pn).reduce((s, d) => s + d.allocated, 0) +
    (allocatedElsewhere.get(pn) ?? 0)
  );
}

/** The canonical demand ordering (PROJECT_PROFILE §18). */
function outstanding(pn: string): Demand[] {
  return demands
    .filter((d) => d.pn === pn && d.requested - d.allocated > 0)
    .sort((a, b) => {
      const rank = (a.rank ?? Infinity) - (b.rank ?? Infinity);
      if (rank) return rank;
      if (a.due !== b.due) {
        if (a.due === null) return 1;
        if (b.due === null) return -1;
        return a.due < b.due ? -1 : 1;
      }
      if (a.due === null && a.received !== b.received) {
        return a.received < b.received ? -1 : 1;
      }
      return a.id - b.id;
    });
}

function suggestion(pn: string, wanted: number | null) {
  const stocked = stockedOf(pn);
  const allocated = allocatedOf(pn);
  const available = stocked - allocated;
  const quantity = Math.min(wanted ?? available, Math.max(available, 0));
  let remaining = quantity;
  const lines = outstanding(pn).map((d) => {
    const shortage = d.requested - d.allocated;
    const proposed = Math.min(shortage, remaining);
    remaining -= proposed;
    return {
      work_order_id: d.woId,
      work_order_number: d.woNumber,
      received_date: d.received,
      work_order_demand_id: d.id,
      priority_rank: d.rank,
      due_date: d.due,
      requested_quantity: d.requested,
      previously_allocated_quantity: d.allocated,
      remaining_shortage: shortage,
      proposed_quantity: proposed,
    };
  });
  return {
    part_number: pn,
    quantity,
    stocked_quantity: stocked,
    active_allocated_quantity: allocated,
    available_stocked_quantity: available,
    proposed_total: quantity - remaining,
    unallocated_quantity: remaining,
    lines,
  };
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
        department_id: 1,
        barcode_value: `PF:AREA:${a.id}`,
        icon_url: null,
        is_active: true,
      })),
    );
  }
  if (url === '/api/departments')
    return json([{ id: 1, name: 'Machining', is_active: true }]);
  if (url === '/api/operations') {
    return json(
      OPERATIONS.map((o) => ({
        ...operationRef(o),
        area_id: o.area_id,
        description: null,
        default_expected_duration: null,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/machines') return json([]);
  const context = /^\/api\/scan-stations\/([^/]+)\/context$/.exec(url);
  if (context) {
    const station = STATIONS.find((s) => s.station_id === context[1])!;
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Machining' },
      area: areaRef(station.area_id),
      operations: OPERATIONS.filter((o) => o.area_id === station.area_id).map(
        operationRef,
      ),
      has_machines: false,
    });
  }
  const inventory = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inventory) {
    const areaId = Number(inventory[1]);
    const here = flows.filter((f) => f.areaId === areaId && !f.stocked);
    const lines = inventoryLines(here);
    return json({
      area: areaRef(areaId),
      has_machines: false,
      lines,
      total_part_numbers: lines.length,
      total_quantity: here.reduce((s, f) => s + f.qty, 0),
      queued: [],
      queued_quantity: 0,
      machines: [],
      on_machine_quantity: 0,
      processing: lines,
      processing_quantity: here.reduce((s, f) => s + f.qty, 0),
      finished: [],
      finished_quantity: 0,
    });
  }
  const resolve = /^\/api\/scan-stations\/([^/]+)\/scans\/resolve$/.exec(url);
  if (resolve && method === 'POST') {
    const station = STATIONS.find((s) => s.station_id === resolve[1])!;
    const input = body as { barcode?: string; part_number?: string };
    const pn = (
      input.barcode
        ? input.barcode.trim().slice('PF:PN:'.length)
        : input.part_number!
    )
      .trim()
      .toUpperCase();
    const active = flows.filter((f) => f.pn === pn && !f.stocked);
    const inArea = active.filter((f) => f.areaId === station.area_id);
    const candidates = active.filter((f) => f.areaId !== station.area_id);
    const terminal = AREAS.find((a) => a.id === station.area_id)!.terminal;
    const ops = OPERATIONS.filter((o) => o.area_id === station.area_id);
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
        suggested_operation_id: ops.length === 1 ? ops[0].id : null,
        repair_available: false,
      })),
      operations: ops.map(operationRef),
      has_active_demand: demands.some((d) => d.pn === pn),
      transfer_blocked_reason: terminal
        ? "Area 'Stockroom' is a terminal Area. Receiving finished quantity there is the Stockroom workflow, not a transfer."
        : null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
      combine_groups: [],
      scrapped_quantity: 0,
      stocked_quantity: stockedOf(pn),
      available_stocked_quantity: stockedOf(pn) - allocatedOf(pn),
      stock_available: terminal && candidates.length > 0,
    });
  }
  if (/^\/api\/scan-stations\/[^/]+\/transfers$/.test(url)) {
    return json({ detail: 'A terminal Area never receives a transfer.' }, 409);
  }
  const stocking = /^\/api\/scan-stations\/([^/]+)\/stockings$/.exec(url);
  if (stocking && method === 'POST') {
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      source_area_id: number;
      target_area_id: number;
      quantity: number;
      device_event_id: string;
      repair?: unknown;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    if (stockFailure && stockFailure !== 'lost-response') {
      return json(stockFailure.body, stockFailure.status);
    }
    if ('repair' in request)
      return json({ detail: 'unexpected field repair' }, 422);
    const flow = flows.find(
      (f) => f.id === request.quantity_flow_id && !f.stocked,
    );
    if (!flow || flow.areaId !== request.source_area_id) {
      return json(
        { detail: 'Quantity Flow is no longer in the selected source Area.' },
        409,
      );
    }
    if (request.quantity > flow.qty) return json({ detail: 'exceeds' }, 422);
    const partial = request.quantity < flow.qty;
    let stockedFlow = flow;
    let remainder: Flow | null = null;
    if (partial) {
      flow.stocked = true; // consumed by the split (source closes)
      stockedFlow = {
        id: nextId++,
        pn: flow.pn,
        qty: request.quantity,
        areaId: 4,
        stocked: true,
      };
      remainder = {
        id: nextId++,
        pn: flow.pn,
        qty: flow.qty - request.quantity,
        areaId: flow.areaId,
      };
      flows.push(stockedFlow, remainder);
      flow.qty = 0;
    } else {
      flow.stocked = true;
      flow.areaId = 4;
    }
    const result = {
      movement_id: nextId++,
      movement_type: 'STOCKED',
      quantity_flow_id: stockedFlow.id,
      part_number: flow.pn,
      quantity: request.quantity,
      from_area_id: request.source_area_id,
      to_area_id: 4,
      operation_id: 40,
      station_id: 'STOCK-ST-01',
      assigned_route_step_id: null,
      route_deviation: null,
      completed_movement_id: nextId++,
      completed_machine_id: null,
      source_quantity_flow_id: partial ? flow.id : null,
      remainder_quantity_flow_id: remainder ? remainder.id : null,
      remainder_quantity: remainder ? remainder.qty : null,
      movement_reason: null,
      reason: null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-09-01T12:00:00Z',
    };
    committed.set(request.device_event_id, result);
    if (stockFailure === 'lost-response') {
      stockFailure = null;
      throw new TypeError('Failed to fetch');
    }
    return json(result, 201);
  }
  const preview = /\/undo-preview\/([^/]+)$/.exec(url);
  if (preview && method === 'GET') {
    // The server's verdict on a stocked command: never undoable
    // (PROJECT_PROFILE §32 open decision 1).
    const eventId = decodeURIComponent(preview[1]);
    const record = committed.get(eventId) as
      | { part_number: string; quantity: number; occurred_at: string }
      | undefined;
    if (!record) {
      return json({ detail: `No production event under '${eventId}'.` }, 404);
    }
    return json({
      reverses_device_event_id: eventId,
      station_id: 'STOCK-ST-01',
      kind: 'STOCK',
      part_number: record.part_number,
      quantity: record.quantity,
      occurred_at: record.occurred_at,
      eligible: false,
      ineligible_reason:
        'This action stocked the quantity at the Stockroom: it is manufacturing-complete and may already be allocated to Work Order Demand. Returning stocked quantity to production is not supported; adjust the allocation instead.',
      movements: [],
      restored: [],
    });
  }
  const suggest = /^\/api\/allocations\/suggestion\?(.*)$/.exec(url);
  if (suggest) {
    const params = new URLSearchParams(suggest[1]);
    const pn = params.get('part_number')!;
    const quantity = params.get('quantity');
    return json(suggestion(pn, quantity === null ? null : Number(quantity)));
  }
  if (url === '/api/allocations' && method === 'POST') {
    const request = body as {
      part_number: string;
      allocation_quantity: number;
      lines: { work_order_demand_id: number; quantity: number }[];
      station_id?: string;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    if (allocationFailure && allocationFailure !== 'lost-response') {
      return json(allocationFailure.body, allocationFailure.status);
    }
    const total = request.lines.reduce((s, l) => s + l.quantity, 0);
    if (total !== request.allocation_quantity) {
      return json(
        {
          detail:
            'The allocated lines must equal the quantity being allocated.',
        },
        422,
      );
    }
    const available =
      stockedOf(request.part_number) - allocatedOf(request.part_number);
    if (request.allocation_quantity > available) {
      return json(
        {
          detail: `Only ${available} pcs of Part Number '${request.part_number}' are available in stock; ${request.allocation_quantity} pcs cannot be allocated — the available quantity changed since the allocation was prepared. Reload the suggestion and confirm again. Nothing was allocated.`,
        },
        409,
      );
    }
    for (const line of request.lines) {
      const demand = demands.find((d) => d.id === line.work_order_demand_id)!;
      if (line.quantity > demand.requested - demand.allocated) {
        return json(
          { detail: 'Allocation never exceeds the requested quantity.' },
          409,
        );
      }
    }
    const completedIds: number[] = [];
    for (const line of request.lines) {
      const demand = demands.find((d) => d.id === line.work_order_demand_id)!;
      demand.allocated += line.quantity;
      if (
        demands
          .filter((d) => d.woId === demand.woId)
          .every((d) => d.allocated >= d.requested)
      ) {
        completedIds.push(demand.woId);
      }
    }
    const result = {
      kind: 'ALLOCATE',
      part_number: request.part_number,
      allocation_quantity: request.allocation_quantity,
      rows: request.lines.map((line, index) => ({
        allocation_id: nextId++,
        work_order_demand_id: line.work_order_demand_id,
        work_order_id: demands.find((d) => d.id === line.work_order_demand_id)!
          .woId,
        part_number: request.part_number,
        quantity: line.quantity,
        source: request.station_id ? 'STOCKROOM' : 'MANAGEMENT',
        is_manual_override: false,
        allocation_reason: null,
        reverses_allocation_id: null,
        station_id: request.station_id ?? null,
        actor_reference: null,
        allocated_at: '2026-09-01T12:00:00Z',
        command_sequence: index + 1,
      })),
      completed_work_order_ids: completedIds,
      reopened_work_order_ids: [],
      device_event_id: request.device_event_id,
    };
    committed.set(request.device_event_id, result);
    if (allocationFailure === 'lost-response') {
      allocationFailure = null;
      throw new TypeError('Failed to fetch');
    }
    return json(result, 201);
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    { id: 100, pn: 'PN-A', qty: 12, areaId: 1 },
    { id: 101, pn: 'PN-B', qty: 5, areaId: 1 },
    { id: 102, pn: 'PN-B', qty: 7, areaId: 3 },
    // Already in stock: the 2 pcs previously allocated to 007010.
    { id: 103, pn: 'PN-A', qty: 2, areaId: 4, stocked: true },
  ];
  demands = [
    // Canonical order for PN-A: Hot #1 (007020) first, then the dated
    // 007003, then the undated 007010 by received date.
    {
      id: 11,
      woId: 1,
      woNumber: '007003',
      received: '2026-08-01',
      due: '2026-10-01',
      rank: null,
      pn: 'PN-A',
      requested: 5,
      allocated: 0,
    },
    {
      id: 12,
      woId: 2,
      woNumber: '007010',
      received: '2026-08-05',
      due: null,
      rank: null,
      pn: 'PN-A',
      requested: 10,
      allocated: 2,
    },
    {
      id: 13,
      woId: 3,
      woNumber: '007020',
      received: '2026-08-10',
      due: null,
      rank: 1,
      pn: 'PN-A',
      requested: 4,
      allocated: 0,
    },
    {
      id: 14,
      woId: 4,
      woNumber: '007030',
      received: '2026-08-11',
      due: null,
      rank: null,
      pn: 'PN-B',
      requested: 20,
      allocated: 0,
    },
  ];
  committed = new Map();
  requests = [];
  nextId = 500;
  stockFailure = null;
  allocationFailure = null;
  healthDown = false;
  allocatedElsewhere = new Map();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      return Promise.resolve(handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId = 'STOCK-ST-01') {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText(/In this Area now/);
  return input;
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function quantityInput(container: HTMLElement) {
  return within(container).getByLabelText(/^Quantity: /);
}

function stockingRequests() {
  return requests.filter((r) => r.url.endsWith('/stockings'));
}

function allocationRequests() {
  return requests.filter(
    (r) => r.url === '/api/allocations' && r.method === 'POST',
  );
}

function suggestionRequests() {
  return requests.filter((r) =>
    r.url.startsWith('/api/allocations/suggestion'),
  );
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

/** Scan PN-A at the Stockroom, confirm `quantity` pcs, wait for the
 * allocation dialog to load its suggestion. */
async function stockPnA(quantity = 12) {
  scan('PF:PN:PN-A');
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  fireEvent.change(quantityInput(box), { target: { value: String(quantity) } });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm stocking' }),
  );
  const allocation = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await within(allocation).findByRole('table', {
    name: 'Allocation suggestion',
  });
  return allocation;
}

function lineQuantity(dialog: HTMLElement, woNumber: string) {
  return within(dialog).getByLabelText(
    `Quantity for ${woNumber}`,
  ) as HTMLInputElement;
}

/* ============ Receiving ============ */

test('scanning at the Stockroom station stocks the whole quantity and opens the allocation with the server suggestion', async () => {
  await renderStation();
  scan('PF:PN:PN-A');

  // The Stockroom arrival — never the ordinary transfer dialog.
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  expect(box).toHaveTextContent('Material');
  expect(box).toHaveTextContent('manufacturing-complete');
  expect(quantityInput(box)).toHaveValue('12');
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  const summary = screen.getByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  expect(summary).toHaveTextContent('Receive into Stockroom');
  expect(summary).toHaveTextContent(
    'AREA_COMPLETED, then STOCKED (one command)',
  );
  expect(summary).toHaveTextContent('After stocking');
  // Nothing recorded before Confirm.
  expect(stockingRequests()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm stocking' }),
  );

  // The STOCKED write went to the stockings endpoint with the stocked
  // quantity and no Repair intent; the allocation dialog opens ONLY
  // after the server confirmed it, with the suggestion for exactly the
  // stocked quantity.
  const allocation = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  expect(stockingRequests()).toHaveLength(1);
  expect(stockingRequests()[0].body).toMatchObject({
    part_number: 'PN-A',
    quantity_flow_id: 100,
    source_area_id: 1,
    target_area_id: 4,
    quantity: 12,
  });
  expect(stockingRequests()[0].body).not.toHaveProperty('repair');
  expect(requests.filter((r) => r.url.endsWith('/transfers'))).toHaveLength(0);
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 12 stocked');
  expect(toast).toHaveTextContent('STOCKED #');

  await within(allocation).findByRole('table', {
    name: 'Allocation suggestion',
  });
  expect(suggestionRequests()[0].url).toContain('part_number=PN-A');
  expect(suggestionRequests()[0].url).toContain('quantity=12');
  expect(allocation).toHaveTextContent('12 pcs stocked at');
  // Every outstanding line in the canonical order with the §18 columns.
  const rows = within(allocation).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('007020'); // Hot #1
  expect(rows[0]).toHaveTextContent('🔥#1');
  expect(rows[1]).toHaveTextContent('007003'); // dated
  expect(rows[2]).toHaveTextContent('007010'); // undated, previously allocated 2
  // Requested · previously allocated · remaining shortage · proposed.
  expect(
    within(rows[2])
      .getAllByRole('cell')
      .map((c) => c.textContent),
  ).toEqual([
    expect.stringContaining('007010'),
    '10',
    '2',
    '8',
    expect.any(String),
  ]);
  expect(lineQuantity(allocation, '007020')).toHaveValue('4');
  expect(lineQuantity(allocation, '007003')).toHaveValue('5');
  expect(lineQuantity(allocation, '007010')).toHaveValue('3');
  expect(allocation).toHaveTextContent('12 / 12 pcs');
  expect(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  ).toBeEnabled();
  // Nothing allocated before Confirm.
  expect(allocationRequests()).toHaveLength(0);
});

test('a partial quantity stocks only that part through the server split', async () => {
  await renderStation();
  const allocation = await stockPnA(4);
  expect(stockingRequests()[0].body.quantity).toBe(4);
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 4 stocked');
  expect(toast).toHaveTextContent('SPLIT · 8 pcs remain at Material');
  // The allocation is for exactly the stocked 4 pcs.
  expect(suggestionRequests()[0].url).toContain('quantity=4');
  expect(allocation).toHaveTextContent('4 pcs stocked at');
  expect(lineQuantity(allocation, '007020')).toHaveValue('4');
  expect(lineQuantity(allocation, '007003')).toHaveValue('0');
  expect(allocation).toHaveTextContent('4 / 4 pcs');
  // The remainder stays at Material as active quantity.
  expect(flows.find((f) => f.pn === 'PN-A' && !f.stocked)?.qty).toBe(8);
});

test('several sources take an explicit selection before stocking — never an automatic pick', async () => {
  await renderStation();
  scan('PF:PN:PN-B');

  const select = await screen.findByRole('dialog', {
    name: 'Select the source to stock',
  });
  expect(select).toHaveTextContent('never combined');
  const choices = within(select).getAllByRole('button', { name: /pcs/ });
  expect(choices).toHaveLength(2);
  expect(stockingRequests()).toHaveLength(0);
  fireEvent.click(choices.find((c) => c.textContent?.includes('Cut'))!);

  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  expect(box).toHaveTextContent('Cut');
  expect(quantityInput(box)).toHaveValue('7');
  // Back returns to the selection.
  fireEvent.click(within(box).getByRole('button', { name: '‹ Back' }));
  await screen.findByRole('dialog', { name: 'Select the source to stock' });
});

/* ============ The allocation ============ */

test('the confirmation sends the stocked quantity as allocation_quantity with the lines, then refreshes and refocuses', async () => {
  const input = await renderStation();
  const allocation = await stockPnA(12);
  const inventoryReadsBefore = requests.filter((r) =>
    /\/inventory$/.test(r.url),
  ).length;

  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  );

  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  const body = allocationRequests()[0].body;
  expect(body).toMatchObject({
    part_number: 'PN-A',
    allocation_quantity: 12,
    station_id: 'STOCK-ST-01',
  });
  expect(body.lines).toEqual([
    { work_order_demand_id: 13, quantity: 4 },
    { work_order_demand_id: 11, quantity: 5 },
    { work_order_demand_id: 12, quantity: 3 },
  ]);
  expect(typeof body.device_event_id).toBe('string');
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Allocate stocked quantity' }),
    ).toBeNull(),
  );
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 12 allocated');
  // 007020 (4/4) and 007003 (5/5) completed; 007010 (5/10) did not —
  // NAMED by Work Order Number, never only by id.
  expect(toast).toHaveTextContent('Work Orders 007020, 007003 completed');
  // The Area is re-read from the server and the barcode input refocused.
  await waitFor(() =>
    expect(
      requests.filter((r) => /\/inventory$/.test(r.url)).length,
    ).toBeGreaterThan(inventoryReadsBefore),
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('the operator adjusts the suggestion — Confirm stays disabled until the total equals the stocked quantity', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  const confirm = within(allocation).getByRole('button', {
    name: 'Confirm allocation',
  });

  // Move 2 pcs away from 007003: the total no longer matches.
  fireEvent.click(
    within(allocation).getByRole('button', {
      name: 'Allocate one less to 007003',
    }),
  );
  fireEvent.click(
    within(allocation).getByRole('button', {
      name: 'Allocate one less to 007003',
    }),
  );
  expect(lineQuantity(allocation, '007003')).toHaveValue('3');
  expect(allocation).toHaveTextContent('10 / 12 pcs');
  expect(allocation).toHaveTextContent('2 pcs still to allocate');
  expect(allocation).toHaveTextContent('adjusted (suggested 5)');
  expect(confirm).toBeDisabled();

  // The stepper never exceeds a line's shortage (007010: 8 short).
  fireEvent.change(lineQuantity(allocation, '007010'), {
    target: { value: '99' },
  });
  expect(lineQuantity(allocation, '007010')).toHaveValue('8');
  expect(allocation).toHaveTextContent('15 / 12 pcs');
  expect(allocation).toHaveTextContent('3 pcs too many');
  expect(confirm).toBeDisabled();
  expect(
    within(allocation).getByRole('button', {
      name: 'Allocate one more to 007010',
    }),
  ).toBeDisabled();

  // Back to a matching total: 4 + 3 + 5.
  fireEvent.change(lineQuantity(allocation, '007010'), {
    target: { value: '5' },
  });
  expect(allocation).toHaveTextContent('12 / 12 pcs');
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  expect(allocationRequests()[0].body.allocation_quantity).toBe(12);
  expect(allocationRequests()[0].body.lines).toEqual([
    { work_order_demand_id: 13, quantity: 4 },
    { work_order_demand_id: 11, quantity: 3 },
    { work_order_demand_id: 12, quantity: 5 },
  ]);
  expect(allocationRequests()[0].body.allocation_quantity).not.toBe(
    stockedOf('PN-A') + 1000, // (never the whole available stock — see below)
  );
});

test('the allocation quantity is the stocked quantity, never the whole available stock', async () => {
  // 20 pcs of PN-A were stocked earlier and never allocated; the
  // operator now stocks 12 more. The allocation covers the 12, and the
  // request says so — not the 32 available.
  flows.push({ id: 200, pn: 'PN-A', qty: 20, areaId: 4, stocked: true });
  await renderStation();
  const allocation = await stockPnA(12);
  expect(allocation).toHaveTextContent('12 pcs stocked at');
  expect(allocation).toHaveTextContent('12 / 12 pcs');
  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  );
  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  expect(allocationRequests()[0].body.allocation_quantity).toBe(12);
  expect(
    allocationRequests()[0].body.lines.reduce(
      (s: number, l: { quantity: number }) => s + l.quantity,
      0,
    ),
  ).toBe(12);
});

test('a stale-stock refusal keeps the dialog open, refreshes the suggestion and reports no success', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  // Someone allocated 10 pcs of PN-A from Management meanwhile: only
  // 2 pcs are available now — the server refuses the stale 12.
  allocatedElsewhere.set('PN-A', 10);
  const suggestionsBefore = suggestionRequests().length;
  const firstEventId = () => allocationRequests()[0].body.device_event_id;

  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  );
  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  const box = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await waitFor(() =>
    expect(box).toHaveTextContent('Only 2 pcs of Part Number'),
  );
  expect(box).toHaveTextContent('The suggestion was refreshed from the server');
  // The suggestion was re-read: the refreshed figures show the stale
  // stock up front, and nothing was reported as allocated.
  await waitFor(() =>
    expect(suggestionRequests().length).toBeGreaterThan(suggestionsBefore),
  );
  await within(box).findByRole('table', { name: 'Allocation suggestion' });
  expect(box).toHaveTextContent(
    'Only 2 pcs of this Part Number are still unallocated',
  );
  expect(document.querySelector('.ss-toast')?.textContent ?? '').not.toContain(
    'allocated',
  );
  expect(demands.find((d) => d.id === 13)?.allocated).toBe(0);

  // The refreshed proposal for 2 pcs cannot reach the 12 — Confirm
  // stays disabled; the operator leaves the quantity in stock.
  expect(
    within(box).getByRole('button', { name: 'Retry allocation' }),
  ).toBeDisabled();
  fireEvent.click(
    within(box).getByRole('button', {
      name: 'Leave in stock — allocate later',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const toast = await notice();
  expect(toast).toHaveTextContent('left in stock — not allocated');
  // An adjusted intent after a refusal would take a NEW device_event_id
  // (nothing was recorded under the refused one).
  expect(firstEventId()).toBeDefined();
});

test('a refused adjustment is retried under a new device_event_id after adjusting', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  allocationFailure = {
    status: 409,
    body: { detail: 'Demand line 11 can take 3 pcs more, not 5.' },
  };
  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  );
  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  const box = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await within(box).findByText(/Demand line 11 can take 3 pcs more/);
  await within(box).findByRole('table', { name: 'Allocation suggestion' });
  allocationFailure = null;
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry allocation' }),
  );
  await waitFor(() => expect(allocationRequests()).toHaveLength(2));
  expect(allocationRequests()[1].body.device_event_id).not.toBe(
    allocationRequests()[0].body.device_event_id,
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

test('a lost response is an UNKNOWN outcome: the exact same allocation is retried under the same device_event_id and never recorded twice', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  allocationFailure = 'lost-response';
  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  );

  const box = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await within(box).findByText(/may or may not have been recorded/);
  // Frozen: the lines cannot change, and nothing was reported as done.
  expect(lineQuantity(box, '007003')).toBeDisabled();
  expect(document.querySelector('.ss-toast')?.textContent ?? '').not.toContain(
    'allocated',
  );
  const retry = within(box).getByRole('button', {
    name: 'Retry the same allocation',
  });
  fireEvent.click(retry);

  await waitFor(() => expect(allocationRequests()).toHaveLength(2));
  expect(allocationRequests()[1].body).toEqual(allocationRequests()[0].body);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const toast = await notice();
  expect(toast).toHaveTextContent(
    'already recorded by the server — nothing was recorded twice',
  );
  // Recorded exactly once on the server.
  expect(demands.find((d) => d.id === 13)?.allocated).toBe(4);
  expect(demands.find((d) => d.id === 11)?.allocated).toBe(5);
});

test('a lost STOCKED response retries the same stocking under the same device_event_id before any allocation', async () => {
  await renderStation();
  stockFailure = 'lost-response';
  scan('PF:PN:PN-A');
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm stocking' }),
  );
  await within(box).findByText(/may or may not have been recorded/);
  expect(
    screen.queryByRole('dialog', { name: 'Allocate stocked quantity' }),
  ).toBeNull();
  expect(suggestionRequests()).toHaveLength(0);

  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same stocking' }),
  );
  await screen.findByRole('dialog', { name: 'Allocate stocked quantity' });
  expect(stockingRequests()).toHaveLength(2);
  expect(stockingRequests()[1].body.device_event_id).toBe(
    stockingRequests()[0].body.device_event_id,
  );
  const toast = await notice();
  expect(toast).toHaveTextContent('already recorded by the server');
  expect(flows.filter((f) => f.pn === 'PN-A' && f.stocked)).toHaveLength(2);
});

test('a server refusal of the stocking records nothing and opens no allocation', async () => {
  await renderStation();
  stockFailure = {
    status: 409,
    body: { detail: 'Quantity Flow is no longer in the selected source Area.' },
  };
  scan('PF:PN:PN-A');
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm stocking' }),
  );
  await within(box).findByText(/no longer in the selected source Area/);
  expect(
    screen.queryByRole('dialog', { name: 'Allocate stocked quantity' }),
  ).toBeNull();
  expect(within(box).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(flows.find((f) => f.id === 100)?.stocked).toBeUndefined();
});

test('leaving the quantity in stock records no allocation', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  fireEvent.click(
    within(allocation).getByRole('button', {
      name: 'Leave in stock — allocate later',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(allocationRequests()).toHaveLength(0);
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 12 left in stock — not allocated');
  // The stock is visible on the next resolution.
  scan('PF:PN:PN-A');
  const none = await screen.findByRole('dialog', {
    name: 'No quantity to receive',
  });
  expect(none).toHaveTextContent(
    '14 pcs are already stocked (12 pcs not yet allocated)',
  );
});

test('a stocked command is never an Undo target — the Last Scanned PN block offers nothing reversible', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  fireEvent.click(
    within(allocation).getByRole('button', {
      name: 'Leave in stock — allocate later',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  // The stocking is in the session history, but the server (the
  // authority on eligibility) judges it ineligible — UNDO stays
  // disabled and the block names no reversible action.
  const undo = document.querySelector('button.ss-undo') as HTMLButtonElement;
  await waitFor(() => expect(undo).toBeDisabled());
  expect(document.querySelector('.ss-lastpninfo .d')?.textContent).toContain(
    'No reversible Part Number action',
  );
  expect(undo.title).toBe(
    'No completed action of this session can currently be reversed',
  );
  // Nothing was posted to any undo endpoint.
  expect(
    requests.filter((r) => r.method === 'POST' && /\/undos$/.test(r.url)),
  ).toHaveLength(0);
});

test('Enter confirms the allocation once the total matches — keyboard-first, like every other write dialog', async () => {
  await renderStation();
  const allocation = await stockPnA(12);
  // Enter on the dialog itself (focus is not inside a field): the
  // one write, exactly as the Confirm button would send it.
  fireEvent.keyDown(allocation, { key: 'Enter' });
  await waitFor(() => expect(allocationRequests()).toHaveLength(1));
  expect(allocationRequests()[0].body).toMatchObject({
    part_number: 'PN-A',
    allocation_quantity: 12,
  });
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 12 allocated');
});

test('with no outstanding demand the stocked quantity simply stays in stock', async () => {
  demands = demands.filter((d) => d.pn !== 'PN-A');
  await renderStation();
  scan('PF:PN:PN-A');
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm stocking' }),
  );
  const allocation = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await within(allocation).findByText(/No outstanding Work Order Demand/);
  expect(
    within(allocation).getByRole('button', { name: 'Confirm allocation' }),
  ).toBeDisabled();
  fireEvent.click(within(allocation).getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(allocationRequests()).toHaveLength(0);
});

/* ============ Offline ============ */

test('going offline disables the stocking and the allocation confirmations in place', async () => {
  await renderStation();
  scan('PF:PN:PN-A');
  const box = await screen.findByRole('dialog', {
    name: 'Receive into Stockroom',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  await waitFor(() =>
    expect(
      within(box).getByRole('button', { name: 'Confirm stocking' }),
    ).toBeDisabled(),
  );
  expect(box).toHaveTextContent(
    'Disconnected — the stocking cannot be recorded',
  );
  // Enter reaches the confirmation while blocked: the refusal names the
  // stocking — never a "transfer" — and nothing is sent.
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(box).toHaveTextContent(
    'Connection lost — the stocking was not sent. Reconnect and confirm again; nothing was recorded.',
  );
  expect(box).not.toHaveTextContent('the transfer was not sent');
  expect(stockingRequests()).toHaveLength(0);
  act(() => {
    window.dispatchEvent(new Event('online'));
  });
  // Back online the same dialog offers the retry of the stocking.
  await waitFor(() =>
    expect(
      within(box).getByRole('button', { name: 'Retry stocking' }),
    ).toBeEnabled(),
  );
  fireEvent.click(within(box).getByRole('button', { name: 'Retry stocking' }));
  const allocation = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  await within(allocation).findByRole('table', {
    name: 'Allocation suggestion',
  });
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  await waitFor(() =>
    expect(
      within(allocation).getByRole('button', { name: 'Confirm allocation' }),
    ).toBeDisabled(),
  );
  expect(allocation).toHaveTextContent(
    'Disconnected — the allocation cannot be recorded',
  );
  expect(allocationRequests()).toHaveLength(0);
});
