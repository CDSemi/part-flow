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

// Real Scan Station (Phase 7 — direct Area processing) against a fake
// in-memory `/api` with the backend's Phase 7 semantics: an Area
// without Machines holds arriving quantity as PROCESSING (no queue,
// Machine null, the Operation recorded), the DONE without a Machine
// (`area-completions` with no `machine_id`) appends one AREA_COMPLETED,
// a transfer of PROCESSING quantity completes it implicitly
// (AREA_COMPLETED + TRANSFERRED, one command), finished quantity
// transfers with TRANSFERRED alone, and the read models carry the Area
// mode (`has_machines`) and the `processing` split. Covers the
// direct-processing inventory presentation (In processing / External
// processing by the flow's recorded Operation), the row DONE and the
// PN-first `Complete Area processing` choice, the wizard without a
// Machine field, success only after the server, rejection and unknown
// outcome, the implicit completion on transfer, the finished-quantity
// transfer without a duplicate completion, and the disconnected guard.

type State = 'QUEUED' | 'PROCESSING' | 'ON_MACHINE' | 'READY_TO_TRANSFER';

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: State;
  machineId: number | null;
}

const AREAS = [
  { id: 2, name: 'Lathe', color: '#3366ff' },
  { id: 3, name: 'Deburr', color: '#33aa66' },
  { id: 6, name: 'Plating', color: '#aa33aa' },
];
/** Plating's Operation is external (an outside vendor). */
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'TURNING', name: 'Turning', is_external: false },
  { id: 30, area_id: 3, code: 'DEBURR', name: 'Deburring', is_external: false },
  { id: 60, area_id: 6, code: 'PLATE', name: 'Plating', is_external: true },
];
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 2 },
  { station_id: 'DEBURR-ST-01', area_id: 3 },
  { station_id: 'PLATING-ST-01', area_id: 6 },
];
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

/** The Area mode follows from its Machines: only Lathe has one. */
const hasMachines = (areaId: number) => areaId === 2;

let flows: Flow[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let writeFailure:
  null | 'network' | 'lost-response' | { status: number; body: unknown };
let healthDown: boolean;

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

function actionsOf(state: State) {
  return state === 'QUEUED'
    ? ['ASSIGN', 'TRANSFER']
    : state === 'ON_MACHINE'
      ? ['DONE', 'QUEUE', 'TRANSFER']
      : state === 'PROCESSING'
        ? ['DONE', 'TRANSFER']
        : ['TRANSFER'];
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation_id: operationsOf(flow.areaId)[0].id,
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

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const sum = (items: Flow[]) => items.reduce((s, f) => s + f.qty, 0);
  const all = lines(here);
  const onMachine = byState('ON_MACHINE');
  return json({
    area: areaRef(areaId),
    has_machines: hasMachines(areaId),
    lines: all,
    total_part_numbers: all.length,
    total_quantity: sum(here),
    queued: lines(byState('QUEUED')),
    queued_quantity: sum(byState('QUEUED')),
    machines: hasMachines(areaId)
      ? [
          {
            machine: {
              ...LATHE_1,
              operational_state: onMachine.length ? 'RUNNING' : 'IDLE',
            },
            lines: lines(onMachine),
            total_quantity: sum(onMachine),
          },
        ]
      : [],
    on_machine_quantity: sum(onMachine),
    processing: lines(byState('PROCESSING')),
    processing_quantity: sum(byState('PROCESSING')),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: sum(byState('READY_TO_TRANSFER')),
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
      })),
      operations: operationsOf(station.area_id),
      has_active_demand: true,
      transfer_blocked_reason: null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
    });
  }

  if (/\/area-completions$/.test(url) && method === 'POST') {
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
    const flow = flows.find((f) => f.id === request.quantity_flow_id);
    if (!flow) return json({ detail: 'does not exist.' }, 422);
    if (request.quantity !== flow.qty) {
      return json({ detail: 'Partial completion is not supported yet.' }, 422);
    }
    if (flow.areaId !== station.area_id) {
      return json({ detail: 'Quantity Flow is not in the Area.' }, 409);
    }
    // The backend's two DONE intents: with a Machine (ON_MACHINE only)
    // or without one (PROCESSING only — an Area without Machines).
    if (request.machine_id === undefined) {
      if (hasMachines(station.area_id)) {
        return json({ detail: `Area has Machines.` }, 409);
      }
      if (flow.state !== 'PROCESSING') {
        return json(
          {
            detail: `Quantity Flow ${flow.id} has already completed processing.`,
          },
          409,
        );
      }
    } else if (flow.state !== 'ON_MACHINE') {
      return json({ detail: 'not on a Machine.' }, 409);
    }
    flow.state = 'READY_TO_TRANSFER';
    flow.machineId = null;
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      movement_type: 'AREA_COMPLETED',
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      area_id: flow.areaId,
      machine_id: request.machine_id ?? null,
      operation_id: operationsOf(flow.areaId)[0].id,
      station_id: station.station_id,
      processing_state: flow.state,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-27T12:00:00Z',
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
    const completesSource =
      flow.state === 'ON_MACHINE' || flow.state === 'PROCESSING';
    const completedFrom = flow.state === 'ON_MACHINE' ? flow.machineId : null;
    const completedMovementId = completesSource ? nextMovementId++ : null;
    flow.areaId = station.area_id;
    flow.state = hasMachines(station.area_id) ? 'QUEUED' : 'PROCESSING';
    flow.machineId = null;
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      from_area_id: request.source_area_id,
      to_area_id: station.area_id,
      operation_id: request.operation_id ?? operationsOf(station.area_id)[0].id,
      station_id: station.station_id,
      assigned_route_step_id: null,
      route_deviation: null,
      completed_movement_id: completedMovementId,
      completed_machine_id: completedFrom,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-27T12:00:00Z',
    });
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    // Plating (external Operation, no Machines): one processing, one finished.
    {
      id: 200,
      pn: '2027-60-8114-00',
      qty: 12,
      areaId: 6,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 201,
      pn: 'PLT-DONE',
      qty: 4,
      areaId: 6,
      state: 'READY_TO_TRANSFER',
      machineId: null,
    },
    // Deburr (internal Operation, no Machines): two flows of one PN.
    {
      id: 210,
      pn: '118-052',
      qty: 5,
      areaId: 3,
      state: 'PROCESSING',
      machineId: null,
    },
    {
      id: 211,
      pn: '118-052',
      qty: 7,
      areaId: 3,
      state: 'PROCESSING',
      machineId: null,
    },
    // Lathe (Machines): queued quantity.
    {
      id: 220,
      pn: 'LATHE-Q',
      qty: 9,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  writeFailure = null;
  healthDown = false;
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

async function renderStation(stationId = 'PLATING-ST-01') {
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
    (r) => r.method === 'POST' && /\/(area-completions|transfers)$/.test(r.url),
  );
}

