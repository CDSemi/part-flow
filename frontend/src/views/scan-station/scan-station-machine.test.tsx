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

// Real Scan Station (Phase 6) against a fake in-memory `/api` with the
// backend's Machine-Area surface and semantics: Machine barcode
// resolution into the one-shot assignment context, the three in-Area
// commands (assign / QUEUE / DONE — idempotent per device_event_id,
// replay 200, rejections with nothing recorded), the transfer that
// completes ON_MACHINE quantity implicitly, and the Area inventory split
// (queued / per Machine card / finished) the station renders and
// refreshes. Covers Machine-first and PN-first entry, the keyboard flow,
// cancel, stale responses, retry replay with the SAME device_event_id,
// the DONE / QUEUE distinction, implicit completion on transfer, the
// refreshes after success and focus restoration.

type State = 'QUEUED' | 'ON_MACHINE' | 'READY_TO_TRANSFER';

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: State;
  machineId: number | null;
}

interface FakeMachine {
  id: number;
  name: string;
  tag: string;
  areaId: number;
  maintenance: boolean;
  retired: boolean;
}

const AREAS = [
  { id: 2, name: 'Lathe', color: '#3366ff' },
  { id: 3, name: 'Cut', color: '#33aa66' },
  { id: 5, name: 'Mill', color: '#aa6633' },
];
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'TURNING', name: 'Turning' },
  { id: 30, area_id: 3, code: 'CUTTING', name: 'Cutting' },
];
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 2 },
  { station_id: 'CUT-ST-01', area_id: 3 },
];
const MACHINES: FakeMachine[] = [
  {
    id: 1,
    name: 'Lathe 1',
    tag: 'CD-0001',
    areaId: 2,
    maintenance: false,
    retired: false,
  },
  {
    id: 2,
    name: 'Lathe 2',
    tag: 'CD-0002',
    areaId: 2,
    maintenance: false,
    retired: false,
  },
  {
    id: 3,
    name: 'Lathe 3',
    tag: 'CD-0003',
    areaId: 2,
    maintenance: true,
    retired: false,
  },
  {
    id: 4,
    name: 'Mill 1',
    tag: 'CD-0004',
    areaId: 5,
    maintenance: false,
    retired: false,
  },
  {
    id: 9,
    name: 'Lathe 9',
    tag: 'CD-0009',
    areaId: 2,
    maintenance: false,
    retired: true,
  },
];

