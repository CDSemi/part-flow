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

// Real Scan Station (Phase 8 — partial quantity and `Combine
// quantities`) against a fake in-memory `/api` with the backend's Phase
// 8 semantics: a quantity smaller than the flow's splits the flow on
// the SERVER inside the same command (the source closes, a selected
// child receives the action, a remainder child keeps the source's
// state; the response names `source_quantity_flow_id`,
// `remainder_quantity_flow_id` and `remainder_quantity`), the PN
// resolution reports `combine_groups` — the in-Area flows the server
// judges combinable — and `POST …/merges` combines the named flows into
// one resulting flow (409 with nothing recorded when their context is
// not identical). Covers: partial Transfer / Assign / QUEUE / Machine
// DONE / direct DONE with the remainder shown before and after the
// write, the full-quantity regression (no split), the Combine action's
// visibility, source selection, the confirmation, the recorded merge
// with the inventory and totals afterwards, the server refusal, the
// lost-response retry under the same device_event_id, and focus
// restoration. The client never splits, never combines and never judges
// compatibility itself.

type State = 'QUEUED' | 'PROCESSING' | 'ON_MACHINE' | 'READY_TO_TRANSFER';

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: State;
  machineId: number | null;
  /** Phase 11 monitoring context of the shared row (optional in the
   * fixtures: the default entry timestamp and no completing Machine). */
  completedMachineId?: number | null;
  enteredAt?: string;
}

const AREAS = [
  { id: 2, name: 'Lathe', color: '#3366ff' },
  { id: 6, name: 'Plating', color: '#aa33aa' },
];
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'TURNING', name: 'Turning', is_external: false },
  { id: 60, area_id: 6, code: 'PLATE', name: 'Plating', is_external: false },
];
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 2 },
  { station_id: 'PLATING-ST-01', area_id: 6 },
];
const MACHINES = [
  { id: 1, name: 'Lathe 1', tag: 'CD-0001', areaId: 2 },
  { id: 2, name: 'Lathe 2', tag: 'CD-0002', areaId: 2 },
];

let flows: Flow[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let nextFlowId: number;
let writeFailure:
  null | 'network' | 'lost-response' | { status: number; body: unknown };
// When set, the resolution reports exactly these groups — a test seam
// for a stale server judgement naming flows no longer in the Area.
let combineGroupsOverride: number[][] | null;

function areaRef(areaId: number) {
  const area = AREAS.find((a) => a.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: false,
  };
}

function operationsOf(areaId: number) {
  return OPERATIONS.filter((o) => o.area_id === areaId);
}

function machineRef(machine: (typeof MACHINES)[number]) {
  const assigned = flows
    .filter((f) => f.state === 'ON_MACHINE' && f.machineId === machine.id)
    .reduce((s, f) => s + f.qty, 0);
  return {
    id: machine.id,
    name: machine.name,
    asset_tag: machine.tag,
    barcode_value: `PF:MACHINE:${machine.tag}`,
    operational_state: assigned > 0 ? 'RUNNING' : 'IDLE',
    state_changed_at: '2026-08-25T10:00:00Z',
    maintenance_since: null,
    maintenance_note: null,
    maintenance_expected_return: null,
  };
}

function actionsOf(state: State) {
  return state === 'QUEUED'
    ? ['ASSIGN', 'TRANSFER', 'SCRAP']
    : state === 'ON_MACHINE'
      ? ['DONE', 'QUEUE', 'TRANSFER', 'SCRAP']
      : state === 'PROCESSING'
        ? ['DONE', 'TRANSFER', 'SCRAP']
        : ['TRANSFER', 'SCRAP'];
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: {
      ...OPERATIONS.find((o) => o.area_id === flow.areaId)!,
      is_active: true,
    },
    processing_state: flow.state,
    machine_id: flow.machineId,
    completed_machine_id: flow.completedMachineId ?? null,
    entered_at: flow.enteredAt ?? '2026-08-01T06:30:00Z',
    available_actions: actionsOf(flow.state),
    work_order: {
      work_order_id: 1,
      work_order_number: '007003',
      work_order_demand_id: 11,
      request_type: 'NEW',
      job_numbers: ['18112'],
      due_date: null,
      priority_rank: null,
      received_date: '2026-07-12',
    },
  };
}

