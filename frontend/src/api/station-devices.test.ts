import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { confirmAllocation, getAllocationSuggestion } from './allocations';
import { getAreaInventory } from './area-inventory';
import { ApiError } from './client';
import {
  addQuantity,
  combineQuantities,
  getStationContext,
  getUndoPreview,
  receiveQuantity,
  recordMachineAction,
  resolveMachineScan,
  resolveScan,
  saveStationThemePreference,
  scanBadge,
  scrapQuantity,
  stockAtStationArea,
  transferToStationArea,
  undoProductionCommand,
} from './scan-station';
import {
  STATION_DEVICE_HEADER,
  activateStationDevice,
  forgetStationDeviceToken,
  issueStationDeviceEnrollment,
  listStationDevices,
  readStationDeviceToken,
  revokeStationDevice,
  stationDeviceHeaders,
  stationDeviceRefusal,
  stationPermissionDenied,
  storeStationDeviceToken,
} from './station-devices';

// Scan Station devices (Phase 14 slice 4): every station call carries
// the enrolled-device token of ITS station in `X-PartFlow-Station-
// Device` (none without a stored token), the token lives per station in
// `localStorage` with an in-memory fallback, a token is forgotten only
// compare-and-delete, and the device API converts the wire contract
// strictly.

let fetchMock: ReturnType<typeof vi.fn>;
let answer: { body: unknown; status: number };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  window.localStorage.clear();
  answer = { body: { detail: 'Not answered here.' }, status: 500 };
  fetchMock = vi.fn(async () => json(answer.body, answer.status));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

/** Every station API call, addressed to `stationId`. */
const STATION_CALLS: [string, (stationId: string) => Promise<unknown>][] = [
  ['station context', (id) => getStationContext(id)],
  ['PN resolve', (id) => resolveScan(id, { barcode: 'PF:PN:PN-1' })],
  [
    'Machine resolve',
    (id) => resolveMachineScan(id, { barcode: 'PF:MACHINE:CD-1' }),
  ],
  ['badge scan', (id) => scanBadge(id, 'B-1')],
  ['transfer', (id) => transferToStationArea({ stationId: id } as never)],
  ['stocking', (id) => stockAtStationArea({ stationId: id } as never)],
  [
    'assignment',
    (id) => recordMachineAction('ASSIGN', { stationId: id } as never),
  ],
  ['QUEUE', (id) => recordMachineAction('QUEUE', { stationId: id } as never)],
  ['DONE', (id) => recordMachineAction('DONE', { stationId: id } as never)],
  ['merge', (id) => combineQuantities({ stationId: id } as never)],
  ['receipt', (id) => receiveQuantity({ stationId: id } as never)],
  ['scrap', (id) => scrapQuantity({ stationId: id } as never)],
  ['quantity addition', (id) => addQuantity({ stationId: id } as never)],
  ['Undo preview', (id) => getUndoPreview(id, 'evt-1')],
  ['Undo', (id) => undoProductionCommand({ stationId: id } as never)],
  ['allocation suggestion', (id) => getAllocationSuggestion(id, 'PN-1', 5)],
  [
    'allocation',
    (id) =>
      confirmAllocation({
        stationId: id,
        partNumber: 'PN-1',
        allocationQuantity: 5,
        lines: [],
        deviceEventId: 'evt-2',
        suggestionUnchanged: true,
      }),
  ],
  ['Area inventory', (id) => getAreaInventory(7, id)],
  ['station theme', (id) => saveStationThemePreference(id, 'dark')],
];

function sentHeaders(call = 0): Record<string, string> {
  return (fetchMock.mock.calls[call][1] as RequestInit).headers as Record<
    string,
    string
  >;
}

test.each(STATION_CALLS)(
  'FS-2: the %s call carries the device token of its own station, and none without one',
  async (_name, call) => {
    storeStationDeviceToken('ST-A', 'token-a');
    storeStationDeviceToken('ST-B', 'token-b');
    await call('ST-A').catch(() => undefined);
    expect(sentHeaders(0)[STATION_DEVICE_HEADER]).toBe('token-a');
    // The request-origin header is never replaced.
    expect(sentHeaders(0)['X-PartFlow-CSRF']).toBe('1');
    await call('ST-B').catch(() => undefined);
    expect(sentHeaders(1)[STATION_DEVICE_HEADER]).toBe('token-b');
    await call('ST-NONE').catch(() => undefined);
    expect(STATION_DEVICE_HEADER in sentHeaders(2)).toBe(false);
  },
);

