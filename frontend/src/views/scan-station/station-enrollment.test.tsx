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
import { STATION_PERMISSIONS } from '../../api/scan-station';
import type { StationPermission } from '../../api/scan-station';
import { readStationDeviceToken } from '../../api/station-devices';

// Real Scan Station with enrolled station devices (Phase 14 slice 4 —
// GUI_DESIGN §4.13) against a fake in-memory `/api` with the backend's
// wire contract: every station route needs the
// `X-PartFlow-Station-Device` token of a device enrolled for THAT
// station (none / revoked → 401 `station_device_required`, another
// station's → 403 `station_device_mismatch`), the Area inventory also
// needs the station to be bound to that Area (else 409
// `station_context_changed`), `POST …/device-activations` exchanges a
// one-time code for a token, the context reports the device and the
// station permissions, and a command the role applied at Scan Stations
// does not grant is refused with 403 `station_permission_denied`.
// Covers: the enrollment panel (and the unknown / inactive station
// errors it never replaces), activation outcomes, the blocking
// enrollment dialog over an open wizard with the draft and the
// `device_event_id` kept, the unknown outcome kept through a device
// refusal, station-permission refusals, actions hidden by the station
// permissions, the rebinding refusal, and late refusals of an older
// token.

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
}

const STATION = 'DEBURR-ST-01';
const OTHER_STATION = 'CUT-ST-02';
const AREAS = [
  { id: 2, name: 'Deburr', color: '#33aa66' },
  { id: 3, name: 'Cut', color: '#3366ff' },
];
const DEBURRING = { id: 20, code: 'DEBURR', name: 'Deburring' };
const K1_CONFIRM =
  'Scan Stations are not allowed to confirm quantity. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.';
const D1 =
  'This device is not enrolled for this Scan Station, or its enrollment was revoked or replaced. Ask an administrator for an enrollment code.';
const D2 =
  'This device is enrolled for a different Scan Station. Enroll it for this station to continue.';
const C1 =
  "This Scan Station's Area changed. The station reloads with its current Area.";

let flows: Flow[];
let stations: { station_id: string; area_id: number; is_active: boolean }[];
/** Valid device tokens → the station each is enrolled for. */
let tokens: Map<string, string>;
/** Unused one-time codes (canonical) → their station. */
let codes: Map<string, string>;
let stationPermissions: StationPermission[];
let committed: Map<string, unknown>;
let requests: {
  url: string;
  method: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  body: any;
  token: string | null;
}[];
let nextId: number;
/** The next transfer: refused with this answer, or its response lost. */
let transferFailure: null | 'lost-response' | { status: number; body: unknown };
/** The next PN resolve is refused with this answer. */
let resolveFailure: null | { status: number; body: unknown };
/** The next activation answers no response at all / a 503. */
let activationFailure: null | 'network' | 'server';
/** While set, PN resolves stay pending until it resolves. */
let resolveHold: Promise<void> | null;
/** PNs the server reports with the `Receive Quantity` entry condition. */
let intakePns: Set<string>;

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
  return { ...DEBURRING, is_external: false };
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

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

const DUE_SOON_POLICY_WIRE = {
  due_soon_min_days: 2,
  due_soon_lead_time_percent: 15,
  due_soon_max_days: 7,
  updated_at: '2026-10-01T08:00:00Z',
};

function areaOf(stationId: string): number {
  return stations.find((s) => s.station_id === stationId)!.area_id;
}

/** The server's device check of a station route (null = accepted). */
function deviceCheck(
  token: string | null,
  stationId: string | null,
): Response | null {
  const enrolledFor = token === null ? undefined : tokens.get(token);
  if (enrolledFor === undefined) {
    return json({ detail: D1, station_device_required: true }, 401);
  }
  if (stationId !== null && enrolledFor !== stationId) {
    return json({ detail: D2, station_device_mismatch: true }, 403);
  }
  return null;
}

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const lines = here.map((flow) => ({
    part_number: flow.pn,
    total_quantity: flow.qty,
    flows: [flowWire(flow)],
  }));
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