function reads(pattern: RegExp) {
  return requests.filter((r) => r.method === 'GET' && pattern.test(r.url));
}

function summaryCard(): HTMLElement {
  return document.querySelector('.abd-summary') as HTMLElement;
}

/** The PN row of the `In this Area now` card. */
function row(pn: string): HTMLElement {
  return within(summaryCard()).getByText(pn).closest('li') as HTMLElement;
}

function summaryTerms(box: HTMLElement) {
  return within(box)
    .getAllByRole('term')
    .map((term) => term.textContent);
}

async function lastAction() {
  return waitFor(() => document.querySelector('.ss-lastpn .d') as HTMLElement);
}

/* ============ Inventory presentation ============ */

test('an Area without Machines renders direct processing from the server state: no queue, no Machine cards, processing and finished groups, DONE on the processing rows only', async () => {
  await renderStation();

  expect(document.querySelector('.abd-machine')).toBeNull();
  expect(document.querySelector('.am-single')).not.toBeNull();
  const summary = summaryCard();
  expect(
    within(summary).getByText('In processing', { selector: '.abd-grp' }),
  ).toBeInTheDocument();
  expect(
    within(summary).queryByText('Area queue — awaiting Machine'),
  ).toBeNull();
  expect(within(summary).queryByText('On Machines')).toBeNull();
  expect(
    within(summary).getByText('Finished — ready to move', {
      selector: '.abd-grp',
    }),
  ).toBeInTheDocument();
  // The processing row's status follows the flow's recorded Operation:
  // Plating's Operation is external → `External processing`.
  const processing = row('2027-60-8114-00');
  expect(processing).toHaveTextContent('External processing');
  expect(processing).toHaveTextContent('12 pcs');
  expect(
    within(processing).getByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeInTheDocument();
  expect(
    within(processing).queryByRole('button', { name: 'Return to Area queue' }),
  ).toBeNull();
  // A finished row never carries the action.
  const finished = row('PLT-DONE');
  expect(finished).toHaveTextContent('Finished — ready to move');
  expect(
    within(finished).queryByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeNull();
  // Header totals: the direct-processing statistics, no queue figures.
  const stats = screen.getByLabelText('Area statistics');
  expect(stats).toHaveTextContent('Processing');
  expect(stats).not.toHaveTextContent('Queued');
  expect(stats).not.toHaveTextContent('On machines');
  expect(
    within(stats).getByText('Processing').previousSibling,
  ).toHaveTextContent('12');
  expect(within(stats).getByText('Done').previousSibling).toHaveTextContent(
    '4',
  );
  expect(screen.getByText(/complete its processing here/)).toBeInTheDocument();
});

test('an internal Operation reads `In processing`; several flows of one PN are several rows, each with its own DONE', async () => {
  await renderStation('DEBURR-ST-01');
  const rows = within(summaryCard())
    .getAllByText('118-052')
    .map((pn) => pn.closest('li') as HTMLElement);
  expect(rows).toHaveLength(2);
  for (const item of rows) {
    expect(item).toHaveTextContent('In processing');
    expect(item).not.toHaveTextContent('External processing');
    expect(
      within(item).getByRole('button', { name: 'Complete Area processing' }),
    ).toBeInTheDocument();
  }
  const stats = screen.getByLabelText('Area statistics');
  expect(
    within(stats).getByText('Processing').previousSibling,
  ).toHaveTextContent('12');
});

test('an Area with Machines is unchanged: no row action on the summary card, the queue group renders', async () => {
  await renderStation('LATHE-ST-01');
  const summary = summaryCard();
  expect(
    within(summary).getByText('Area queue — awaiting Machine'),
  ).toBeInTheDocument();
  expect(within(summary).queryByText('In processing')).toBeNull();
  expect(
    within(summary).queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
  expect(document.querySelector('.abd-machine')).not.toBeNull();
});

/* ============ Direct DONE from the row action ============ */

test('the row DONE opens the Area Completion wizard without a Machine field, records exactly one Machine-less completion after the server confirmed it, refreshes the Area and restores focus', async () => {
  const input = await renderStation();
  const inventoryReads = reads(/\/inventory$/).length;
  fireEvent.click(
    within(row('2027-60-8114-00')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );

  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  expect(dlg).toHaveTextContent('12 pcs are in processing at Plating');
  expect(dlg).toHaveTextContent('Plating processing');
  expect(dlg).toHaveTextContent('Plating finished rack');
  expect(dlg).not.toHaveTextContent('Lathe');
  expect(within(dlg).getByLabelText(/^Quantity: /)).toHaveValue('12');
  // Opened from the row: no previous dialog, so no Back.
  expect(within(dlg).queryByRole('button', { name: '‹ Back' })).toBeNull();
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));

  const summary = dialog();
  const terms = summaryTerms(summary);
  expect(terms).toEqual([
    'Action',
    'PN',
    'Quantity',
    'Area',
    'Operation',
    'Result',
    'Scan Station',
    'Recorded event',
  ]);
  expect(terms).not.toContain('Machine');
  expect(summary).toHaveTextContent('Complete Area processing');
  expect(summary).toHaveTextContent('2027-60-8114-00');
  expect(summary).toHaveTextContent('12 pcs');
  expect(summary).toHaveTextContent('Plating');
  expect(summary).toHaveTextContent('Finished — ready to move');
  expect(summary).toHaveTextContent('PLATING-ST-01');
  expect(summary).toHaveTextContent('AREA_COMPLETED');
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm completion' }),
  );

  // The final question (information tone) — still nothing recorded.
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  expect(gate).toHaveTextContent(
    'Are you sure Plating has finished processing 12 pcs of 2027-60-8114-00?',
  );
  expect(writes()).toHaveLength(0);
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));

  const toast = await notice();
  expect(toast).toHaveTextContent('2027-60-8114-00 × 12 finished at Plating');
  expect(toast).toHaveTextContent(
    'Recorded by the server (AREA_COMPLETED #500)',
  );
  expect(writes()).toHaveLength(1);
  expect(writes()[0].url).toMatch(
    /\/scan-stations\/PLATING-ST-01\/area-completions$/,
  );
  expect(writes()[0].body).toEqual({
    part_number: '2027-60-8114-00',
    quantity_flow_id: 200,
    quantity: 12,
    device_event_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
  });
  expect(writes()[0].body).not.toHaveProperty('machine_id');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(await lastAction()).toHaveTextContent(
    'AREA_COMPLETED · Plating processing → Finished — ready to move · qty 12',
  );
  // The Area refreshed from the server: the quantity moved to the
  // finished group and lost its DONE; the header totals followed.
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
  await waitFor(() =>
    expect(row('2027-60-8114-00')).toHaveTextContent(
      'Finished — ready to move',
    ),
  );
  expect(
    within(summaryCard()).queryByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeNull();
  const stats = screen.getByLabelText('Area statistics');
  expect(
    within(stats).getByText('Processing').previousSibling,
  ).toHaveTextContent('0');
  expect(within(stats).getByText('Done').previousSibling).toHaveTextContent(
    '16',
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a partial quantity is refused inside the wizard and never submitted; the final question can be declined', async () => {
  await renderStation();
  fireEvent.click(
    within(row('2027-60-8114-00')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  const quantity = within(dlg).getByLabelText(/^Quantity: /);
  fireEvent.change(quantity, { target: { value: '5' } });
  expect(dlg).toHaveTextContent(/whole|as a whole|12 pcs/);
  expect(within(dlg).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.keyDown(quantity, { key: 'Enter' });
  expect(
    within(dlg).queryByRole('button', { name: 'Confirm completion' }),
  ).toBeNull();
  fireEvent.change(quantity, { target: { value: '12' } });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.queryByRole('dialog', { name: 'Confirm finished quantity?' }),
  ).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Complete Area processing' }),
  ).toBeInTheDocument();
  expect(writes()).toHaveLength(0);
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes()).toHaveLength(0);
  expect(await notice()).toHaveTextContent('No changes were recorded');
});

/* ============ PN-first ============ */

test('PN-first: a directly processing PN offers `Complete Area processing` (no Machine choices), Back returns to the action dialog, and the same command is sent', async () => {
  await renderStation('DEBURR-ST-01');
  scan('PF:PN:118-052');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).toHaveTextContent(
    '12 pcs of this Part Number are already in Deburr',
  );
  expect(actions).toHaveTextContent('Several separate quantities are here');
  const choices = within(actions).getAllByRole('button', {
    name: /^Complete Area processing/,
  });
  expect(choices).toHaveLength(2);
  expect(choices[0]).toHaveTextContent('5 pcs in processing');
  expect(choices[1]).toHaveTextContent('7 pcs in processing');
  expect(actions).not.toHaveTextContent('on Lathe');
  expect(within(actions).queryByText('Assign to Machine')).toBeNull();
  expect(within(actions).queryByText(/Return to Area queue/)).toBeNull();
  fireEvent.click(choices[1]);

  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  expect(dlg).toHaveTextContent('7 pcs are in processing at Deburr');
  // Back returns to the action dialog with its choices intact.
  fireEvent.click(within(dlg).getByRole('button', { name: '‹ Back' }));
  const again = await screen.findByRole('dialog', { name: 'Select an action' });
  expect(
    within(again).getAllByRole('button', { name: /^Complete Area processing/ }),
  ).toHaveLength(2);
  fireEvent.click(
    within(again).getAllByRole('button', {
      name: /^Complete Area processing/,
    })[1],
  );
  const wizard = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(wizard).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summary).toHaveTextContent('Deburring');
  expect(summaryTerms(summary)).not.toContain('Machine');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));
  expect(await notice()).toHaveTextContent('118-052 × 7 finished at Deburr');
  expect(writes()).toHaveLength(1);
  expect(writes()[0].body).toMatchObject({
    quantity_flow_id: 211,
    quantity: 7,
  });
  expect(writes()[0].body).not.toHaveProperty('machine_id');
  // The other flow of the PN is untouched and still processing.
  await waitFor(() =>
    expect(
      within(summaryCard()).getAllByRole('button', {
        name: 'Complete Area processing',
      }),
    ).toHaveLength(1),
  );
});

