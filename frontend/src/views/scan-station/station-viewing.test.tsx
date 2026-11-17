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
import { STATION_PERMISSIONS } from '../../api/scan-station';

// Viewing a Scan Station without an enrolled device (owner decision
// 2026-11-17; GUI_DESIGN §4.13) against a fake in-memory `/api` with the
// backend's wire contract: the station context and the Area inventory
// answer an enrolled device of the station (its `X-PartFlow-Station-Device`
// token) and, without a token, a signed-in User whose role holds Manage
// Scan Stations — the context then carries `device: null`, the `viewer`
// and no station permissions, and the inventory needs `?station_id=` of
// the viewed station. Everything else needs the device (401
// `station_device_required`). Covers: the viewing state (persistent
// notice, every write entry point disabled or hidden, no enrollment
// block, no Worker sign-in), enrolling from the notice, a refused stored
// token falling back to viewing, users without the permission and
// production mode unchanged.

const STATION = 'DEBURR-ST-01';
const AREA = { id: 2, name: 'Deburr', color: '#33aa66' };
const DEBURRING = { id: 20, code: 'DEBURR', name: 'Deburring' };
const D1 =
  'This device is not enrolled for this Scan Station, or its enrollment was revoked or replaced. Ask an administrator for an enrollment code.';
const VIEWING_NOTICE = `Viewing as Jane Doe. This device is not enrolled for Scan Station ${STATION}; actions need an enrolled device.`;

let userPermissions: string[] | null;
let workerMode: 'DISABLED' | 'SCANNED';
let tokens: Map<string, string>;
let codes: Map<string, string>;
let requests: { url: string; method: string; token: string | null }[];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function areaRef() {
  return {
    id: AREA.id,
    name: AREA.name,
    color: AREA.color,
    description: null,
    is_terminal: false,
  };
}

function operationRef() {
  return { ...DEBURRING, is_external: false };
}

function inventoryWire() {
  const line = {
    part_number: 'PN-H',
    total_quantity: 4,
    flows: [
      {
        part_number: 'PN-H',
        quantity_flow_id: 200,
        quantity: 4,
        route_mode: 'FLOATING',
        operation: { ...operationRef(), is_active: true },
        processing_state: 'PROCESSING',
        machine_id: null,
        completed_machine: null,
        entered_at: '2026-10-01T06:30:00Z',
        available_actions: ['DONE', 'TRANSFER', 'SCRAP'],
        work_order: null,
      },
    ],
  };
  return {
    area: areaRef(),
    demand_context: [],
    scrapped: [],
    has_machines: false,
    lines: [line],
    total_part_numbers: 1,
    total_quantity: 4,
    queued: [],
    queued_quantity: 0,
    machines: [],
    on_machine_quantity: 0,
    processing: [line],
    processing_quantity: 4,
    finished: [],
    finished_quantity: 0,
  };
}

function sessionWire() {
  return {
    user:
      userPermissions === null
        ? null
        : {
            id: 7,
            login_name: 'jdoe',
            display_name: 'Jane Doe',
            role_id: 2,
            role_name: 'Administrator',
            avatar_updated_at: null,
            permissions: userPermissions,
            must_change_password: false,
            session_expires_at: null,
            theme_preference: null,
          },
    setup_open: false,
  };
}

/** The server's reader check of the two viewable reads. */
function readerOf(
  token: string | null,
): { device: boolean } | { refused: Response } {
  if (token !== null) {
    return tokens.get(token) === STATION
      ? { device: true }
      : { refused: json({ detail: D1, station_device_required: true }, 401) };
  }
  if (userPermissions?.includes('MANAGE_SCAN_STATIONS')) {
    return { device: false };
  }
  return { refused: json({ detail: D1, station_device_required: true }, 401) };
}