function resolution(stationId: string, pn: string) {
  const areaId = areaOf(stationId);
  const here = flows.filter((f) => f.pn === pn && f.areaId === areaId);
  const candidates = flows.filter((f) => f.pn === pn && f.areaId !== areaId);
  return {
    part_number: pn,
    station_id: stationId,
    area: areaRef(areaId),
    resolution: here.length
      ? 'ALREADY_IN_AREA'
      : candidates.length
        ? 'TRANSFER_SOURCE_AVAILABLE'
        : 'NO_TRANSFERABLE_QUANTITY',
    in_area: here.map(flowWire),
    candidates: candidates.map((flow) => ({
      ...flowWire(flow),
      current_area: areaRef(flow.areaId),
      route_status: 'FLOATING',
      expected_next_area: null,
      expected_operation_id: null,
      suggested_operation_id: DEBURRING.id,
      repair_available: false,
    })),
    operations: [operationRef()],
    has_active_demand: true,
    intake_available: intakePns.has(pn),
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
    worker_session: null,
  };
}

function handle(
  url: string,
  method: string,
  body: unknown,
  token: string | null,
): Response {
  if (url === '/api/policies/due-soon') return json(DUE_SOON_POLICY_WIRE);
  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/machines') return json([]);
  if (url === '/api/scan-stations') {
    return json(
      stations.map((s) => ({ ...s, created_at: 't', updated_at: 't' })),
    );
  }
  if (url === '/api/areas') {
    return json(
      AREAS.map((a) => ({
        ...areaRef(a.id),
        department_id: 1,
        barcode_value: `PF:AREA:${a.id}`,
        icon_url: null,
        is_active: true,
        worker_identification_mode: 'DISABLED',
        fixed_worker_id: null,
        worker_session_timeout_minutes: null,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/departments') {
    return json([
      {
        id: 1,
        name: 'Finishing',
        is_active: true,
        created_at: 't',
        updated_at: 't',
      },
    ]);
  }
  if (url === '/api/operations') {
    return json([
      {
        ...DEBURRING,
        area_id: 2,
        description: null,
        default_expected_duration: null,
        is_external: false,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      },
    ]);
  }
  const activation = /^\/api\/scan-stations\/([^/]+)\/device-activations$/.exec(
    url,
  );
  if (activation && method === 'POST') {
    const stationId = decodeURIComponent(activation[1]);
    if (activationFailure === 'network') {
      activationFailure = null;
      throw new TypeError('Failed to fetch');
    }
    if (activationFailure === 'server') {
      activationFailure = null;
      return json({ detail: 'Service unavailable' }, 503);
    }
    const code = String((body as { enrollment_code: string }).enrollment_code)
      .replace(/[\s-]/g, '')
      .toUpperCase();
    if (codes.get(code) !== stationId) {
      return json(
        {
          detail: `This enrollment code is not valid for Scan Station ${stationId}. It may have expired (codes last 15 minutes), been used already, or been issued for another station. Ask an administrator for a new code.`,
          enrollment_code_invalid: true,
        },
        403,
      );
    }
    codes.delete(code);
    const deviceToken = `token-${nextId++}`;
    tokens.set(deviceToken, stationId);
    return json(
      {
        device_token: deviceToken,
        device: {
          id: nextId++,
          station_id: stationId,
          label: 'Station PC',
          activated_at: '2026-10-07T08:00:00Z',
        },
      },
      201,
    );
  }
  const inventoryMatch = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inventoryMatch) {
    const refused = deviceCheck(token, null);
    if (refused) return refused;
    const areaId = Number(inventoryMatch[1]);
    if (areaOf(tokens.get(token!)!) !== areaId) {
      return json({ detail: C1, station_context_changed: true }, 409);
    }
    return inventory(areaId);
  }
  const station = /^\/api\/scan-stations\/([^/]+)\/(.+)$/.exec(url);
  if (!station) return json({ detail: `Unhandled ${method} ${url}` }, 500);
  const stationId = decodeURIComponent(station[1]);
  const refused = deviceCheck(token, stationId);
  if (refused) return refused;
  const path = station[2];
  if (path === 'context') {
    return json({
      station_id: stationId,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(areaOf(stationId)),
      operations: [operationRef()],
      has_machines: false,
      worker_identification: {
        mode: 'DISABLED',
        fixed_worker: null,
        session: null,
        final_gates: { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' },
      },
      theme_preference: null,
      device: { id: 1, label: 'Station PC' },
      station_permissions: stationPermissions,
    });
  }
  if (path === 'scans/resolve') {
    if (resolveFailure) {
      const failure = resolveFailure;
      resolveFailure = null;
      return json(failure.body, failure.status);
    }
    const pn = String((body as { barcode: string }).barcode).slice(
      'PF:PN:'.length,
    );
    return json(resolution(stationId, pn));
  }
  if (path === 'transfers' && method === 'POST') {
    const request = body as {
      quantity_flow_id: number;
      source_area_id: number;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    if (transferFailure && transferFailure !== 'lost-response') {
      const failure = transferFailure;
      transferFailure = null;
      return json(failure.body, failure.status);
    }
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    flow.areaId = areaOf(stationId);
    const result = {
      movement_id: nextId++,
      movement_type: 'TRANSFERRED',
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      from_area_id: request.source_area_id,
      to_area_id: flow.areaId,
      operation_id: DEBURRING.id,
      station_id: stationId,
      assigned_route_step_id: null,
      movement_reason: null,
      reason: null,
      route_deviation: null,
      completed_movement_id: null,
      completed_machine_id: null,
      source_quantity_flow_id: null,
      remainder_quantity_flow_id: null,
      remainder_quantity: null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-10-07T08:30:00Z',
    };
    committed.set(request.device_event_id, result);
    if (transferFailure === 'lost-response') {
      transferFailure = null;
      throw new TypeError('Failed to fetch');
    }
    return json(result, 201);
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  window.localStorage.clear();
  flows = [
    { id: 100, pn: 'PN-W', qty: 6, areaId: 3 },
    { id: 101, pn: 'PN-V', qty: 2, areaId: 3 },
    { id: 102, pn: 'PN-V', qty: 3, areaId: 3 },
    { id: 200, pn: 'PN-H', qty: 4, areaId: 2 },
  ];
  stations = [
    { station_id: STATION, area_id: 2, is_active: true },
    { station_id: OTHER_STATION, area_id: 3, is_active: true },
    { station_id: 'OLD-ST-09', area_id: 2, is_active: false },
  ];
  tokens = new Map();
  codes = new Map([
    ['K7M2QX9RTA', STATION],
    ['AAAAABBBBB', STATION],
  ]);
  stationPermissions = [...STATION_PERMISSIONS];
  committed = new Map();
  requests = [];
  nextId = 500;
  transferFailure = null;
  resolveFailure = null;
  activationFailure = null;
  resolveHold = null;
  intakePns = new Set();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const token = headers['X-PartFlow-Station-Device'] ?? null;
      if (!url.endsWith('/api/health') && url !== '/api/policies/due-soon') {
        requests.push({ url, method, body, token });
      }
      const hold =
        resolveHold && url.endsWith('/scans/resolve')
          ? resolveHold
          : Promise.resolve();
      return hold.then(() => handle(url, method, body, token));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

/** This browser already holds a valid token for `stationId`. */
function enrollBrowser(stationId = STATION, token = 'token-initial') {
  tokens.set(token, stationId);
  window.localStorage.setItem(`partflow.station-device.${stationId}`, token);
  return token;
}

function open(stationId = STATION, suffix = '') {
  window.history.replaceState({}, '', `/scan-station/${stationId}${suffix}`);
  render(<App />);
}

async function renderEnrolledStation() {
  enrollBrowser();
  open();
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

async function openTransferSummary(pn = 'PN-W') {
  scan(`PF:PN:${pn}`);
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByText('Review the transfer, then confirm.');
  return box;
}

function transfers() {
  return requests.filter((r) => r.url.endsWith('/transfers'));
}

function enter(container: HTMLElement, code: string) {
  fireEvent.change(within(container).getByLabelText('Enrollment code'), {
    target: { value: code },
  });
  fireEvent.click(
    within(container).getByRole('button', { name: 'Enroll device' }),
  );
}

/* ============ Enrollment panel ============ */

test('FS-4: a browser without a device sees the enrollment panel under the station header', async () => {
  open(STATION, '/production');
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  expect(panel.textContent).toContain(
    `This device is not enrolled for Scan Station ${STATION}. Production at this station needs an enrolled device. Ask an administrator for an enrollment code (Administration → Scan Stations → Devices).`,
  );
  const header = document.querySelector('.ss-head') as HTMLElement;
  expect(header.textContent).toContain('Finishing');
  expect(header.textContent).toContain(STATION);
  expect(header.textContent).toContain('Deburr');
  // The code field owns focus; production mode keeps a way out.
  expect(document.activeElement).toBe(
    within(panel).getByLabelText('Enrollment code'),
  );
  const field = within(panel).getByLabelText('Enrollment code');
  expect(field).toHaveAttribute('placeholder', 'XXXXX-XXXXX');
  expect(field).toHaveAttribute('autocomplete', 'off');
  expect(field).toHaveAttribute('spellcheck', 'false');
  expect(field).toHaveAttribute('autocapitalize', 'characters');
  expect(
    within(panel).getByRole('button', { name: 'Station Selector' }),
  ).toBeInTheDocument();
  // No station content and no scan input without a device.
  expect(screen.queryByLabelText('Scan barcode')).toBeNull();
  // The context read sent no device header.
  expect(requests.find((r) => r.url.endsWith('/context'))?.token).toBeNull();

  // A short code is refused in place, nothing sent.
  fireEvent.change(field, { target: { value: 'K7M2Q-X9' } });
  fireEvent.click(within(panel).getByRole('button', { name: 'Enroll device' }));
  expect(
    within(panel).getByRole('alert').querySelector('.gtext')?.textContent,
  ).toBe('Enter the 10-character enrollment code.');
  expect(requests.some((r) => r.url.endsWith('/device-activations'))).toBe(
    false,
  );
});

test('FS-4: an unknown or inactive Station ID is the unavailable-station error, never the panel', async () => {
  open('NOPE-ST');
  expect(
    await screen.findByText('Scan Station “NOPE-ST” is unavailable'),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('region', { name: 'Enroll this device' }),
  ).toBeNull();
  cleanup();

  open('OLD-ST-09');
  expect(
    await screen.findByText(
      "Scan Station 'OLD-ST-09' is inactive and accepts no production use.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('region', { name: 'Enroll this device' }),
  ).toBeNull();
});

test('FS-4: a device enrolled for another station sees the mismatch panel; offline blocks enrolling', async () => {
  // The browser holds the Cut station's token under this station's key.
  window.localStorage.setItem(
    `partflow.station-device.${STATION}`,
    'token-for-cut',
  );
  tokens.set('token-for-cut', OTHER_STATION);
  open();
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  expect(panel.textContent).toContain(
    `This device is enrolled for a different Scan Station, not ${STATION}. Enroll it for this station to continue.`,
  );
  // A mismatch never forgets the token.
  expect(readStationDeviceToken(STATION)).toBe('token-for-cut');
  cleanup();

  vi.mocked(fetch).mockImplementation(((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/api/health')) {
      return Promise.resolve(json({ status: 'unavailable' }, 503));
    }
    return Promise.resolve(handle(url, 'GET', undefined, null));
  }) as typeof fetch);
  window.localStorage.clear();
  open();
  const offline = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  await waitFor(() =>
    expect(
      within(offline).getByRole('button', { name: 'Enroll device' }),
    ).toBeDisabled(),
  );
  expect(offline.textContent).toContain(
    'Enrolling needs the connection to the PartFlow server.',
  );
});

test('FS-5: activation stores the token, loads the station, confirms and focuses the barcode input', async () => {
  open();
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  enter(panel, 'k7m2q x9rta');
  const input = await screen.findByLabelText('Scan barcode');
  expect(readStationDeviceToken(STATION)).toMatch(/^token-/);
  expect((await notice()).textContent).toContain(
    `This device is enrolled for Scan Station ${STATION}.`,
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
  // The station reads carry the new token.
  const context = requests.filter((r) => r.url.endsWith('/context')).at(-1);
  expect(context?.token).toBe(readStationDeviceToken(STATION));
  const activation = requests.find((r) =>
    r.url.endsWith('/device-activations'),
  );
  expect(activation?.body).toEqual({ enrollment_code: 'k7m2q x9rta' });
  expect(activation?.token).toBeNull();
});

test('FS-5: a refused code shows the server text and keeps the field; network and server failures say so', async () => {
  open();
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  enter(panel, 'ZZZZZ-ZZZZZ');
  expect(
    (await within(panel).findByRole('alert')).querySelector('.gtext')
      ?.textContent,
  ).toBe(
    `This enrollment code is not valid for Scan Station ${STATION}. It may have expired (codes last 15 minutes), been used already, or been issued for another station. Ask an administrator for a new code.`,
  );
  expect(within(panel).getByLabelText('Enrollment code')).toHaveValue(
    'ZZZZZ-ZZZZZ',
  );

  activationFailure = 'network';
  enter(panel, 'AAAAA-BBBBB');
  await waitFor(() =>
    expect(
      within(panel).getByRole('alert').querySelector('.gtext')?.textContent,
    ).toBe('The PartFlow server could not be reached. Try again.'),
  );
  activationFailure = 'server';
  enter(panel, 'AAAAA-BBBBB');
  await waitFor(() =>
    expect(
      within(panel).getByRole('alert').querySelector('.gtext')?.textContent,
    ).toBe(
      'The server did not answer — this device may or may not be enrolled. If the next attempt says the code is not valid, ask an administrator for a new code.',
    ),
  );
  expect(readStationDeviceToken(STATION)).toBeNull();
  // The code still works: the station opens.
  enter(panel, 'AAAAA-BBBBB');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
});

/* ============ Enrollment dialog over an open wizard ============ */

test('FS-6: a revoked device raises the enrollment dialog over the wizard; the same request is confirmed again', async () => {
  const oldToken = await renderEnrolledStation();
  void oldToken;
  const box = await openTransferSummary();
  // An administrator revokes this device meanwhile.
  tokens.delete('token-initial');
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );

  const enrollDialog = await screen.findByRole('dialog', {
    name: 'Enroll this device',
  });
  expect(enrollDialog.textContent).toContain(
    `This device's enrollment for Scan Station ${STATION} was revoked or replaced. Ask an administrator for a new enrollment code.`,
  );
  expect(enrollDialog.textContent).toContain(
    'Not sent — this device must be enrolled first. Nothing was recorded.',
  );
  // The wizard and its draft stay underneath; no error, no rejection.
  expect(
    screen.getByRole('dialog', { name: 'Receive from another Area' }),
  ).toBe(box);
  expect(box.textContent).not.toContain(D1);
  // The revoked token is forgotten.
  expect(readStationDeviceToken(STATION)).toBeNull();
  // Escape never dismisses the enrollment dialog, and it offers no way
  // out of the station (the panel's Station Selector is not rendered).
  fireEvent.keyDown(enrollDialog, { key: 'Escape' });
  expect(
    screen.getByRole('dialog', { name: 'Enroll this device' }),
  ).toBeInTheDocument();
  expect(
    within(enrollDialog).queryByRole('button', { name: 'Station Selector' }),
  ).toBeNull();

  enter(enrollDialog, 'K7M2Q-X9RTA');
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Enroll this device' }),
    ).toBeNull(),
  );
  const newToken = readStationDeviceToken(STATION);
  expect(newToken).toMatch(/^token-/);
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Receive from another Area' }),
    ).toBeNull(),
  );
  const [first, second] = transfers();
  expect(second.body).toEqual(first.body);
  expect(first.token).toBe('token-initial');
  expect(second.token).toBe(newToken);
  expect(committed.size).toBe(1);
});

test('FS-7: a device refusal of a retry after an unknown outcome keeps the unknown outcome', async () => {
  await renderEnrolledStation();
  const box = await openTransferSummary();
  transferFailure = 'lost-response';
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  const retry = await within(box).findByRole('button', {
    name: 'Retry the same transfer',
  });
  tokens.delete('token-initial');
  fireEvent.click(retry);

  const enrollDialog = await screen.findByRole('dialog', {
    name: 'Enroll this device',
  });
  expect(enrollDialog.textContent).toContain(
    'The outcome of the last action is unknown. After enrolling, confirm it again — it is recorded only once.',
  );
  // Nothing anywhere claims the action was not recorded.
  expect(document.body.textContent).not.toMatch(/nothing was recorded/i);
  expect(
    within(box).getByRole('button', { name: 'Retry the same transfer' }),
  ).toBeInTheDocument();

  enter(enrollDialog, 'K7M2Q-X9RTA');
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Enroll this device' }),
    ).toBeNull(),
  );
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same transfer' }),
  );
  // The committed original replays: recorded once.
  expect((await notice()).textContent).toContain('nothing was recorded twice');
  const sent = transfers();
  expect(new Set(sent.map((r) => r.body.device_event_id)).size).toBe(1);
  expect(committed.size).toBe(1);
});

/* ============ Station permissions ============ */

test('FS-8: a station-permission refusal of a command is an ordinary rejection and ends an unknown outcome', async () => {
  await renderEnrolledStation();
  const box = await openTransferSummary();
  transferFailure = {
    status: 403,
    body: {
      detail: K1_CONFIRM,
      station_permission_denied: true,
      required_permissions: ['CONFIRM_QUANTITY'],
    },
  };
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  expect(await within(box).findByText(K1_CONFIRM)).toBeInTheDocument();
  expect(
    within(box).getByRole('button', { name: 'Retry transfer' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('dialog', { name: 'Enroll this device' }),
  ).toBeNull();
  // The token stays.
  expect(readStationDeviceToken(STATION)).toBe('token-initial');
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(committed.size).toBe(0);
});

test('FS-8: a station-permission refusal after an unknown outcome ends it', async () => {
  await renderEnrolledStation();
  const box = await openTransferSummary();
  transferFailure = 'lost-response';
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await within(box).findByRole('button', { name: 'Retry the same transfer' });
  committed.clear();
  flows.find((f) => f.id === 100)!.areaId = 3;
  transferFailure = {
    status: 403,
    body: {
      detail: K1_CONFIRM,
      station_permission_denied: true,
      required_permissions: ['CONFIRM_QUANTITY'],
    },
  };
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same transfer' }),
  );
  expect(await within(box).findByText(K1_CONFIRM)).toBeInTheDocument();
  expect(
    within(box).queryByRole('button', { name: 'Retry the same transfer' }),
  ).toBeNull();
  expect(
    within(box).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
});

test('FS-8: a station-permission refusal of a PN scan is a red notification; the input is cleared and refocused', async () => {
  const input = await renderEnrolledStation();
  const K1_SCAN =
    'Scan Stations are not allowed to scan Part Number barcodes. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.';
  resolveFailure = {
    status: 403,
    body: {
      detail: K1_SCAN,
      station_permission_denied: true,
      required_permissions: ['SCAN_PN_BARCODES'],
    },
  };
  scan('PF:PN:PN-W');
  const toast = await notice();
  expect(toast.classList.contains('err')).toBe(true);
  expect(toast.textContent).toContain(K1_SCAN);
  expect(input).toHaveValue('');
  await waitFor(() => expect(document.activeElement).toBe(input));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('FS-13: a scan that would open only an action the station may not do opens nothing and sends nothing', async () => {
  stationPermissions = STATION_PERMISSIONS.filter(
    (key) => key !== 'CONFIRM_QUANTITY',
  );
  const input = await renderEnrolledStation();
  // PN-W has exactly one source elsewhere: the transfer would open.
  scan('PF:PN:PN-W');
  const toast = await notice();
  expect(toast.classList.contains('err')).toBe(true);
  expect(toast.textContent).toContain(K1_CONFIRM);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(input).toHaveValue('');
  await waitFor(() => expect(document.activeElement).toBe(input));
  // Several sources: the source selection does not open either.
  scan('PF:PN:PN-V');
  await waitFor(() =>
    expect(
      requests.filter((r) => r.url.endsWith('/scans/resolve')),
    ).toHaveLength(2),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(transfers()).toEqual([]);
  // The direct-processing row action DONE is not rendered.
  expect(
    screen.queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
});

test('FS-13: the in-Area dialog renders only the choices the station may do', async () => {
  // PN-H is in this Area and elsewhere: DONE, receive more, add, scrap.
  flows.push({ id: 300, pn: 'PN-H', qty: 1, areaId: 3 });
  await renderEnrolledStation();
  scan('PF:PN:PN-H');
  const box = await screen.findByRole('dialog', { name: 'Select an action' });
  const allChoices = Array.from(
    box.querySelectorAll('.choice .ct1'),
    (el) => el.textContent,
  );
  expect(allChoices).toEqual([
    'Complete Area processing',
    'Receive more quantity from another Area',
    'Add more quantity',
    'Scrap damaged quantity',
  ]);
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  // With the permission the direct-processing row DONE is offered.
  expect(
    screen.getByRole('button', { name: 'Complete Area processing' }),
  ).toBeInTheDocument();
  cleanup();

  // Without
  // CONFIRM_QUANTITY every choice of this PN is hidden → refused.
  stationPermissions = STATION_PERMISSIONS.filter(
    (key) => key !== 'CONFIRM_QUANTITY',
  );
  await renderEnrolledStation();
  scan('PF:PN:PN-H');
  expect((await notice()).textContent).toContain(K1_CONFIRM);
  expect(screen.queryByRole('dialog', { name: 'Select an action' })).toBeNull();
});

test('FS-13: an intent the station may not do is not offered; the others keep their order', async () => {
  intakePns.add('PN-W');
  await renderEnrolledStation();
  scan('PF:PN:PN-W');
  let box = await screen.findByRole('dialog', { name: 'Select an action' });
  expect(
    Array.from(box.querySelectorAll('.choice .ct1'), (el) => el.textContent),
  ).toEqual(['Receive from another Area', 'Receive new quantity']);
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  cleanup();

  stationPermissions = STATION_PERMISSIONS.filter(
    (key) => key !== 'RECEIVE_QUANTITY',
  );
  await renderEnrolledStation();
  scan('PF:PN:PN-W');
  box = await screen.findByRole('dialog', { name: 'Select an action' });
  expect(
    Array.from(box.querySelectorAll('.choice .ct1'), (el) => el.textContent),
  ).toEqual(['Receive from another Area']);
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));

  // Nothing to transfer and only the receipt left: refused, not opened.
  intakePns.add('PN-ONLY-NEW');
  scan('PF:PN:PN-ONLY-NEW');
  expect((await notice()).textContent).toContain(
    'Scan Stations are not allowed to receive quantity. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.',
  );
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('FS-13: a refusal despite the context (stale station permissions) is an ordinary rejection', async () => {
  await renderEnrolledStation();
  const box = await openTransferSummary();
  stationPermissions = [];
  transferFailure = {
    status: 403,
    body: {
      detail: K1_CONFIRM,
      station_permission_denied: true,
      required_permissions: ['CONFIRM_QUANTITY'],
    },
  };
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  expect(await within(box).findByText(K1_CONFIRM)).toBeInTheDocument();
  expect(committed.size).toBe(0);
});

/* ============ Rebinding and late refusals ============ */

test('FS-14: a station rebound while open reloads its context; the token stays and no enrollment is asked', async () => {
  await renderEnrolledStation();
  const reads = () => requests.filter((r) => r.url.endsWith('/context')).length;
  const before = reads();
  // An administrator rebinds the station to Cut; the next inventory
  // read of Deburr is refused as stale.
  stations[0].area_id = 3;
  scan('PF:PN:NOTHING');
  await waitFor(() => expect(reads()).toBeGreaterThan(before));
  await waitFor(() =>
    expect(requests.some((r) => r.url === '/api/areas/3/inventory')).toBe(true),
  );
  expect(readStationDeviceToken(STATION)).toBe('token-initial');
  expect(
    screen.queryByRole('dialog', { name: 'Enroll this device' }),
  ).toBeNull();
  await waitFor(() =>
    expect(
      (document.querySelector('.ss-head .area') as HTMLElement).textContent,
    ).toContain('Cut'),
  );
});

test('FS-14: the inventory refusal of a rebound station shows its text as information', async () => {
  await renderEnrolledStation();
  const box = await openTransferSummary();
  // The station is rebound while the wizard is open: the inventory
  // re-read after the confirmed transfer meets the stale Area.
  stations[0].area_id = 3;
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    expect(toast?.textContent ?? '').toContain(C1);
  });
  expect(readStationDeviceToken(STATION)).toBe('token-initial');
});

test('FS-15: a late refusal of an older token never removes a newer one; a refusal of the stored token does', async () => {
  await renderEnrolledStation();
  // A resolve leaves with the old token and is held.
  let release: () => void = () => undefined;
  resolveHold = new Promise((resolve) => {
    release = resolve;
  });
  scan('PF:PN:PN-W');
  await waitFor(() =>
    expect(
      requests.filter((r) => r.url.endsWith('/scans/resolve')),
    ).toHaveLength(1),
  );
  // Meanwhile this browser was re-enrolled (another tab): T2 stored,
  // and the old token revoked.
  tokens.delete('token-initial');
  tokens.set('token-2', STATION);
  window.localStorage.setItem(`partflow.station-device.${STATION}`, 'token-2');
  resolveHold = null;
  await act(async () => {
    release();
  });
  // The stale refusal leaves the newer token and raises nothing.
  await waitFor(() =>
    expect(document.querySelector('.ss-toast')).not.toBeNull(),
  );
  expect(readStationDeviceToken(STATION)).toBe('token-2');
  expect(
    screen.queryByRole('dialog', { name: 'Enroll this device' }),
  ).toBeNull();

  // A refusal of the token currently stored removes it.
  tokens.delete('token-2');
  scan('PF:PN:PN-W');
  expect(
    await screen.findByRole('dialog', { name: 'Enroll this device' }),
  ).toBeInTheDocument();
  expect(readStationDeviceToken(STATION)).toBeNull();
});