function lines(items: Flow[]) {
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

function stationOf(url: string): { station_id: string; area_id: number } {
  const id = decodeURIComponent(/\/scan-stations\/([^/]+)\//.exec(url)![1]);
  return STATIONS.find((s) => s.station_id === id)!;
}

function hasMachines(areaId: number) {
  return MACHINES.some((m) => m.areaId === areaId);
}

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const sum = (items: Flow[]) => items.reduce((s, f) => s + f.qty, 0);
  const all = lines(here);
  return json({
    area: areaRef(areaId),
    has_machines: hasMachines(areaId),
    lines: all,
    total_part_numbers: all.length,
    total_quantity: sum(here),
    queued: lines(byState('QUEUED')),
    queued_quantity: sum(byState('QUEUED')),
    machines: MACHINES.filter((m) => m.areaId === areaId).map((m) => {
      const held = here.filter(
        (f) => f.state === 'ON_MACHINE' && f.machineId === m.id,
      );
      return {
        machine: machineRef(m),
        lines: lines(held),
        total_quantity: sum(held),
      };
    }),
    on_machine_quantity: sum(byState('ON_MACHINE')),
    processing: lines(byState('PROCESSING')),
    processing_quantity: sum(byState('PROCESSING')),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: sum(byState('READY_TO_TRANSFER')),
  });
}

/** The SERVER's judgement of what may combine: one identical context
 * (state + Machine) per group, at least two flows. */
function combineGroups(inArea: Flow[]) {
  const groups = new Map<string, number[]>();
  for (const flow of [...inArea].sort((a, b) => a.id - b.id)) {
    const key = `${flow.state}:${flow.machineId ?? ''}`;
    groups.set(key, [...(groups.get(key) ?? []), flow.id]);
  }
  return [...groups.values()].filter((ids) => ids.length >= 2);
}

function failWrite(): Response | null {
  if (writeFailure === 'network') {
    writeFailure = null;
    throw new TypeError('Failed to fetch');
  }
  if (writeFailure && typeof writeFailure === 'object') {
    const failure = writeFailure;
    writeFailure = null;
    return json(failure.body, failure.status);
  }
  return null;
}

function commit(deviceEventId: string, result: unknown): Response {
  committed.set(deviceEventId, result);
  if (writeFailure === 'lost-response') {
    writeFailure = null;
    throw new TypeError('Failed to fetch');
  }
  return json(result, 201);
}

/**
 * The server's Phase 8 split: a quantity smaller than the flow's
 * closes the source and creates the selected child (returned) and the
 * remainder child, which keeps the source's state.
 */
function splitIfPartial(
  flow: Flow,
  quantity: number,
): { acted: Flow; split: Record<string, number | null> } {
  if (quantity === flow.qty) {
    return {
      acted: flow,
      split: {
        source_quantity_flow_id: null,
        remainder_quantity_flow_id: null,
        remainder_quantity: null,
      },
    };
  }
  const selected: Flow = { ...flow, id: nextFlowId++, qty: quantity };
  const remainder: Flow = {
    ...flow,
    id: nextFlowId++,
    qty: flow.qty - quantity,
  };
  flows = flows.filter((f) => f.id !== flow.id).concat([selected, remainder]);
  return {
    acted: selected,
    split: {
      source_quantity_flow_id: flow.id,
      remainder_quantity_flow_id: remainder.id,
      remainder_quantity: remainder.qty,
    },
  };
}

function handle(url: string, method: string, body: unknown): Response {
  if (url === '/api/health') return json({ status: 'ok' });
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
  if (url === '/api/departments') {
    return json([{ id: 1, name: 'Finishing', is_active: true }]);
  }
  if (url === '/api/operations') {
    return json(
      OPERATIONS.map((o) => ({
        ...o,
        description: null,
        default_expected_duration: null,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/machines') return json([]);
  if (/\/context$/.test(url)) {
    const station = stationOf(url);
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(station.area_id),
      operations: operationsOf(station.area_id),
      has_machines: hasMachines(station.area_id),
    });
  }
  const inv = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inv) return inventory(Number(inv[1]));

  if (/\/scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const input = body as { barcode?: string; part_number?: string };
    const scanned = input.barcode ?? `PF:PN:${input.part_number}`;
    const pn = scanned.slice('PF:PN:'.length).toUpperCase();
    const mine = flows.filter((f) => f.pn === pn);
    const inArea = mine.filter((f) => f.areaId === station.area_id);
    const candidates = mine.filter((f) => f.areaId !== station.area_id);
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
      has_active_demand: true,
      intake_available: false,
      part_number_known: true,
      internal_work_orders: [],
      // Phase 10.5: the PN's existing ACTIVE distribution — a receipt
      // never joins it (PROJECT_PROFILE §14).
      active_quantity: [],
      transfer_blocked_reason: null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
      combine_groups: combineGroupsOverride ?? combineGroups(inArea),
      scrapped_quantity: 0,
      stocked_quantity: 0,
      available_stocked_quantity: 0,
      stock_available: false,
      scanned_at: new Date().toISOString(),
    });
  }

  const inArea =
    /\/(machine-assignments|machine-releases|area-completions)$/.exec(url);
  if (inArea && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      machine_id?: number;
      quantity: number;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const source = flows.find((f) => f.id === request.quantity_flow_id);
    if (!source) return json({ detail: 'no longer active.' }, 409);
    if (request.quantity > source.qty) {
      return json({ detail: 'exceeds' }, 422);
    }
    const { acted, split } = splitIfPartial(source, request.quantity);
    const kind = inArea[1];
    let movementType: string;
    if (kind === 'machine-assignments') {
      acted.state = 'ON_MACHINE';
      acted.machineId = request.machine_id!;
      movementType = 'ASSIGNED_TO_MACHINE';
    } else if (kind === 'machine-releases') {
      acted.state = 'QUEUED';
      acted.machineId = null;
      movementType = 'RELEASED_FROM_MACHINE';
    } else {
      acted.state = 'READY_TO_TRANSFER';
      acted.machineId = null;
      movementType = 'AREA_COMPLETED';
    }
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      movement_type: movementType,
      quantity_flow_id: acted.id,
      part_number: acted.pn,
      quantity: acted.qty,
      area_id: acted.areaId,
      machine_id: request.machine_id ?? null,
      operation_id: operationsOf(acted.areaId)[0].id,
      station_id: station.station_id,
      processing_state: acted.state,
      ...split,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-29T12:00:00Z',
    });
  }

  if (/\/transfers$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      quantity_flow_id: number;
      source_area_id: number;
      quantity: number;
      operation_id: number | null;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const source = flows.find((f) => f.id === request.quantity_flow_id)!;
    if (request.quantity > source.qty) {
      return json({ detail: 'exceeds' }, 422);
    }
    const { acted, split } = splitIfPartial(source, request.quantity);
    const completesSource =
      acted.state === 'ON_MACHINE' || acted.state === 'PROCESSING';
    const completedFrom = acted.state === 'ON_MACHINE' ? acted.machineId : null;
    const completedMovementId = completesSource ? nextMovementId++ : null;
    acted.areaId = station.area_id;
    acted.state = hasMachines(station.area_id) ? 'QUEUED' : 'PROCESSING';
    acted.machineId = null;
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      quantity_flow_id: acted.id,
      part_number: acted.pn,
      quantity: acted.qty,
      from_area_id: request.source_area_id,
      to_area_id: station.area_id,
      operation_id: request.operation_id ?? operationsOf(station.area_id)[0].id,
      station_id: station.station_id,
      assigned_route_step_id: null,
      movement_reason: null,
      reason: null,
      route_deviation: null,
      completed_movement_id: completedMovementId,
      completed_machine_id: completedFrom,
      ...split,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-29T12:00:00Z',
    });
  }

  if (/\/merges$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_ids: number[];
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const sources = request.quantity_flow_ids.map((id) =>
      flows.find((f) => f.id === id),
    );
    if (sources.some((f) => !f)) {
      return json(
        {
          detail:
            'Quantity Flow was merged into another quantity and is no longer active. Nothing was recorded.',
        },
        409,
      );
    }
    const present = sources as Flow[];
    const keys = new Set(present.map((f) => `${f.state}:${f.machineId ?? ''}`));
    if (keys.size !== 1) {
      return json(
        {
          detail:
            'These Quantity Flows cannot be merged: they are not on the same Machine. Merge only quantities with one identical production context. Nothing was recorded.',
        },
        409,
      );
    }
    const first = present[0];
    const result: Flow = {
      ...first,
      id: nextFlowId++,
      qty: present.reduce((s, f) => s + f.qty, 0),
    };
    flows = flows
      .filter((f) => !request.quantity_flow_ids.includes(f.id))
      .concat([result]);
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      quantity_flow_id: result.id,
      part_number: result.pn,
      quantity: result.qty,
      area_id: result.areaId,
      machine_id: result.machineId,
      operation_id: operationsOf(result.areaId)[0].id,
      station_id: station.station_id,
      processing_state: result.state,
      source_quantity_flow_ids: [...request.quantity_flow_ids].sort(
        (a, b) => a - b,
      ),
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-29T12:00:00Z',
    });
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    // Lathe (Machines): a queued PN, and one on a Machine.
    {
      id: 300,
      pn: 'PN-A',
      qty: 12,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 301,
      pn: 'PN-B',
      qty: 10,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 1,
    },
    // Lathe: two portions of one PN on DIFFERENT Machines — never combinable.
    {
      id: 330,
      pn: 'PN-E',
      qty: 3,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 1,
    },
    {
      id: 331,
      pn: 'PN-E',
      qty: 5,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 2,
    },
    // Plating (no Machines): one processing PN.
    {
      id: 310,
      pn: 'PN-C',
      qty: 9,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    // Plating: three portions of one PN — two processing (combinable), one finished.
    {
      id: 320,
      pn: 'PN-D',
      qty: 6,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 321,
      pn: 'PN-D',
      qty: 4,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 322,
      pn: 'PN-D',
      qty: 2,
      areaId: 6,
      state: 'READY_TO_TRANSFER',
      machineId: null,
    },
    // Plating: three processing portions of one PN.
    {
      id: 340,
      pn: 'PN-F',
      qty: 1,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 341,
      pn: 'PN-F',
      qty: 2,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 342,
      pn: 'PN-F',
      qty: 3,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  nextFlowId = 900;
  writeFailure = null;
  combineGroupsOverride = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      return Promise.resolve().then(() => handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId: string) {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  return input;
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function dialog() {
  return screen.getByRole('dialog');
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function writes() {
  return requests.filter(
    (r) =>
      r.method === 'POST' &&
      /\/(area-completions|machine-assignments|machine-releases|transfers|merges)$/.test(
        r.url,
      ),
  );
}

function reads(pattern: RegExp) {
  return requests.filter((r) => r.method === 'GET' && pattern.test(r.url));
}

function summaryCard(): HTMLElement {
  return document.querySelector('.abd-summary') as HTMLElement;
}

function machineCard(name: string): HTMLElement {
  const heading = screen.getByText(name, { selector: '.mname' });
  return heading.closest('.abd-machine') as HTMLElement;
}

function rowsOf(container: HTMLElement, pn: string): HTMLElement[] {
  return within(container)
    .getAllByText(pn)
    .map((node) => node.closest('li') as HTMLElement);
}

function quantityInput(box: HTMLElement) {
  return within(box).getByLabelText(/^Quantity: /);
}

function summaryValue(box: HTMLElement, term: string): string {
  const dt = within(box).getByText(term, { selector: 'dt' });
  return dt.nextElementSibling?.textContent ?? '';
}

async function lastAction() {
  return waitFor(() => document.querySelector('.ss-lastpn .d') as HTMLElement);
}

/* ============ Partial quantity ============ */

test('a partial transfer shows MAX, the selected part and the remainder before confirming, sends only the chosen quantity, and afterwards both resulting quantities read correctly', async () => {
  const input = await renderStation('PLATING-ST-01');
  scan('PF:PN:PN-A'); // 12 pcs queued at Lathe → Plating
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(box).toHaveTextContent('Available at Lathe: 12 pcs (MAX)');
  expect(box).toHaveTextContent('A smaller quantity moves only that part');
  const quantity = quantityInput(box);
  expect(quantity).toHaveValue('12');
  fireEvent.change(quantity, { target: { value: '5' } });
  expect(within(box).getByRole('button', { name: 'Next' })).toBeEnabled();
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));

  const summary = dialog();
  expect(summaryValue(summary, 'Quantity')).toBe('5 pcs of 12');
  expect(summaryValue(summary, 'Remaining at source')).toBe(
    '7 pcs — stays queued',
  );
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 5 → Plating');
  expect(toast).toHaveTextContent('SPLIT · 7 pcs remain at Lathe');
  // The client sent the chosen quantity only — no split of its own.
  expect(writes()).toHaveLength(1);
  expect(writes()[0].body.quantity).toBe(5);
  expect(writes()[0].body.quantity_flow_id).toBe(300);
  expect(await lastAction()).toHaveTextContent(
    'SPLIT + TRANSFERRED · Lathe → Plating — direct processing · qty 5 of 12',
  );
  // The moved part arrived here as processing; the remainder is still
  // queued at Lathe (both are the server's new flows).
  await waitFor(() =>
    expect(rowsOf(summaryCard(), 'PN-A')[0]).toHaveTextContent('5 pcs'),
  );
  expect(rowsOf(summaryCard(), 'PN-A')[0]).toHaveTextContent('In processing');
  expect(flows.find((f) => f.id === 300)).toBeUndefined();
  const remainder = flows.find((f) => f.pn === 'PN-A' && f.areaId === 2)!;
  expect(remainder).toMatchObject({ qty: 7, state: 'QUEUED' });
  expect(
    flows.filter((f) => f.pn === 'PN-A').reduce((s, f) => s + f.qty, 0),
  ).toBe(12);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a partial Assign to Machine sends the chosen quantity and leaves the rest queued; the summary names the remainder', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Assign to Machine/ }),
  );
  const assign = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  fireEvent.click(within(assign).getByRole('button', { name: /Lathe 2/ }));
  fireEvent.click(within(assign).getByRole('button', { name: 'Next' }));
  expect(assign).toHaveTextContent('Available: 12 pcs queued (MAX)');
  fireEvent.change(quantityInput(assign), { target: { value: '4' } });
  fireEvent.click(within(assign).getByRole('button', { name: 'Next' }));
  expect(summaryValue(assign, 'Quantity')).toBe('4 pcs of 12');
  expect(summaryValue(assign, 'Remaining queued after assignment')).toBe(
    '8 pcs',
  );
  fireEvent.click(
    within(assign).getByRole('button', { name: 'Confirm assignment' }),
  );
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 4 assigned to Lathe 2');
  expect(toast).toHaveTextContent('SPLIT · 8 pcs remain in the Lathe queue');
  expect(writes()[0].body).toMatchObject({
    quantity_flow_id: 300,
    machine_id: 2,
    quantity: 4,
  });
  // Lathe 2 already held 5 pcs of PN-E: 5 + 4.
  await waitFor(() =>
    expect(machineCard('Lathe 2')).toHaveTextContent('9 pcs assigned'),
  );
  expect(machineCard('Lathe 2')).toHaveTextContent('PN-A4 pcs');
  // The summary card lists both resulting quantities of the PN: the
  // remainder still queued and the part now on Lathe 2.
  const rows = rowsOf(summaryCard(), 'PN-A').map((r) => r.textContent ?? '');
  expect(
    rows.some((text) => text.includes('8 pcs') && text.includes('queue')),
  ).toBe(true);
  expect(
    rows.some((text) => text.includes('4 pcs') && text.includes('Lathe 2')),
  ).toBe(true);
  const stats = screen.getByLabelText('Area statistics');
  expect(within(stats).getByText('Queued').previousSibling).toHaveTextContent(
    '8',
  );
});

test.each([
  [
    'QUEUE',
    'Return to Area queue',
    'Confirm return to queue',
    'Yes — return to queue',
    'QUEUED',
  ],
  [
    'DONE',
    'Complete Area processing',
    'Confirm completion',
    'Yes — finished',
    'READY_TO_TRANSFER',
  ],
])(
  'a partial Machine %s acts on the chosen part only; the remainder stays on the Machine',
  async (_kind, rowAction, confirmLabel, yesLabel, resultingState) => {
    await renderStation('LATHE-ST-01');
    fireEvent.click(
      within(machineCard('Lathe 1')).getAllByRole('button', {
        name: rowAction,
      })[0],
    );
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('10 pcs are on Lathe 1 (MAX)');
    expect(dlg).toHaveTextContent('the rest stays on Lathe 1');
    fireEvent.change(quantityInput(dlg), { target: { value: '3' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
    expect(summaryValue(dlg, 'Quantity')).toBe('3 pcs of 10');
    expect(summaryValue(dlg, 'Remaining on Lathe 1')).toBe('7 pcs');
    fireEvent.click(within(dlg).getByRole('button', { name: confirmLabel }));
    const gate = await screen.findByRole('dialog', { name: /\?$/ });
    expect(gate).toHaveTextContent('3 pcs');
    expect(gate).toHaveTextContent('The remaining 7 pcs stay on Lathe 1');
    fireEvent.click(within(gate).getByRole('button', { name: yesLabel }));
    const toast = await notice();
    expect(toast).toHaveTextContent('PN-B × 3');
    expect(toast).toHaveTextContent('SPLIT · 7 pcs remain on Lathe 1');
    expect(writes()[0].body).toMatchObject({
      quantity_flow_id: 301,
      machine_id: 1,
      quantity: 3,
    });
    // Lathe 1 held 10 pcs of PN-B and 3 pcs of PN-E: 13 − 3.
    await waitFor(() =>
      expect(machineCard('Lathe 1')).toHaveTextContent('10 pcs assigned'),
    );
    expect(machineCard('Lathe 1')).toHaveTextContent('PN-B7 pcs');
    const acted = flows.find((f) => f.pn === 'PN-B' && f.qty === 3)!;
    expect(acted.state).toBe(resultingState);
    expect(flows.find((f) => f.pn === 'PN-B' && f.qty === 7)!.state).toBe(
      'ON_MACHINE',
    );
  },
);

test('a partial direct-processing DONE finishes the chosen part; the remainder keeps processing', async () => {
  const input = await renderStation('PLATING-ST-01');
  fireEvent.click(
    within(rowsOf(summaryCard(), 'PN-C')[0]).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  expect(dlg).toHaveTextContent('9 pcs are in processing at Plating (MAX)');
  fireEvent.change(quantityInput(dlg), { target: { value: '4' } });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  expect(summaryValue(dlg, 'Quantity')).toBe('4 pcs of 9');
  expect(summaryValue(dlg, 'Remaining in processing')).toBe('5 pcs');
  fireEvent.click(
    within(dlg).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  expect(gate).toHaveTextContent('The remaining 5 pcs stay in processing');
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-C × 4 finished at Plating');
  expect(toast).toHaveTextContent('SPLIT · 5 pcs remain in Plating processing');
  expect(writes()[0].body).toEqual({
    part_number: 'PN-C',
    quantity_flow_id: 310,
    quantity: 4,
    device_event_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
  });
  await waitFor(() => expect(rowsOf(summaryCard(), 'PN-C')).toHaveLength(2));
  const rows = rowsOf(summaryCard(), 'PN-C');
  expect(rows.map((r) => r.textContent)).toEqual([
    expect.stringMatching(/5 pcs.*In processing|In processing.*5 pcs/s),
    expect.stringMatching(/4 pcs.*Finished|Finished.*4 pcs/s),
  ]);
  const stats = screen.getByLabelText('Area statistics');
  expect(within(stats).getByText('Done').previousSibling).toHaveTextContent(
    '6',
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('the full quantity keeps the whole-flow workflow: no remainder row, no split in the notice', async () => {
  await renderStation('PLATING-ST-01');
  fireEvent.click(
    within(rowsOf(summaryCard(), 'PN-C')[0]).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  expect(summaryValue(dlg, 'Quantity')).toBe('9 pcs');
  expect(within(dlg).queryByText('Remaining in processing')).toBeNull();
  fireEvent.click(
    within(dlg).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  expect(gate).not.toHaveTextContent('remaining');
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-C × 9 finished at Plating');
  expect(toast).not.toHaveTextContent('SPLIT');
  expect(writes()[0].body.quantity).toBe(9);
  expect(await lastAction()).toHaveTextContent(
    'AREA_COMPLETED · Plating processing → Finished — ready to move · qty 9',
  );
  expect(flows.filter((f) => f.pn === 'PN-C')).toHaveLength(1);
  expect(flows.find((f) => f.pn === 'PN-C')!.id).toBe(310);
});

test('a lost response of a partial action retries the same device_event_id and replays the committed split without a second one', async () => {
  await renderStation('PLATING-ST-01');
  fireEvent.click(
    within(rowsOf(summaryCard(), 'PN-C')[0]).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.change(quantityInput(dlg), { target: { value: '2' } });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  writeFailure = 'lost-response';
  fireEvent.click(
    within(dlg).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));
  const retry = await screen.findByRole('button', {
    name: 'Retry the same completion',
  });
  expect(writes()).toHaveLength(1);
  fireEvent.click(retry);
  const toast = await notice();
  expect(toast).toHaveTextContent('already recorded by the server');
  expect(toast).toHaveTextContent('SPLIT · 7 pcs remain in Plating processing');
  expect(writes()).toHaveLength(2);
  expect(writes()[1].body).toEqual(writes()[0].body);
  expect(
    flows
      .filter((f) => f.pn === 'PN-C')
      .map((f) => f.qty)
      .sort(),
  ).toEqual([2, 7]);
});

/* ============ Combine quantities ============ */

test('`Combine quantities` is offered exactly for the server-reported combinable group, named by quantity, state and Operation — never for incompatible portions', async () => {
  await renderStation('PLATING-ST-01');
  scan('PF:PN:PN-D');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  const combine = within(actions).getByRole('button', {
    name: /Combine quantities/,
  });
  expect(combine).toHaveTextContent('6 pcs + 4 pcs · In processing · Plating');
  // The finished 2 pcs are not part of the offer.
  expect(combine).not.toHaveTextContent('2 pcs');
  expect(actions).not.toHaveTextContent('Quantity Flow');
  fireEvent.keyDown(actions, { key: 'Escape' });

  // Two portions of one PN on different Machines: the server reports no
  // group, so no Combine action exists.
  cleanup();
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-E');
  const lathe = await screen.findByRole('dialog', { name: 'Select an action' });
  expect(lathe).toHaveTextContent('Several separate quantities are here');
  expect(
    within(lathe).queryByRole('button', { name: /Combine quantities/ }),
  ).toBeNull();
  // A lone quantity never offers it either.
  fireEvent.keyDown(lathe, { key: 'Escape' });
  scan('PF:PN:PN-A');
  const single = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(
    within(single).queryByRole('button', { name: /Combine quantities/ }),
  ).toBeNull();
});

test('a stale server group whose portions are no longer all in the Area offers no Combine action', async () => {
  // The server judged 320 combinable with a flow that has since been
  // consumed (999 is not in the resolution): fewer than two portions
  // resolve, so the station renders no Combine choice — it never
  // substitutes its own judgement for the server's.
  combineGroupsOverride = [
    [320, 999],
    [998, 999],
  ];
  await renderStation('PLATING-ST-01');
  scan('PF:PN:PN-D');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent('Several separate quantities are here');
  expect(
    within(actions).queryByRole('button', { name: /Combine quantities/ }),
  ).toBeNull();
});

test('the Combine dialog selects at least two portions, previews the result, confirms with the sum, records only after the server, and the inventory shows one quantity with unchanged totals', async () => {
  const input = await renderStation('PLATING-ST-01');
  const inventoryReads = reads(/\/inventory$/).length;
  const stats = screen.getByLabelText('Area statistics');
  expect(
    within(stats).getByText('Processing').previousSibling,
  ).toHaveTextContent('25'); // 9 + 6 + 4 + 1 + 2 + 3
  scan('PF:PN:PN-F');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Combine quantities/ }),
  );
  const dlg = await screen.findByRole('dialog', { name: 'Combine quantities' });
  const portions = within(dlg).getByRole('group', {
    name: 'Quantities to combine',
  });
  const buttons = within(portions).getAllByRole('button');
  expect(buttons.map((b) => b.textContent)).toEqual([
    expect.stringContaining('1 pcs · In processing · Plating'),
    expect.stringContaining('2 pcs · In processing · Plating'),
    expect.stringContaining('3 pcs · In processing · Plating'),
  ]);
  // All preselected; the preview is the sum.
  expect(buttons.every((b) => b.getAttribute('aria-pressed') === 'true')).toBe(
    true,
  );
  expect(dlg).toHaveTextContent('Result: 6 pcs · In processing · Plating');
  // Deselecting narrows the combine; below two nothing can be combined.
  fireEvent.click(buttons[2]);
  expect(dlg).toHaveTextContent('Result: 3 pcs · In processing · Plating');
  fireEvent.click(buttons[1]);
  expect(dlg).toHaveTextContent('Select at least two quantities to combine');
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.click(buttons[1]);
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeEnabled();
  // Back returns to the action dialog.
  fireEvent.click(within(dlg).getByRole('button', { name: '‹ Back' }));
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(dialog()).getByRole('button', { name: /Combine quantities/ }),
  );
  const again = await screen.findByRole('dialog', {
    name: 'Combine quantities',
  });
  fireEvent.click(
    within(again).getAllByRole('button', { name: /3 pcs · In processing/ })[0],
  );
  fireEvent.click(within(again).getByRole('button', { name: 'Next' }));

  const summary = screen.getByRole('dialog', { name: 'Combine quantities' });
  expect(summaryValue(summary, 'Action')).toBe('Combine quantities');
  expect(summaryValue(summary, 'PN')).toBe('PN-F');
  expect(summaryValue(summary, 'Area')).toContain('Plating');
  expect(summaryValue(summary, 'Selected quantities')).toBe('1 pcs + 2 pcs');
  expect(summaryValue(summary, 'Resulting quantity')).toBe(
    '1 pcs + 2 pcs → 3 pcs',
  );
  expect(summaryValue(summary, 'State')).toBe('In processing');
  expect(summaryValue(summary, 'Operation')).toBe('Plating');
  expect(summaryValue(summary, 'Recorded event')).toBe('MERGED');
  expect(summary).not.toHaveTextContent('Quantity Flow');
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm combine' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-F: 1 pcs + 2 pcs → 3 pcs combined');
  expect(toast).toHaveTextContent('Recorded by the server (MERGED #500)');
  expect(writes()).toHaveLength(1);
  expect(writes()[0].url).toMatch(/\/scan-stations\/PLATING-ST-01\/merges$/);
  expect(writes()[0].body).toEqual({
    part_number: 'PN-F',
    quantity_flow_ids: [340, 341],
    device_event_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
  });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await lastAction()).toHaveTextContent(
    'MERGED · 1 pcs + 2 pcs → 3 pcs in Plating',
  );
  // The consumed portions are gone, the result is here, the totals hold.
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
  await waitFor(() => expect(rowsOf(summaryCard(), 'PN-F')).toHaveLength(2));
  expect(rowsOf(summaryCard(), 'PN-F').map((r) => r.textContent)).toEqual([
    expect.stringContaining('3 pcs'),
    expect.stringContaining('3 pcs'),
  ]);
  expect(
    within(stats).getByText('Processing').previousSibling,
  ).toHaveTextContent('25');
  expect(
    flows
      .filter((f) => f.pn === 'PN-F')
      .map((f) => f.qty)
      .sort(),
  ).toEqual([3, 3]);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a combine the server refuses is shown in place with nothing recorded; Back is withdrawn and the Area is re-read', async () => {
  await renderStation('PLATING-ST-01');
  const inventoryReads = reads(/\/inventory$/).length;
  scan('PF:PN:PN-D');
  fireEvent.click(
    within(
      await screen.findByRole('dialog', { name: 'Select an action' }),
    ).getByRole('button', { name: /Combine quantities/ }),
  );
  const dlg = await screen.findByRole('dialog', { name: 'Combine quantities' });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  // The context changed meanwhile: the server judges the portions
  // incompatible now (one finished) — an explicit refusal.
  flows.find((f) => f.id === 321)!.state = 'READY_TO_TRANSFER';
  fireEvent.click(within(dlg).getByRole('button', { name: 'Confirm combine' }));
  await waitFor(() =>
    expect(dlg).toHaveTextContent('These Quantity Flows cannot be merged'),
  );
  expect(dlg).toHaveTextContent('Nothing was recorded');
  expect(within(dlg).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(dlg).getByRole('button', { name: 'Retry combine' }),
  ).toBeEnabled();
  expect(committed.size).toBe(0);
  expect(flows.filter((f) => f.pn === 'PN-D')).toHaveLength(3);
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await notice()).toHaveTextContent('No changes were recorded');
});

test('a combine whose response was lost retries under the same device_event_id and replays the recorded combine — never a second one', async () => {
  const input = await renderStation('PLATING-ST-01');
  scan('PF:PN:PN-D');
  fireEvent.click(
    within(
      await screen.findByRole('dialog', { name: 'Select an action' }),
    ).getByRole('button', { name: /Combine quantities/ }),
  );
  const dlg = await screen.findByRole('dialog', { name: 'Combine quantities' });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  writeFailure = 'lost-response';
  fireEvent.click(within(dlg).getByRole('button', { name: 'Confirm combine' }));
  const retry = await screen.findByRole('button', {
    name: 'Retry the same combine',
  });
  expect(dlg).toHaveTextContent('may or may not have been recorded');
  expect(within(dlg).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(dlg).getByRole('button', { name: 'Leave — check the Area' }),
  ).toBeInTheDocument();
  expect(writes()).toHaveLength(1);
  fireEvent.click(retry);
  const toast = await notice();
  expect(toast).toHaveTextContent(
    'already recorded by the server (MERGED #500)',
  );
  expect(toast).toHaveTextContent('nothing was recorded twice');
  expect(writes()).toHaveLength(2);
  expect(writes()[1].body).toEqual(writes()[0].body);
  expect(committed.size).toBe(1);
  expect(
    flows
      .filter((f) => f.pn === 'PN-D')
      .map((f) => f.qty)
      .sort(),
  ).toEqual([10, 2]);
  await waitFor(() => expect(document.activeElement).toBe(input));
});