/* ============ Server refusal and unknown outcome ============ */

test('a server refusal is shown in place with nothing recorded, Back is withdrawn, and Cancel re-reads the Area', async () => {
  await renderStation();
  const inventoryReads = reads(/\/inventory$/).length;
  fireEvent.click(
    within(row('2027-60-8114-00')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  writeFailure = {
    status: 409,
    body: {
      detail:
        'Quantity Flow 200 has already completed processing at Area Plating (DONE) and waits for transfer. Nothing was recorded.',
    },
  };
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));

  await waitFor(() =>
    expect(dialog()).toHaveTextContent('has already completed processing'),
  );
  expect(screen.queryByText(/Recorded by the server/)).toBeNull();
  expect(committed.size).toBe(0);
  expect(within(dialog()).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(dialog()).getByRole('button', { name: 'Retry completion' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
});

test('a lost response is an unknown outcome: the exact retry keeps its device_event_id and the committed completion replays without a duplicate', async () => {
  await renderStation();
  fireEvent.click(
    within(row('2027-60-8114-00')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  writeFailure = 'lost-response';
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));

  const retry = await screen.findByRole('button', {
    name: 'Retry the same completion',
  });
  expect(dialog()).toHaveTextContent(/may or may not have been recorded/);
  expect(committed.size).toBe(1);
  const [first] = writes();
  fireEvent.click(retry);
  expect(await notice()).toHaveTextContent('already recorded by the server');
  expect(writes()).toHaveLength(2);
  expect(writes()[1].body).toEqual(first.body);
  expect(nextMovementId).toBe(501);
});

/* ============ Transfers ============ */

test('transferring directly processing quantity announces the implicit completion and reports both recorded events; finished quantity transfers with TRANSFERRED alone', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:2027-60-8114-00');
  const dlg = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(dlg).toHaveTextContent(
    'still in processing at Plating: transferring it completes that processing first',
  );
  expect(dlg).not.toHaveTextContent('on a Machine');
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summaryTerms(summary)).toContain('Recorded events');
  expect(summary).toHaveTextContent('Completed at Plating by this transfer');
  expect(summary).toHaveTextContent(
    'AREA_COMPLETED, then TRANSFERRED (one command)',
  );
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm transfer' }),
  );
  const toast = await notice();
  expect(toast).toHaveTextContent(
    '2027-60-8114-00 × 12 → Lathe queue (awaiting Machine)',
  );
  expect(toast).toHaveTextContent('Processing at Plating was completed');
  expect(toast).toHaveTextContent('AREA_COMPLETED #500 + TRANSFERRED #501');
  expect(await lastAction()).toHaveTextContent(
    'AREA_COMPLETED + TRANSFERRED · Plating → Lathe queue (awaiting Machine) · qty 12',
  );
  expect(writes()).toHaveLength(1);

  // Finished quantity: TRANSFERRED alone, no completion named twice.
  scan('PF:PN:PLT-DONE');
  const plain = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(plain).not.toHaveTextContent('completes that processing');
  fireEvent.click(within(plain).getByRole('button', { name: 'Next' }));
  expect(summaryTerms(dialog())).toContain('Recorded event');
  expect(summaryTerms(dialog())).not.toContain('Recorded events');
  expect(dialog()).not.toHaveTextContent('Source processing');
  expect(dialog()).not.toHaveTextContent('AREA_COMPLETED');
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  const second = await notice();
  expect(second).toHaveTextContent('PLT-DONE × 4');
  expect(second).toHaveTextContent('Recorded by the server (TRANSFERRED #502)');
  expect(second).not.toHaveTextContent('AREA_COMPLETED');
});