test('the station allocation sends suggestion_unchanged; the suggestion keeps its query', async () => {
  storeStationDeviceToken('STOCK-ST', 'token-s');
  await confirmAllocation({
    stationId: 'STOCK-ST',
    partNumber: 'PN-1',
    allocationQuantity: 5,
    lines: [{ workOrderDemandId: 3, quantity: 5 }],
    deviceEventId: 'evt-3',
    suggestionUnchanged: false,
  }).catch(() => undefined);
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/allocations');
  expect(JSON.parse(init.body as string)).toEqual({
    part_number: 'PN-1',
    allocation_quantity: 5,
    lines: [{ work_order_demand_id: 3, quantity: 5 }],
    station_id: 'STOCK-ST',
    device_event_id: 'evt-3',
    suggestion_unchanged: false,
  });
  await getAllocationSuggestion('STOCK-ST', 'PN-1', 5).catch(() => undefined);
  expect(fetchMock.mock.calls[1][0]).toBe(
    '/api/allocations/suggestion?part_number=PN-1&quantity=5',
  );
});

test('FS-3: tokens are kept per station; forgetting compares first; unavailable storage falls back to memory', () => {
  expect(readStationDeviceToken('ST-A')).toBeNull();
  expect(stationDeviceHeaders('ST-A')).toEqual({});
  expect(storeStationDeviceToken('ST-A', 'T1')).toEqual({ persisted: true });
  expect(storeStationDeviceToken('ST-B', 'TB')).toEqual({ persisted: true });
  expect(window.localStorage.getItem('partflow.station-device.ST-A')).toBe(
    'T1',
  );
  expect(stationDeviceHeaders('ST-A')).toEqual({
    'X-PartFlow-Station-Device': 'T1',
  });

  // A late refusal of an older token never removes the newer one.
  storeStationDeviceToken('ST-A', 'T2');
  forgetStationDeviceToken('ST-A', 'T1');
  expect(readStationDeviceToken('ST-A')).toBe('T2');
  forgetStationDeviceToken('ST-A', 'T2');
  expect(readStationDeviceToken('ST-A')).toBeNull();
  // Stations are independent.
  expect(readStationDeviceToken('ST-B')).toBe('TB');

  // Storage throwing (blocked, private mode): the page keeps the token.
  const setItem = vi
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
  const getItem = vi
    .spyOn(Storage.prototype, 'getItem')
    .mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
  expect(storeStationDeviceToken('ST-C', 'TC')).toEqual({ persisted: false });
  expect(readStationDeviceToken('ST-C')).toBe('TC');
  forgetStationDeviceToken('ST-C', 'other');
  expect(readStationDeviceToken('ST-C')).toBe('TC');
  forgetStationDeviceToken('ST-C', 'TC');
  expect(readStationDeviceToken('ST-C')).toBeNull();
  setItem.mockRestore();
  getItem.mockRestore();
});

test('the refusal helpers read the server flags', () => {
  const refusal = (body: unknown, status: number) =>
    new ApiError(status, 'refused', body);
  expect(
    stationDeviceRefusal(refusal({ station_device_required: true }, 401)),
  ).toBe('required');
  expect(
    stationDeviceRefusal(refusal({ station_device_mismatch: true }, 403)),
  ).toBe('mismatch');
  expect(
    stationDeviceRefusal(refusal({ station_permission_denied: true }, 403)),
  ).toBeNull();
  expect(
    stationPermissionDenied(
      refusal(
        {
          station_permission_denied: true,
          required_permissions: ['CONFIRM_QUANTITY'],
        },
        403,
      ),
    ),
  ).toBe(true);
  expect(stationPermissionDenied(new TypeError('offline'))).toBe(false);
});