function handle(
  url: string,
  method: string,
  body: unknown,
  token: string | null,
): Response {
  if (url === '/api/session' && method === 'GET') return json(sessionWire());
  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/policies/due-soon') {
    return json({
      due_soon_min_days: 2,
      due_soon_lead_time_percent: 15,
      due_soon_max_days: 7,
      updated_at: '2026-10-01T08:00:00Z',
    });
  }
  if (url === '/api/scan-stations') {
    return json([
      {
        station_id: STATION,
        area_id: AREA.id,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      },
    ]);
  }
  if (url === '/api/areas') {
    return json([
      {
        ...areaRef(),
        department_id: 1,
        barcode_value: 'PF:AREA:2',
        icon_url: null,
        is_active: true,
        worker_identification_mode: workerMode,
        fixed_worker_id: null,
        worker_session_timeout_minutes: null,
        created_at: 't',
        updated_at: 't',
      },
    ]);
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
  const activation = /^\/api\/scan-stations\/([^/]+)\/device-activations$/.exec(
    url,
  );
  if (activation && method === 'POST') {
    const stationId = decodeURIComponent(activation[1]);
    const code = String((body as { enrollment_code: string }).enrollment_code)
      .replace(/[\s-]/g, '')
      .toUpperCase();
    if (codes.get(code) !== stationId) {
      return json(
        { detail: 'Invalid code', enrollment_code_invalid: true },
        403,
      );
    }
    codes.delete(code);
    tokens.set('token-new', stationId);
    return json(
      {
        device_token: 'token-new',
        device: {
          id: 9,
          station_id: stationId,
          label: 'Station PC',
          activated_at: '2026-10-07T08:00:00Z',
        },
      },
      201,
    );
  }
  const inventory = /^\/api\/areas\/(\d+)\/inventory(\?station_id=(.+))?$/.exec(
    url,
  );
  if (inventory) {
    const reader = readerOf(token);
    if ('refused' in reader) return reader.refused;
    if (!reader.device && decodeURIComponent(inventory[3] ?? '') !== STATION) {
      return json({ detail: 'Name the station' }, 422);
    }
    return json(inventoryWire());
  }
  if (url === `/api/scan-stations/${STATION}/context`) {
    const reader = readerOf(token);
    if ('refused' in reader) return reader.refused;
    return json({
      station_id: STATION,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(),
      operations: [operationRef()],
      has_machines: false,
      worker_identification: {
        mode: workerMode,
        fixed_worker: null,
        session: null,
        final_gates: { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' },
      },
      theme_preference: null,
      device: reader.device ? { id: 9, label: 'Station PC' } : null,
      viewer: reader.device ? null : { id: 7, name: 'Jane Doe' },
      station_permissions: reader.device ? [...STATION_PERMISSIONS] : [],
    });
  }
  if (url.startsWith('/api/scan-stations/')) {
    // Every other station route is device-only.
    if (token === null || tokens.get(token) !== STATION) {
      return json({ detail: D1, station_device_required: true }, 401);
    }
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  window.localStorage.clear();
  userPermissions = ['MANAGE_SCAN_STATIONS'];
  workerMode = 'DISABLED';
  tokens = new Map();
  codes = new Map([['K7M2QX9RTA', STATION]]);
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const token = headers['X-PartFlow-Station-Device'] ?? null;
      requests.push({ url, method, token });
      return Promise.resolve().then(() => handle(url, method, body, token));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function open(suffix = '') {
  window.history.replaceState({}, '', `/scan-station/${STATION}${suffix}`);
  render(<App />);
}

async function viewingNotice() {
  return screen.findByRole('status', { name: 'Viewing only' });
}

test('a signed-in user with Manage Scan Stations views the station without an enrolled device', async () => {
  open();
  const notice = await viewingNotice();
  expect(notice.textContent).toContain(VIEWING_NOTICE);
  // What the station shows: its header and its Area inventory.
  await screen.findByText('Total PNs');
  expect(screen.getAllByText('PN-H').length).toBeGreaterThan(0);
  const header = document.querySelector('.ss-head') as HTMLElement;
  expect(header.textContent).toContain('Deburr');
  // The inventory read named the viewed station; no read sent a token.
  expect(
    requests.some(
      (r) => r.url === `/api/areas/2/inventory?station_id=${STATION}`,
    ),
  ).toBe(true);
  expect(requests.every((r) => r.token === null)).toBe(true);
  // Every write entry point is disabled or hidden.
  const input = screen.getByLabelText('Scan barcode');
  expect(input).toBeDisabled();
  expect(input).toHaveAttribute(
    'placeholder',
    'Viewing only — scanning needs an enrolled device',
  );
  expect(
    screen.getByRole('button', { name: '⌨ Enter PN manually' }),
  ).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Complete Area processing' }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: /UNDO/ })).toBeNull();
  // No enrollment panel or dialog blocks the view.
  expect(
    screen.queryByRole('region', { name: 'Enroll this device' }),
  ).toBeNull();
  expect(screen.queryByRole('dialog')).toBeNull();
  // Nothing station-only was sent.
  expect(
    requests.filter(
      (r) =>
        r.url.startsWith('/api/scan-stations/') && !r.url.endsWith('/context'),
    ),
  ).toEqual([]);
});

test('a viewer of a Scanned-session station is never asked for a Worker badge', async () => {
  workerMode = 'SCANNED';
  open();
  await viewingNotice();
  await screen.findByText('Total PNs');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByLabelText('Scan barcode')).toBeDisabled();
  expect(requests.some((r) => r.url.endsWith('/badge-scans'))).toBe(false);
});

test('enrolling from the viewing notice turns the browser into the station device', async () => {
  open();
  const notice = await viewingNotice();
  await screen.findByText('Total PNs');
  fireEvent.click(
    within(notice).getByRole('button', { name: 'Enroll this device' }),
  );
  let dialog = await screen.findByRole('dialog', {
    name: 'Enroll this device',
  });
  // Opened by choice, the dialog closes without changing anything.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(await viewingNotice()).toBeInTheDocument();
  expect(requests.some((r) => r.url.endsWith('/device-activations'))).toBe(
    false,
  );

  fireEvent.click(
    within(await viewingNotice()).getByRole('button', {
      name: 'Enroll this device',
    }),
  );
  dialog = await screen.findByRole('dialog', { name: 'Enroll this device' });
  fireEvent.change(within(dialog).getByLabelText('Enrollment code'), {
    target: { value: 'K7M2Q-X9RTA' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Enroll device' }),
  );
  // The station now behaves as today: no notice, the scan input works.
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: 'Viewing only' })).toBeNull(),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Scan barcode')).toBeEnabled(),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.getAllByRole('button', { name: 'Complete Area processing' }).length,
  ).toBeGreaterThan(0);
  const context = requests.filter((r) => r.url.endsWith('/context')).at(-1);
  expect(context?.token).toBe('token-new');
});