test('a transfer INTO an Area without Machines lands as direct processing', async () => {
  await renderStation('DEBURR-ST-01');
  scan('PF:PN:LATHE-Q');
  const dlg = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  expect(dlg).toHaveTextContent('Deburr — direct processing');
  expect(dlg).not.toHaveTextContent('completes that processing');
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  expect(await notice()).toHaveTextContent(
    'LATHE-Q × 9 → Deburr — direct processing',
  );
  await waitFor(() =>
    expect(row('LATHE-Q')).toHaveTextContent('In processing'),
  );
  expect(
    within(row('LATHE-Q')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  ).toBeInTheDocument();
});

/* ============ Disconnected ============ */

test('while disconnected the row DONE is disabled in place and the final Confirm stays disabled — nothing is sent', async () => {
  await renderStation();
  fireEvent.click(
    within(row('2027-60-8114-00')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  const confirm = within(dialog()).getByRole('button', {
    name: 'Confirm completion',
  });
  healthDown = true;
  await waitFor(() => expect(confirm).toBeDisabled(), { timeout: 4000 });
  fireEvent.click(confirm);
  fireEvent.keyDown(dialog(), { key: 'Enter' });
  expect(writes()).toHaveLength(0);
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  await waitFor(() =>
    expect(
      within(row('2027-60-8114-00')).getByRole('button', {
        name: 'Complete Area processing',
      }),
    ).toBeDisabled(),
  );
});