let flows: Flow[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
/** Failure injected into the NEXT write (any command). */
let writeFailure:
  null | 'network' | 'lost-response' | { status: number; body: unknown };

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

function machineRef(machine: FakeMachine, assigned: number) {
  return {
    id: machine.id,
    name: machine.name,
    asset_tag: machine.tag,
    barcode_value: `PF:MACHINE:${machine.tag}`,
    operational_state: machine.maintenance
      ? 'MAINTENANCE'
      : assigned > 0
        ? 'RUNNING'
        : 'IDLE',
    state_changed_at: '2026-08-25T10:00:00Z',
    maintenance_since: machine.maintenance ? '2026-08-25T09:00:00Z' : null,
    maintenance_note: machine.maintenance ? 'belt' : null,
    maintenance_expected_return: null,
  };
}

function actionsOf(state: State) {
  return state === 'QUEUED'
    ? ['ASSIGN', 'TRANSFER']
    : state === 'ON_MACHINE'
      ? ['DONE', 'QUEUE', 'TRANSFER']
      : ['TRANSFER'];
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    processing_state: flow.state,
    machine_id: flow.machineId,
    available_actions: actionsOf(flow.state),
    work_order: {
      work_order_id: 1,
      work_order_number: '007003',
      work_order_demand_id: 11,
      request_type: 'NEW',
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

function assignedTo(machineId: number) {
  return flows
    .filter((f) => f.state === 'ON_MACHINE' && f.machineId === machineId)
    .reduce((s, f) => s + f.qty, 0);
}

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const all = lines(here);
  return json({
    area: areaRef(areaId),
    lines: all,
    total_part_numbers: all.length,
    total_quantity: here.reduce((s, f) => s + f.qty, 0),
    queued: lines(byState('QUEUED')),
    queued_quantity: byState('QUEUED').reduce((s, f) => s + f.qty, 0),
    machines: MACHINES.filter((m) => m.areaId === areaId && !m.retired).map(
      (m) => {
        const held = here.filter(
          (f) => f.state === 'ON_MACHINE' && f.machineId === m.id,
        );
        return {
          machine: machineRef(m, assignedTo(m.id)),
          lines: lines(held),
          total_quantity: held.reduce((s, f) => s + f.qty, 0),
        };
      },
    ),
    on_machine_quantity: byState('ON_MACHINE').reduce((s, f) => s + f.qty, 0),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: byState('READY_TO_TRANSFER').reduce(
      (s, f) => s + f.qty,
      0,
    ),
  });
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
    return json([{ id: 1, name: 'Machining', is_active: true }]);
  }
  if (url === '/api/operations') {
    return json(
      OPERATIONS.map((o) => ({
        ...o,
        is_external: false,
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
      department: { id: 1, name: 'Machining' },
      area: areaRef(station.area_id),
      operations: OPERATIONS.filter((o) => o.area_id === station.area_id).map(
        (o) => ({ ...o, is_external: false }),
      ),
      has_machines: station.area_id === 2,
    });
  }
  const inv = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inv) return inventory(Number(inv[1]));

  if (/\/scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const input = body as { barcode?: string; part_number?: string };
    const scanned = input.barcode ?? `PF:PN:${input.part_number}`;
    if (scanned.startsWith('PF:MACHINE:')) {
      return json({ detail: 'This is a Machine barcode (PF:MACHINE:…).' }, 422);
    }
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
        suggested_operation_id: OPERATIONS.find(
          (o) => o.area_id === station.area_id,
        )!.id,
      })),
      operations: OPERATIONS.filter((o) => o.area_id === station.area_id).map(
        (o) => ({ ...o, is_external: false }),
      ),
      has_active_demand: true,
      transfer_blocked_reason: null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
    });
  }

  if (/\/machine-scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const input = body as { barcode?: string; asset_tag?: string };
    const tag = input.asset_tag ?? input.barcode!.slice('PF:MACHINE:'.length);
    const machine = MACHINES.find((m) => m.tag === tag);
    if (!machine) {
      return json({ detail: `No Machine has the Asset Tag '${tag}'.` }, 404);
    }
    if (machine.retired) {
      return json(
        {
          detail: `Machine '${machine.name}' is retired and accepts no scans.`,
        },
        409,
      );
    }
    if (machine.areaId !== station.area_id) {
      return json(
        { detail: `Machine '${machine.name}' belongs to another Area.` },
        409,
      );
    }
    if (machine.maintenance) {
      return json(
        {
          detail: `Machine '${machine.name}' is under maintenance and accepts no new assignment.`,
        },
        409,
      );
    }
    const queued = flows.filter(
      (f) => f.areaId === station.area_id && f.state === 'QUEUED',
    );
    return json({
      station_id: station.station_id,
      area: areaRef(station.area_id),
      machine: machineRef(machine, assignedTo(machine.id)),
      assigned_quantity: assignedTo(machine.id),
      queued: queued.map(flowWire),
      requires_selection: queued.length > 1,
    });
  }

  const action =
    /\/(machine-assignments|machine-releases|area-completions)$/.exec(url);
  if (action && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      machine_id: number;
      quantity: number;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const flow = flows.find((f) => f.id === request.quantity_flow_id);
    const machine = MACHINES.find((m) => m.id === request.machine_id);
    if (!flow || !machine) return json({ detail: 'does not exist.' }, 422);
    if (request.quantity !== flow.qty) {
      return json({ detail: 'Partial action is not supported yet.' }, 422);
    }
    if (flow.areaId !== station.area_id) {
      return json({ detail: 'Quantity Flow is not in the Area.' }, 409);
    }
    const kind = action[1];
    let type: string;
    if (kind === 'machine-assignments') {
      if (flow.state !== 'QUEUED') {
        return json(
          { detail: `Quantity Flow ${flow.id} is already on a Machine.` },
          409,
        );
      }
      if (machine.retired) {
        return json({ detail: `Machine '${machine.name}' is retired.` }, 409);
      }
      if (machine.maintenance) {
        return json(
          { detail: `Machine '${machine.name}' is under maintenance.` },
          409,
        );
      }
      flow.state = 'ON_MACHINE';
      flow.machineId = machine.id;
      type = 'ASSIGNED_TO_MACHINE';
    } else {
      if (flow.state !== 'ON_MACHINE' || flow.machineId !== machine.id) {
        return json(
          {
            detail: `Quantity Flow ${flow.id} is not on the selected Machine.`,
          },
          409,
        );
      }
      flow.machineId = null;
      flow.state = kind === 'area-completions' ? 'READY_TO_TRANSFER' : 'QUEUED';
      type =
        kind === 'area-completions'
          ? 'AREA_COMPLETED'
          : 'RELEASED_FROM_MACHINE';
    }
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      movement_type: type,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      area_id: flow.areaId,
      machine_id: machine.id,
      operation_id: 20,
      station_id: station.station_id,
      processing_state: flow.state,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-26T12:00:00Z',
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
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    const completedFrom = flow.state === 'ON_MACHINE' ? flow.machineId : null;
    const completedMovementId =
      completedFrom !== null ? nextMovementId++ : null;
    flow.areaId = station.area_id;
    flow.state = 'QUEUED';
    flow.machineId = null;
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      from_area_id: request.source_area_id,
      to_area_id: station.area_id,
      operation_id: request.operation_id ?? 30,
      station_id: station.station_id,
      assigned_route_step_id: null,
      route_deviation: null,
      completed_movement_id: completedMovementId,
      completed_machine_id: completedFrom,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-26T12:00:00Z',
    });
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
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 101,
      pn: '118-052',
      qty: 5,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 102,
      pn: '118-052',
      qty: 7,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 103,
      pn: '0455-20-0118-03',
      qty: 3,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 1,
    },
    {
      id: 104,
      pn: 'DONE-1',
      qty: 4,
      areaId: 2,
      state: 'READY_TO_TRANSFER',
      machineId: null,
    },
    {
      id: 105,
      pn: 'CUT-1',
      qty: 6,
      areaId: 3,
      state: 'QUEUED',
      machineId: null,
    },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  writeFailure = null;
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

