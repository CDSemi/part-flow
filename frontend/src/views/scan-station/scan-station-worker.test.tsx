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

// Real Scan Station (Phase 13 — Area Worker ID modes) against a fake
// in-memory `/api` with the backend's wire contract: the station
// context carries the Area's `worker_identification` (Disabled, or
// Fixed Worker with its Worker), `POST …/badge-scans` answers a badge
// check (a read — nothing recorded) with `NOT_USED_IN_AREA` / `UNKNOWN`
// and the Area's current mode, and a transfer is recorded as in the
// Phase 5 suite. Covers the Worker pill (none in a Disabled Area, the
// Fixed Worker with avatar in both header layouts), the GUI §4.4 scan
// placeholder, badge scans answered with the not-used / unrecognized
// notices without touching the Last Scanned PN, the Undo target or the
// station, the local rejection of unknown `PF:` values, the offline
// guard, and the context freshness — re-read after a command, on every
// resolved scan and on a badge answer naming a different mode.

type Identification =
  | { mode: 'DISABLED'; fixed_worker: null }
  | {
      mode: 'FIXED';
      fixed_worker: {
        id: number;
        name: string;
        avatar_updated_at: string | null;
      };
    };

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
}

const DISABLED: Identification = { mode: 'DISABLED', fixed_worker: null };
const FIXED_NGUYEN: Identification = {
  mode: 'FIXED',
  fixed_worker: { id: 7, name: 'H. Nguyen', avatar_updated_at: null },
};

const AREAS = [
  { id: 2, name: 'Deburr', color: '#33aa66' },
  { id: 3, name: 'Cut', color: '#3366ff' },
];
const TURNING = { id: 20, code: 'DEBURR', name: 'Deburring' };
/** Active Worker badges the server knows (canonical uppercase). */
const ACTIVE_BADGES = new Set(['ABC123']);

let identification: Identification;
let flows: Flow[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let healthDown: boolean;
let badgeFailure: boolean;
/** While set, badge checks stay pending until it resolves. */
let badgeHold: Promise<void> | null;
/** Applied when the server commits a transfer (a concurrent Admin edit). */
let onTransferCommitted: (() => void) | null;

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

function operationRef() {
  return { ...TURNING, is_external: false };
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: { ...operationRef(), is_active: true },
    processing_state: 'PROCESSING',
    machine_id: null,
    completed_machine: null,
    entered_at: '2026-10-01T06:30:00Z',
    available_actions: ['DONE', 'TRANSFER', 'SCRAP'],
    work_order: null,
  };
}