test('a refused stored token falls back to viewing for a user who may view', async () => {
  window.localStorage.setItem(
    `partflow.station-device.${STATION}`,
    'token-revoked',
  );
  open();
  await viewingNotice();
  await screen.findByText('Total PNs');
  expect(
    screen.queryByRole('region', { name: 'Enroll this device' }),
  ).toBeNull();
  expect(screen.queryByRole('dialog')).toBeNull();
  // The refused token was forgotten; the second read sent none.
  expect(
    window.localStorage.getItem(`partflow.station-device.${STATION}`),
  ).toBeNull();
  const contexts = requests.filter((r) => r.url.endsWith('/context'));
  expect(contexts.some((r) => r.token === 'token-revoked')).toBe(true);
  expect(contexts.at(-1)?.token).toBeNull();
});

test('a user without Manage Scan Stations keeps the enrollment panel', async () => {
  userPermissions = ['VIEW_PRODUCTION_DATA'];
  open();
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  expect(panel.textContent).toContain(
    `This device is not enrolled for Scan Station ${STATION}.`,
  );
  expect(screen.queryByRole('status', { name: 'Viewing only' })).toBeNull();
  expect(screen.queryByLabelText('Scan barcode')).toBeNull();
});

test('a signed-out browser keeps the enrollment panel', async () => {
  userPermissions = null;
  open();
  await screen.findByRole('region', { name: 'Enroll this device' });
  expect(screen.queryByRole('status', { name: 'Viewing only' })).toBeNull();
});

test('production mode stays enrolled-device only, even for a user who may view', async () => {
  open('/production');
  const panel = await screen.findByRole('region', {
    name: 'Enroll this device',
  });
  expect(panel.textContent).toContain(
    `This device is not enrolled for Scan Station ${STATION}. Production at this station needs an enrolled device.`,
  );
  expect(screen.queryByRole('status', { name: 'Viewing only' })).toBeNull();
  expect(screen.queryByLabelText('Scan barcode')).toBeNull();
  // No inventory is read for a station production mode does not open.
  expect(requests.some((r) => r.url.includes('/inventory'))).toBe(false);
});