async function renderStation(stationId = 'LATHE-ST-01') {
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
      /\/(machine-assignments|machine-releases|area-completions|transfers)$/.test(
        r.url,
      ),
  );
}

function reads(pattern: RegExp) {
  return requests.filter((r) => r.method === 'GET' && pattern.test(r.url));
}

function machineCard(name: string): HTMLElement {
  const heading = screen.getByText(name, { selector: '.mname' });
  return heading.closest('.abd-machine') as HTMLElement;
}

async function lastAction() {
  return waitFor(() => document.querySelector('.ss-lastpn .d') as HTMLElement);
}

/* ============ Inventory presentation ============ */

test('the inventory separates queued, per-Machine and finished quantity', async () => {
  await renderStation();

  const summary = document.querySelector('.abd-summary') as HTMLElement;
  expect(
    within(summary).getByText('Area queue — awaiting Machine'),
  ).toBeInTheDocument();
  expect(
    within(summary).getByText('Finished — ready to move', {
      selector: '.abd-grp',
    }),
  ).toBeInTheDocument();
  expect(within(summary).getByText('DONE-1')).toBeInTheDocument();
  // The Machine card holds ONLY its ON_MACHINE quantity; finished
  // quantity is never on a card.
  const lathe1 = machineCard('Lathe 1');
  expect(lathe1).toHaveTextContent('running');
  expect(within(lathe1).getByText('0455-20-0118-03')).toBeInTheDocument();
  expect(within(lathe1).queryByText('DONE-1')).toBeNull();
  expect(lathe1).toHaveTextContent('3 pcs assigned');
  expect(
    within(lathe1).getByRole('button', { name: 'Complete Area processing' }),
  ).toBeInTheDocument();
  expect(
    within(lathe1).getByRole('button', { name: 'Return to Area queue' }),
  ).toBeInTheDocument();
  const lathe2 = machineCard('Lathe 2');
  expect(lathe2).toHaveTextContent('idle');
  expect(lathe2).toHaveTextContent('No production assigned');
  expect(machineCard('Lathe 3')).toHaveTextContent('Under maintenance');
  expect(screen.queryByText('Lathe 9')).toBeNull(); // retired: no card
  // The summary card carries no row actions in an Area with Machines.
  expect(
    within(summary).queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
  // Header totals reconcile from the server's states.
  const stats = screen.getByLabelText('Area statistics');
  expect(stats).toHaveTextContent('Queued');
  expect(within(stats).getByText('24')).toBeInTheDocument(); // 12 + 5 + 7
  expect(within(stats).getByText('3')).toBeInTheDocument(); // on machines
  expect(within(stats).getByText('4')).toBeInTheDocument(); // done
});

test('an Area without Machines shows no Machine cards and no DONE action', async () => {
  await renderStation('CUT-ST-01');
  expect(document.querySelector('.abd-machine')).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
  expect(
    screen.getByText('In processing', { selector: '.abd-grp' }),
  ).toBeInTheDocument();

  scan('PF:PN:CUT-1');
  const actions = await screen.findByRole('dialog');
  expect(within(actions).queryByText('Assign to Machine')).toBeNull();
  expect(actions).toHaveTextContent('Completion in an Area without Machines');
  fireEvent.keyDown(actions, { key: 'Escape' });
});

/* ============ Machine-first ============ */

test('Machine-first: a Machine scan opens Assign to Machine with the Machine preselected, a queued PN is chosen, reviewed and recorded', async () => {
  const input = await renderStation();
  const contextReads = reads(/\/context$/).length;
  const inventoryReads = reads(/\/inventory$/).length;

  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  expect(dlg).toHaveTextContent('This assignment applies once');
  const machineGroup = within(dlg).getByRole('group', { name: 'Machine' });
  expect(
    within(machineGroup).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(
    within(machineGroup).getByRole('button', { name: /Lathe 3/ }),
  ).toBeDisabled();
  // Every queued flow is an explicit choice — two separate 118-052 quantities.
  const pnGroup = within(dlg).getByRole('group', { name: /PN/ });
  expect(within(pnGroup).getAllByRole('button')).toHaveLength(3);
  expect(within(dlg).queryByText('0455-20-0118-03')).toBeNull(); // on a Machine, not queued
  expect(within(dlg).queryByText('DONE-1')).toBeNull(); // finished, not queued
  const next = within(dlg).getByRole('button', { name: 'Next' });
  expect(next).toBeDisabled();
  fireEvent.click(
    within(pnGroup).getByRole('button', { name: /2027-60-8114-00/ }),
  );
  fireEvent.click(next);

  expect(dialog()).toHaveTextContent('Assigning to Lathe 1');
  expect(within(dialog()).getByLabelText(/^Quantity: /)).toHaveValue('12');
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));

  const summary = dialog();
  expect(summary).toHaveTextContent('Review the assignment');
  expect(summary).toHaveTextContent('Destination Machine');
  expect(summary).toHaveTextContent('ASSIGNED_TO_MACHINE');
  expect(writes()).toHaveLength(0); // nothing recorded before Confirm
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm assignment' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('2027-60-8114-00 × 12 assigned to Lathe 1');
  expect(toast).toHaveTextContent('ASSIGNED_TO_MACHINE #500');
  const [request] = writes();
  expect(request.url).toMatch(/\/machine-assignments$/);
  expect(request.body).toMatchObject({
    part_number: '2027-60-8114-00',
    quantity_flow_id: 100,
    machine_id: 1,
    quantity: 12,
  });
  expect(request.body.device_event_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await lastAction()).toHaveTextContent(
    'ASSIGNED_TO_MACHINE · Area queue → Lathe 1 · qty 12',
  );
  // Station context, inventory and Machine cards refresh from the server.
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBe(inventoryReads + 1),
  );
  expect(reads(/\/context$/).length).toBe(contextReads + 1);
  await waitFor(() =>
    expect(machineCard('Lathe 1')).toHaveTextContent('15 pcs assigned'),
  );
  expect(
    within(machineCard('Lathe 1')).getByText('2027-60-8114-00'),
  ).toBeInTheDocument();
  await waitFor(() => expect(document.activeElement).toBe(input));

  // No Machine context survives: the next PN scan is an ordinary PN-first scan.
  scan('PF:PN:118-052');
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
});