function inventoryLines(items: Flow[]) {
  return items.map((flow) => ({
    part_number: flow.pn,
    total_quantity: flow.qty,
    flows: [flowWire(flow)],
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function handle(url: string, method: string, body: unknown): Response {
  if (url === '/api/health') {
    return healthDown
      ? json({ status: 'unavailable' }, 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/machines') return json([]);
  if (url === '/api/scan-stations/DEBURR-ST-01/context') {
    return json({
      station_id: 'DEBURR-ST-01',
      department: { id: 1, name: 'Finishing' },
      area: areaRef(2),
      operations: [operationRef()],
      has_machines: false,
      worker_identification: identification,
    });
  }
  const inventory = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inventory) {
    const areaId = Number(inventory[1]);
    const here = flows.filter((f) => f.areaId === areaId);
    const lines = inventoryLines(here);
    const total = here.reduce((s, f) => s + f.qty, 0);
    return json({
      area: areaRef(areaId),
      demand_context: [],
      scrapped: [],
      has_machines: false,
      lines,
      total_part_numbers: lines.length,
      total_quantity: total,
      queued: [],
      queued_quantity: 0,
      machines: [],
      on_machine_quantity: 0,
      processing: lines,
      processing_quantity: total,
      finished: [],
      finished_quantity: 0,
    });
  }
  if (url === '/api/scan-stations/DEBURR-ST-01/scans/resolve') {
    const pn = String((body as { barcode: string }).barcode).slice(
      'PF:PN:'.length,
    );
    const candidates = flows.filter((f) => f.pn === pn && f.areaId !== 2);
    return json({
      part_number: pn,
      station_id: 'DEBURR-ST-01',
      area: areaRef(2),
      resolution: candidates.length
        ? 'TRANSFER_SOURCE_AVAILABLE'
        : 'NO_TRANSFERABLE_QUANTITY',
      in_area: [],
      candidates: candidates.map((flow) => ({
        ...flowWire(flow),
        current_area: areaRef(flow.areaId),
        route_status: 'FLOATING',
        expected_next_area: null,
        expected_operation_id: null,
        suggested_operation_id: TURNING.id,
        repair_available: false,
      })),
      operations: [operationRef()],
      has_active_demand: true,
      intake_available: false,
      part_number_known: true,
      internal_work_orders: [],
      active_quantity: [],
      transfer_blocked_reason: null,
      requires_selection: false,
      combine_groups: [],
      scrapped_quantity: 0,
      stocked_quantity: 0,
      available_stocked_quantity: 0,
      stock_available: false,
      scanned_at: new Date().toISOString(),
    });
  }
  if (url === '/api/scan-stations/DEBURR-ST-01/badge-scans') {
    if (method !== 'POST') return json({ detail: 'Method Not Allowed' }, 405);
    if (badgeFailure) {
      return json({ detail: 'The badge check is unavailable.' }, 503);
    }
    const request = body as Record<string, unknown>;
    if (
      Object.keys(request).join() !== 'badge' ||
      typeof request.badge !== 'string'
    ) {
      return json({ detail: 'Invalid badge-scan body.' }, 422);
    }
    const badge = request.badge.trim().toUpperCase();
    return json({
      outcome: ACTIVE_BADGES.has(badge) ? 'NOT_USED_IN_AREA' : 'UNKNOWN',
      mode: identification.mode,
    });
  }
  if (
    url === '/api/scan-stations/DEBURR-ST-01/transfers' &&
    method === 'POST'
  ) {
    const request = body as {
      quantity_flow_id: number;
      source_area_id: number;
      quantity: number;
      device_event_id: string;
    };
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    flow.areaId = 2;
    const result = {
      movement_id: nextMovementId++,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      from_area_id: request.source_area_id,
      to_area_id: 2,
      operation_id: TURNING.id,
      station_id: 'DEBURR-ST-01',
      assigned_route_step_id: null,
      movement_reason: null,
      reason: null,
      route_deviation: null,
      completed_movement_id: null,
      completed_machine_id: null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-10-05T12:00:00Z',
    };
    committed.set(request.device_event_id, result);
    onTransferCommitted?.();
    return json(result, 201);
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  identification = DISABLED;
  flows = [
    { id: 100, pn: 'PN-W', qty: 6, areaId: 3 },
    { id: 101, pn: 'PN-V', qty: 2, areaId: 3 },
    { id: 200, pn: 'PN-H', qty: 4, areaId: 2 },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  healthDown = false;
  badgeFailure = false;
  badgeHold = null;
  onTransferCommitted = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      const hold =
        badgeHold && url.endsWith('/badge-scans')
          ? badgeHold
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

async function renderStation(suffix = '') {
  window.history.replaceState({}, '', `/scan-station/DEBURR-ST-01${suffix}`);
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

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function contextReads() {
  return requests.filter((r) => r.url.endsWith('/context')).length;
}

function badgeRequests() {
  return requests.filter((r) => r.url.includes('/badge-scans'));
}

function pill() {
  return document.querySelector('.ss-pill');
}

function summaryTerms(box: HTMLElement) {
  return within(box)
    .getAllByRole('term')
    .map((term) => term.textContent);
}

function summaryValue(box: HTMLElement, term: string): string {
  const dt = within(box).getByText(term, { selector: 'dt' });
  return dt.nextElementSibling?.textContent ?? '';
}

/** Scan PN-W (at Cut) and continue to the transfer summary. */
async function openTransferSummary(pn = 'PN-W') {
  scan(`PF:PN:${pn}`);
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByText('Review the transfer, then confirm.');
  return box;
}

/* ============ Worker pill ============ */

test('a Disabled Area renders no Worker pill', async () => {
  await renderStation();

  expect(pill()).toBeNull();
  expect(screen.queryByText('Fixed Worker')).toBeNull();
});

test('a Fixed Worker Area renders the pill with the Worker, its initials and the Fixed Worker line', async () => {
  identification = FIXED_NGUYEN;
  await renderStation();

  const header = screen.getByRole('banner');
  const mark = within(header).getByText('H. Nguyen').closest('.ss-pill')!;
  expect(mark.querySelector('.val')?.textContent).toBe('H. Nguyen');
  expect(mark.querySelector('.sub')?.textContent).toBe('Fixed Worker');
  const avatar = mark.querySelector('.worker-avatar.pill')!;
  expect(avatar.textContent).toBe('HN');
  // Standard mode: the third header cell after the Area totals.
  expect(
    Array.from(header.children, (child) => child.className.split(' ')[0]),
  ).toEqual(['ss-id', 'ss-stats', 'ss-pill']);
});

test('the Fixed Worker avatar image renders when the Worker has one; production mode keeps the pill in the head group', async () => {
  identification = {
    mode: 'FIXED',
    fixed_worker: {
      id: 7,
      name: 'H. Nguyen',
      avatar_updated_at: '2026-10-01T08:00:00+00:00',
    },
  };
  await renderStation('/production');

  const group = document.querySelector('.ss-headgroup')!;
  const mark = group.querySelector('.ss-pill')!;
  expect(group.firstElementChild).toBe(mark);
  const image = mark.querySelector('img.worker-avatar.pill')!;
  expect(image.getAttribute('src')).toBe(
    `/api/workers/7/avatar?v=${encodeURIComponent('2026-10-01T08:00:00+00:00')}`,
  );
  expect(group.querySelector('.ss-headactions')).not.toBeNull();
});

/* ============ Scan input and badge scans ============ */

test('the idle scan input names Part Number, Worker and Machine barcodes', async () => {
  const input = await renderStation();

  expect(input).toHaveAttribute(
    'placeholder',
    'Scan Part Number, Worker, or Machine barcode · Press Enter',
  );
});

test('a badge scan in a Disabled Area is checked by the server and answered with the not-used notice', async () => {
  const input = await renderStation();
  let release!: () => void;
  badgeHold = new Promise<void>((resolve) => {
    release = resolve;
  });

  scan(' abc123 ');
  await waitFor(() =>
    expect(input).toHaveAttribute('placeholder', 'Checking barcode…'),
  );
  expect(input).toBeDisabled();
  await act(async () => {
    release();
    await badgeHold;
  });

  const toast = await notice();
  expect(toast).toHaveTextContent(
    'Worker badge scans are not used in this Area',
  );
  expect(toast).toHaveTextContent(
    'This Area does not record Worker identity. No changes were recorded.',
  );
  expect(toast.className).toContain('warn');
  // One read, the badge in the body only — never in the URL.
  const [request] = badgeRequests();
  expect(badgeRequests()).toHaveLength(1);
  expect(request.method).toBe('POST');
  expect(request.url).toBe('/api/scan-stations/DEBURR-ST-01/badge-scans');
  expect(request.body).toEqual({ badge: 'abc123' });
  await waitFor(() =>
    expect(input).toHaveAttribute(
      'placeholder',
      'Scan Part Number, Worker, or Machine barcode · Press Enter',
    ),
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a badge scan in a Fixed Worker Area names the automatic recording', async () => {
  identification = FIXED_NGUYEN;
  await renderStation();

  scan('ABC123');
  const toast = await notice();
  expect(toast).toHaveTextContent(
    'Worker badge scans are not used in this Area',
  );
  expect(toast).toHaveTextContent(
    'This Area records its configured Worker automatically. No changes were recorded.',
  );
});

test('an unknown badge is not recognized, with the approved guidance', async () => {
  await renderStation();

  scan('VENDOR-LOT-77');
  const toast = await notice();
  expect(toast).toHaveTextContent('Barcode not recognized');
  expect(toast).toHaveTextContent(
    'Scan a PartFlow Part Number or Machine barcode, or a registered Worker badge. To type a Part Number, select “Enter PN manually.” No changes were recorded.',
  );
  expect(toast.className).toContain('err');
  expect(badgeRequests()).toHaveLength(1);
});

test('a badge check the server cannot answer reports it with nothing recorded', async () => {
  const input = await renderStation();
  badgeFailure = true;

  scan('ABC123');
  const toast = await notice();
  expect(toast).toHaveTextContent('Barcode could not be checked');
  expect(toast).toHaveTextContent(
    'The badge check is unavailable. No changes were recorded.',
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a badge scan changes nothing: the Last Scanned PN, the Undo target and the station stay as they were', async () => {
  const input = await renderStation();
  const box = await openTransferSummary();
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const lastPn = document.querySelector('.ss-lastpn') as HTMLElement;
  await waitFor(() =>
    expect(lastPn.querySelector('.p')).toHaveTextContent('PN-W'),
  );
  const lastBefore = lastPn.textContent;
  const undo = document.querySelector('button.ss-undo') as HTMLButtonElement;
  expect(undo).toBeEnabled();
  const writesBefore = requests.filter((r) => r.method === 'POST').length;

  scan('ABC123');
  expect(await notice()).toHaveTextContent(
    'Worker badge scans are not used in this Area',
  );
  expect(lastPn.textContent).toBe(lastBefore);
  expect(undo).toBeEnabled();
  expect(screen.queryByRole('dialog')).toBeNull();
  // The only request is the badge check itself — no command.
  expect(requests.filter((r) => r.method === 'POST')).toHaveLength(
    writesBefore + 1,
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('an unknown PartFlow value is rejected locally; offline nothing is checked', async () => {
  await renderStation();

  scan('PF:FOO');
  const toast = await notice();
  expect(toast).toHaveTextContent('Barcode not recognized');
  expect(toast).toHaveTextContent(
    'Scan a PartFlow Part Number or Machine barcode, or a registered Worker badge.',
  );
  expect(badgeRequests()).toHaveLength(0);
  cleanup();

  healthDown = true;
  window.history.replaceState({}, '', '/scan-station/DEBURR-ST-01');
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await waitFor(() => expect(input).toBeDisabled());
  scan('ABC123');
  expect(await notice()).toHaveTextContent(
    'Connection lost — scanning is paused',
  );
  expect(badgeRequests()).toHaveLength(0);
});

/* ============ Context freshness ============ */

test('the context re-read after a command shows a mode changed meanwhile', async () => {
  await renderStation();
  expect(pill()).toBeNull();
  // Administration makes the Area Fixed Worker while the command runs.
  onTransferCommitted = () => {
    identification = FIXED_NGUYEN;
  };

  const box = await openTransferSummary();
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  await waitFor(() => expect(pill()).toHaveTextContent('H. Nguyen'));
});

test('every resolved scan re-reads the context: the pill and the summary Worker row follow a mode changed between scans', async () => {
  await renderStation();
  const initialReads = contextReads();

  scan('PF:PN:PN-W');
  const first = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  await waitFor(() => expect(contextReads()).toBe(initialReads + 1));
  fireEvent.keyDown(first, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(pill()).toBeNull();

  // Changed in Administration — no command at this station.
  identification = FIXED_NGUYEN;
  const box = await openTransferSummary();
  await waitFor(() => expect(contextReads()).toBe(initialReads + 2));
  await waitFor(() => expect(pill()).toHaveTextContent('H. Nguyen'));
  await waitFor(() => expect(summaryValue(box, 'Worker')).toBe('H. Nguyen'));
  const terms = summaryTerms(box);
  expect(terms.indexOf('Worker')).toBe(terms.indexOf('Scan Station') - 1);
});

test('a badge answer naming a different mode re-reads the context once', async () => {
  await renderStation();
  const initialReads = contextReads();
  identification = FIXED_NGUYEN;

  scan('ABC123');
  expect(await notice()).toHaveTextContent(
    'This Area records its configured Worker automatically.',
  );
  await waitFor(() => expect(pill()).toHaveTextContent('H. Nguyen'));
  expect(contextReads()).toBe(initialReads + 1);

  // The same mode as rendered: no further re-read.
  scan('ABC123');
  await notice();
  await waitFor(() => expect(badgeRequests()).toHaveLength(2));
  expect(contextReads()).toBe(initialReads + 1);
});

test('the transfer summary has no Worker row in a Disabled Area', async () => {
  await renderStation();

  const box = await openTransferSummary();
  expect(summaryTerms(box)).not.toContain('Worker');
  expect(summaryValue(box, 'Scan Station')).toBe('DEBURR-ST-01');
});