test('activation posts the code to the station and returns the token once', async () => {
  answer = {
    status: 201,
    body: {
      device_token: 'tok-xyz',
      device: {
        id: 9,
        station_id: 'ST-A',
        label: 'Lathe cell PC',
        activated_at: '2026-10-07T08:00:00Z',
      },
    },
  };
  const result = await activateStationDevice('ST-A', 'k7m2q x9rta');
  expect(result).toEqual({
    deviceToken: 'tok-xyz',
    device: {
      id: 9,
      stationId: 'ST-A',
      label: 'Lathe cell PC',
      activatedAt: '2026-10-07T08:00:00Z',
    },
  });
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/scan-stations/ST-A/device-activations');
  expect(init.method).toBe('POST');
  expect(JSON.parse(init.body as string)).toEqual({
    enrollment_code: 'k7m2q x9rta',
  });
  answer = { status: 201, body: { device_token: '', device: null } };
  await expect(activateStationDevice('ST-A', 'x')).rejects.toThrow(/Malformed/);
});

const DEVICE_WIRE = {
  id: 4,
  station_id: 'ST-A',
  label: 'Desk PC',
  state: 'PENDING',
  enrollment_expires_at: '2026-10-07T09:15:00Z',
  activated_at: null,
  last_seen_at: null,
  revoked_at: null,
  revoked_reason: null,
  replaces_device_id: 2,
};

test('the Administration device calls convert strictly', async () => {
  answer = {
    status: 200,
    body: {
      devices: [DEVICE_WIRE],
      enrollment_permissions: [
        'MANAGE_CORRECTION_PERMISSIONS',
        'MANAGE_SCAN_STATIONS',
      ],
    },
  };
  expect(await listStationDevices()).toEqual({
    devices: [
      {
        id: 4,
        stationId: 'ST-A',
        label: 'Desk PC',
        state: 'PENDING',
        enrollmentExpiresAt: '2026-10-07T09:15:00Z',
        activatedAt: null,
        lastSeenAt: null,
        revokedAt: null,
        revokedReason: null,
        replacesDeviceId: 2,
      },
    ],
    enrollmentPermissions: [
      'MANAGE_CORRECTION_PERMISSIONS',
      'MANAGE_SCAN_STATIONS',
    ],
  });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/scan-station-devices');

  answer = {
    status: 200,
    body: {
      devices: [{ ...DEVICE_WIRE, state: 'LOST' }],
      enrollment_permissions: [],
    },
  };
  await expect(listStationDevices()).rejects.toThrow(/Malformed/);
  answer = {
    status: 200,
    body: { devices: [], enrollment_permissions: ['NOPE'] },
  };
  await expect(listStationDevices()).rejects.toThrow(/NOPE/);

  answer = {
    status: 201,
    body: { device: DEVICE_WIRE, enrollment_code: 'K7M2Q-X9RTA' },
  };
  const issued = await issueStationDeviceEnrollment('ST-A', {
    label: 'Desk PC',
    replacesDeviceId: 2,
  });
  expect(issued.enrollmentCode).toBe('K7M2Q-X9RTA');
  const [issuePath, issueInit] = fetchMock.mock.calls[3] as [
    string,
    RequestInit,
  ];
  expect(issuePath).toBe('/api/scan-stations/ST-A/device-enrollments');
  expect(JSON.parse(issueInit.body as string)).toEqual({
    label: 'Desk PC',
    replaces_device_id: 2,
  });
  // No device header on Administration calls.
  expect(STATION_DEVICE_HEADER in (issueInit.headers as object)).toBe(false);

  answer = {
    status: 200,
    body: {
      ...DEVICE_WIRE,
      state: 'REVOKED',
      revoked_at: '2026-10-07T09:00:00Z',
      revoked_reason: 'REVOKED',
    },
  };
  expect((await revokeStationDevice(4)).state).toBe('REVOKED');
  expect(fetchMock.mock.calls[4][0]).toBe(
    '/api/scan-station-devices/4/revocation',
  );
  expect((fetchMock.mock.calls[4][1] as RequestInit).method).toBe('POST');
});