test('Machine-first keyboard flow: the dialog scan input selects the queued PN and Enter advances through review to the confirmed write', async () => {
  await renderStation();
  scan('PF:MACHINE:CD-0002');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  const scanInput = within(dlg).getByLabelText(
    'Scan Machine or queued PN barcode',
  );
  await waitFor(() => expect(document.activeElement).toBe(scanInput));

  fireEvent.change(scanInput, { target: { value: 'PF:PN:2027-60-8114-00' } });
  fireEvent.keyDown(scanInput, { key: 'Enter' });
  const pnGroup = within(dlg).getByRole('group', { name: /PN/ });
  expect(
    within(pnGroup).getByRole('button', { name: /2027-60-8114-00/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  fireEvent.keyDown(scanInput, { key: 'Enter' }); // empty input: advance
  expect(dialog()).toHaveTextContent('Assigning to Lathe 2');
  fireEvent.keyDown(dialog(), { key: 'Enter' }); // quantity step: advance
  expect(dialog()).toHaveTextContent('Review the assignment');
  fireEvent.keyDown(dialog(), { key: 'Enter' }); // confirmation: submit

  expect(await notice()).toHaveTextContent('assigned to Lathe 2');
  expect(writes()[0].body).toMatchObject({
    machine_id: 2,
    quantity_flow_id: 100,
  });
});

test('a PN queued as several quantities must be selected explicitly inside the dialog', async () => {
  await renderStation();
  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  const scanInput = within(dlg).getByLabelText(
    'Scan Machine or queued PN barcode',
  );
  fireEvent.change(scanInput, { target: { value: 'PF:PN:118-052' } });
  fireEvent.keyDown(scanInput, { key: 'Enter' });
  expect(dlg).toHaveTextContent(
    '118-052 is queued as 2 separate quantities. Select exactly one below',
  );
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(scanInput, { target: { value: 'PF:PN:NOPE-1' } });
  fireEvent.keyDown(scanInput, { key: 'Enter' });
  expect(dlg).toHaveTextContent('NOPE-1 has no queued quantity in Lathe');
  expect(writes()).toHaveLength(0);
});

test('refused Machine scans are actionable errors that keep the station and existing selections intact', async () => {
  const input = await renderStation();
  for (const [barcode, detail] of [
    ['PF:MACHINE:CD-0003', 'under maintenance'],
    ['PF:MACHINE:CD-0004', 'belongs to another Area'],
    ['PF:MACHINE:CD-0009', 'retired'],
    ['PF:MACHINE:CD-7777', "No Machine has the Asset Tag 'CD-7777'"],
  ] as const) {
    scan(barcode);
    const toast = await notice();
    expect(toast).toHaveTextContent('Machine cannot be used here');
    expect(toast).toHaveTextContent(detail);
    expect(toast).toHaveTextContent('No changes were recorded');
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(within(toast).getByRole('button'));
  }
  await waitFor(() => expect(document.activeElement).toBe(input));

  // Inside the dialog a refused Machine scan never discards the choices made.
  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  const pnGroup = within(dlg).getByRole('group', { name: /PN/ });
  fireEvent.click(
    within(pnGroup).getByRole('button', { name: /2027-60-8114-00/ }),
  );
  const scanInput = within(dlg).getByLabelText(
    'Scan Machine or queued PN barcode',
  );
  fireEvent.change(scanInput, { target: { value: 'PF:MACHINE:CD-0003' } });
  fireEvent.keyDown(scanInput, { key: 'Enter' });
  await waitFor(() => expect(dlg).toHaveTextContent('under maintenance'));
  const machineGroup = within(dlg).getByRole('group', { name: 'Machine' });
  expect(
    within(machineGroup).getByRole('button', { name: /Lathe 1/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(
    within(pnGroup).getByRole('button', { name: /2027-60-8114-00/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeEnabled();
  expect(writes()).toHaveLength(0);
});

test('Cancel (Esc) abandons the assignment with nothing recorded and no Machine context kept', async () => {
  const input = await renderStation();
  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  fireEvent.click(
    within(within(dlg).getByRole('group', { name: /PN/ })).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.keyDown(dlg, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await notice()).toHaveTextContent(
    'Cancelled. No changes were recorded.',
  );
  expect(writes()).toHaveLength(0);
  await waitFor(() => expect(document.activeElement).toBe(input));
  expect(await lastAction()).toHaveTextContent('No Part Number actions yet');
});

/* ============ PN-first ============ */

test('PN-first: a queued PN offers Assign and opens the same wizard with the PN preselected', async () => {
  await renderStation();
  scan('PF:PN:2027-60-8114-00');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent(
    '12 pcs of this Part Number are already in Lathe',
  );
  fireEvent.click(
    within(actions).getByRole('button', { name: /Assign to Machine/ }),
  );

  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  const pnGroup = within(dlg).getByRole('group', { name: /PN/ });
  expect(
    within(pnGroup).getByRole('button', { name: /2027-60-8114-00/ }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeDisabled(); // Machine still to choose
  fireEvent.click(
    within(within(dlg).getByRole('group', { name: 'Machine' })).getByRole(
      'button',
      { name: /Lathe 2/ },
    ),
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm assignment' }),
  );
  expect(await notice()).toHaveTextContent(
    '2027-60-8114-00 × 12 assigned to Lathe 2',
  );
  expect(writes()[0].body).toMatchObject({
    machine_id: 2,
    quantity_flow_id: 100,
  });
  // Back from the wizard returns to the action dialog (v20).
});

test('PN-first: several separate quantities of one PN are several explicit choices, and a Machine flow offers completion on that Machine', async () => {
  await renderStation();
  scan('PF:PN:118-052');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent('Several separate quantities are here');
  const assigns = within(actions).getAllByRole('button', {
    name: /Assign to Machine/,
  });
  expect(assigns).toHaveLength(2);
  expect(assigns[0]).toHaveTextContent('5 pcs queued');
  expect(assigns[1]).toHaveTextContent('7 pcs queued');
  fireEvent.click(assigns[1]);
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  const pressed = within(within(dlg).getByRole('group', { name: /PN/ }))
    .getAllByRole('button')
    .filter((b) => b.getAttribute('aria-pressed') === 'true');
  expect(pressed).toHaveLength(1);
  expect(pressed[0]).toHaveTextContent('queued 7');
  // Back returns to the action dialog with its choices intact.
  fireEvent.click(within(dlg).getByRole('button', { name: '‹ Back' }));
  expect(
    await screen.findByRole('dialog', { name: 'Select an action' }),
  ).toBeInTheDocument();
  fireEvent.keyDown(dialog(), { key: 'Escape' });

  scan('PF:PN:0455-20-0118-03');
  const onMachine = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(
    within(onMachine).queryByRole('button', { name: /Assign to Machine/ }),
  ).toBeNull();
  fireEvent.click(
    within(onMachine).getByRole('button', {
      name: /Complete Area processing on Lathe 1/,
    }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Complete Area processing' }),
  ).toBeInTheDocument();
});

/* ============ Stale responses and retry ============ */

test('a server rejection of a stale assignment is shown in place with nothing recorded', async () => {
  await renderStation();
  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  fireEvent.click(
    within(within(dlg).getByRole('group', { name: /PN/ })).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  // Meanwhile another station assigned the flow: the server refuses.
  flows.find((f) => f.id === 100)!.state = 'ON_MACHINE';
  flows.find((f) => f.id === 100)!.machineId = 2;
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm assignment' }),
  );

  const summary = dialog();
  await waitFor(() =>
    expect(summary).toHaveTextContent(
      'Quantity Flow 100 is already on a Machine.',
    ),
  );
  expect(
    within(summary).getByRole('button', { name: 'Retry assignment' }),
  ).toBeInTheDocument();
  expect(committed.size).toBe(0);
  expect(document.querySelector('.ss-toast')).toBeNull();
  fireEvent.keyDown(summary, { key: 'Escape' });
  expect(await notice()).toHaveTextContent(
    'Cancelled. No changes were recorded.',
  );
});

test('a lost response freezes the intent and the retry replays the committed assignment under the same device_event_id', async () => {
  await renderStation();
  scan('PF:MACHINE:CD-0001');
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  fireEvent.click(
    within(within(dlg).getByRole('group', { name: /PN/ })).getByRole('button', {
      name: /2027-60-8114-00/,
    }),
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next' }));
  writeFailure = 'lost-response';
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm assignment' }),
  );

  const summary = dialog();
  await waitFor(() =>
    expect(summary).toHaveTextContent('may or may not have been recorded'),
  );
  expect(summary).not.toHaveTextContent('Nothing was recorded');
  expect(within(summary).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(summary).getByRole('button', { name: 'Leave — check the Area' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retry the same assignment' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('already recorded by the server');
  const sent = writes();
  expect(sent).toHaveLength(2);
  expect(JSON.stringify(sent[0].body)).toBe(JSON.stringify(sent[1].body));
  expect(committed.size).toBe(1);
  expect(nextMovementId).toBe(501);
  expect(screen.queryByRole('dialog')).toBeNull();
});

/* ============ DONE / QUEUE ============ */

test('DONE completes processing through its summary and final question and moves the quantity to the finished rack', async () => {
  const input = await renderStation();
  fireEvent.click(
    within(machineCard('Lathe 1')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );

  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  expect(dlg).toHaveTextContent('3 pcs are on Lathe 1');
  expect(within(dlg).getByLabelText(/^Quantity: /)).toHaveValue('3');
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Finished — ready to move');
  expect(summary).toHaveTextContent('AREA_COMPLETED');
  expect(summary).not.toHaveTextContent('RELEASED_FROM_MACHINE');
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm completion' }),
  );

  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  expect(gate).toHaveTextContent(
    'Are you sure Lathe 1 has finished 3 pcs of 0455-20-0118-03?',
  );
  expect(writes()).toHaveLength(0);
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));

  const toast = await notice();
  expect(toast).toHaveTextContent('finished on Lathe 1');
  expect(toast).toHaveTextContent('AREA_COMPLETED #500');
  expect(writes()[0].url).toMatch(/\/area-completions$/);
  expect(writes()[0].body).toMatchObject({
    quantity_flow_id: 103,
    machine_id: 1,
    quantity: 3,
  });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await lastAction()).toHaveTextContent(
    'AREA_COMPLETED · Lathe 1 → Finished — ready to move · qty 3',
  );
  await waitFor(() =>
    expect(machineCard('Lathe 1')).toHaveTextContent('No production assigned'),
  );
  expect(machineCard('Lathe 1')).toHaveTextContent('idle');
  const summaryCard = document.querySelector('.abd-summary') as HTMLElement;
  const finished = within(summaryCard).getByText('Finished — ready to move', {
    selector: '.abd-grp',
  });
  expect(finished).toBeInTheDocument();
  expect(within(summaryCard).getByText('0455-20-0118-03')).toBeInTheDocument();
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('QUEUE returns the quantity to the queue as a distinct action that never reads as completion', async () => {
  await renderStation();
  fireEvent.click(
    within(machineCard('Lathe 1')).getByRole('button', {
      name: 'Return to Area queue',
    }),
  );

  const dlg = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('RELEASED_FROM_MACHINE');
  expect(summary).toHaveTextContent('Queued — awaiting Machine');
  expect(summary).not.toHaveTextContent('Finished');
  expect(summary).not.toHaveTextContent('AREA_COMPLETED');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm return to queue' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity?',
  });
  expect(gate).toHaveTextContent('The quantity stays unfinished.');
  fireEvent.click(
    within(gate).getByRole('button', { name: 'Yes — return to queue' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent(
    'returned from Lathe 1 to the Lathe queue — it remains unfinished',
  );
  expect(writes()[0].url).toMatch(/\/machine-releases$/);
  await waitFor(() =>
    expect(machineCard('Lathe 1')).toHaveTextContent('No production assigned'),
  );
  const summaryCard = document.querySelector('.abd-summary') as HTMLElement;
  expect(within(summaryCard).getByText('0455-20-0118-03')).toBeInTheDocument();
  expect(
    within(summaryCard).getAllByText('Awaiting Machine').length,
  ).toBeGreaterThan(0);
});

test('the final question can be declined, and a partial DONE quantity is refused and never submitted', async () => {
  await renderStation();
  fireEvent.click(
    within(machineCard('Lathe 1')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.change(within(dlg).getByLabelText(/^Quantity: /), {
    target: { value: '2' },
  });
  expect(dlg).toHaveTextContent(
    'Partial completion is not available in this release',
  );
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.keyDown(dlg, { key: 'Enter' });
  expect(dlg).toHaveTextContent('Partial completion');
  fireEvent.change(within(dlg).getByLabelText(/^Quantity: /), {
    target: { value: '3' },
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Confirm finished quantity?' }),
    ).toBeNull(),
  );
  expect(
    screen.getByRole('dialog', { name: 'Complete Area processing' }),
  ).toHaveTextContent('AREA_COMPLETED');
  expect(writes()).toHaveLength(0);
});

test('a stale QUEUE (the quantity left the Machine meanwhile) is refused with nothing recorded', async () => {
  await renderStation();
  fireEvent.click(
    within(machineCard('Lathe 1')).getByRole('button', {
      name: 'Return to Area queue',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Return unfinished quantity to queue',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  flows.find((f) => f.id === 103)!.state = 'READY_TO_TRANSFER';
  flows.find((f) => f.id === 103)!.machineId = null;
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm return to queue' }),
  );
  fireEvent.click(
    within(
      await screen.findByRole('dialog', {
        name: 'Return unfinished quantity?',
      }),
    ).getByRole('button', { name: 'Yes — return to queue' }),
  );
  await waitFor(() =>
    expect(dialog()).toHaveTextContent('not on the selected Machine'),
  );
  expect(committed.size).toBe(0);
  expect(
    within(dialog()).getByRole('button', { name: 'Retry queue return' }),
  ).toBeInTheDocument();
});

/* ============ Implicit completion on transfer ============ */

test('transferring ON_MACHINE quantity announces the implicit completion and reports both recorded events', async () => {
  await renderStation('CUT-ST-01');
  scan('PF:PN:0455-20-0118-03');
  const dlg = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(dlg).toHaveTextContent(
    'still on a Machine at Lathe: transferring it completes that processing first',
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Completed at Lathe by this transfer');
  expect(summary).toHaveTextContent(
    'AREA_COMPLETED + TRANSFERRED (one command)',
  );
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent(
    'Processing at Lathe was completed and the quantity moved here',
  );
  expect(toast).toHaveTextContent('AREA_COMPLETED #500 + TRANSFERRED #501');
  expect(await lastAction()).toHaveTextContent(
    'AREA_COMPLETED + TRANSFERRED · Lathe → Cut',
  );
  expect(writes()).toHaveLength(1);
  expect(committed.size).toBe(1);

  // Queued quantity transfers with TRANSFERRED alone.
  scan('PF:PN:2027-60-8114-00');
  const plain = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(plain).not.toHaveTextContent('completes that processing');
  fireEvent.click(within(plain).getByRole('button', { name: 'Next' }));
  expect(dialog()).not.toHaveTextContent('AREA_COMPLETED');
});
