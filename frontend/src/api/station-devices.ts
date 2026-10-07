// Scan Station devices API (Phase 14 slice 4 — owner decision OD-P6).
//
// Every Scan Station request carries the enrolled-device token of its
// station in the `X-PartFlow-Station-Device` header. The browser keeps
// one token per station in `localStorage` (key
// `partflow.station-device.<stationId>`), or — when storage is
// unavailable — in memory for the page's lifetime. The token is shown
// on no screen: an administrator issues a short one-time enrollment
// code (Administration → Scan Stations → Devices), the station device
// exchanges it here for its token, and the server keeps only a digest.
// A device authenticates a terminal for one station, never a person;
// what an enrolled station may do follows the role applied at Scan
// Stations, judged by the server per command.
//
// Wire responses are the backend's snake_case; the exported types are
// camelCase. Converters fail loudly on malformed bodies and unknown
// values — this client is then out of date.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, refusalFlag, STATION_DEVICE_HEADER } from './client';
import { PERMISSIONS } from './roles';
import type { Permission } from './roles';

export { STATION_DEVICE_HEADER };

// ---------------------------------------------------------------------------
// Device token storage (station browser)
// ---------------------------------------------------------------------------

const STORAGE_PREFIX = 'partflow.station-device.';

/** Tokens kept for the page's lifetime when storage is unavailable. */
const memoryTokens = new Map<string, string>();

function storageKey(stationId: string): string {
  return `${STORAGE_PREFIX}${stationId}`;
}

/** The device token this browser holds for `stationId`, if any. */
export function readStationDeviceToken(stationId: string): string | null {
  try {
    const stored = window.localStorage.getItem(storageKey(stationId));
    if (stored !== null) return stored;
  } catch {
    // Storage unavailable (blocked, private mode): the memory copy.
  }
  return memoryTokens.get(stationId) ?? null;
}

/**
 * Keep the token of a just-enrolled device, replacing any token held
 * for that station. `persisted: false` = the browser's storage refused
 * it: the enrollment lasts until this page is closed.
 */
export function storeStationDeviceToken(
  stationId: string,
  token: string,
): { persisted: boolean } {
  try {
    window.localStorage.setItem(storageKey(stationId), token);
    memoryTokens.delete(stationId);
    return { persisted: true };
  } catch {
    memoryTokens.set(stationId, token);
    return { persisted: false };
  }
}

/**
 * Compare-and-delete: forget the station's token only when it is still
 * `expectedToken` — a late refusal of an older token never removes a
 * token stored since (in this tab or another).
 */
export function forgetStationDeviceToken(
  stationId: string,
  expectedToken: string,
): void {
  try {
    if (window.localStorage.getItem(storageKey(stationId)) === expectedToken) {
      window.localStorage.removeItem(storageKey(stationId));
    }
  } catch {
    // Storage unavailable: only the memory copy can hold the token.
  }
  if (memoryTokens.get(stationId) === expectedToken) {
    memoryTokens.delete(stationId);
  }
}

/** The device header of a request addressed to `stationId` ({} without
 * a token — the server then refuses with `station_device_required`). */
export function stationDeviceHeaders(
  stationId: string,
): Record<string, string> {
  const token = readStationDeviceToken(stationId);
  return token === null ? {} : { [STATION_DEVICE_HEADER]: token };
}

/** The station-device refusal of a failed call: `required` (401, not
 * enrolled / revoked / replaced) or `mismatch` (403, enrolled for
 * another station); null for any other outcome. */
export function stationDeviceRefusal(
  error: unknown,
): 'required' | 'mismatch' | null {
  if (refusalFlag(error, 'station_device_required')) return 'required';
  if (refusalFlag(error, 'station_device_mismatch')) return 'mismatch';
  return null;
}

/** True for the 403 `station_permission_denied`: the role applied at
 * Scan Stations does not grant the action. Judged after the
 * idempotency re-check — nothing was recorded. */
export function stationPermissionDenied(error: unknown): boolean {
  return refusalFlag(error, 'station_permission_denied');
}

// ---------------------------------------------------------------------------
// Activation (station browser)
// ---------------------------------------------------------------------------

export interface ActivatedStationDevice {
  id: number;
  stationId: string;
  label: string;
  activatedAt: string;
}

interface DeviceActivationWire {
  device_token: string;
  device: {
    id: number;
    station_id: string;
    label: string;
    activated_at: string;
  };
}

/**
 * Exchange a one-time enrollment code for this device's token. Not
 * idempotent: a code works once — a lost answer leaves the code used
 * and the administrator issues a new one.
 */
export async function activateStationDevice(
  stationId: string,
  enrollmentCode: string,
): Promise<{ deviceToken: string; device: ActivatedStationDevice }> {
  const wire = await apiRequest<DeviceActivationWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}/device-activations`,
    { method: 'POST', body: { enrollment_code: enrollmentCode } },
  );
  if (
    typeof wire?.device_token !== 'string' ||
    wire.device_token === '' ||
    typeof wire.device?.id !== 'number' ||
    typeof wire.device.station_id !== 'string' ||
    typeof wire.device.label !== 'string' ||
    typeof wire.device.activated_at !== 'string'
  ) {
    throw new Error('Malformed device activation answer from the server.');
  }
  return {
    deviceToken: wire.device_token,
    device: {
      id: wire.device.id,
      stationId: wire.device.station_id,
      label: wire.device.label,
      activatedAt: wire.device.activated_at,
    },
  };
}

// ---------------------------------------------------------------------------
// Administration → Scan Stations → Devices
// ---------------------------------------------------------------------------

export type StationDeviceState = 'PENDING' | 'ACTIVE' | 'EXPIRED' | 'REVOKED';

export interface StationDevice {
  id: number;
  stationId: string;
  label: string;
  state: StationDeviceState;
  enrollmentExpiresAt: string;
  activatedAt: string | null;
  lastSeenAt: string | null;
  revokedAt: string | null;
  revokedReason: 'REVOKED' | 'REPLACED' | null;
  replacesDeviceId: number | null;
}

interface StationDeviceWire {
  id: number;
  station_id: string;
  label: string;
  state: string;
  enrollment_expires_at: string;
  activated_at: string | null;
  last_seen_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  replaces_device_id: number | null;
}

const DEVICE_STATES: readonly StationDeviceState[] = [
  'PENDING',
  'ACTIVE',
  'EXPIRED',
  'REVOKED',
];
const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(PERMISSIONS);

function isDeviceState(value: unknown): value is StationDeviceState {
  return DEVICE_STATES.includes(value as StationDeviceState);
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && value !== '';
}

function isOptionalTimestamp(value: unknown): value is string | null {
  return value === null || isTimestamp(value);
}

function toStationDevice(wire: StationDeviceWire): StationDevice {
  if (
    typeof wire?.id !== 'number' ||
    typeof wire.station_id !== 'string' ||
    typeof wire.label !== 'string' ||
    !isDeviceState(wire.state) ||
    !isTimestamp(wire.enrollment_expires_at) ||
    !isOptionalTimestamp(wire.activated_at) ||
    !isOptionalTimestamp(wire.last_seen_at) ||
    !isOptionalTimestamp(wire.revoked_at) ||
    !(
      wire.revoked_reason === null ||
      wire.revoked_reason === 'REVOKED' ||
      wire.revoked_reason === 'REPLACED'
    ) ||
    !(
      wire.replaces_device_id === null ||
      typeof wire.replaces_device_id === 'number'
    )
  ) {
    throw new Error('Malformed Scan Station device from the server.');
  }
  return {
    id: wire.id,
    stationId: wire.station_id,
    label: wire.label,
    state: wire.state,
    enrollmentExpiresAt: wire.enrollment_expires_at,
    activatedAt: wire.activated_at,
    lastSeenAt: wire.last_seen_at,
    revokedAt: wire.revoked_at,
    revokedReason: wire.revoked_reason,
    replacesDeviceId: wire.replaces_device_id,
  };
}

function toPermission(key: unknown): Permission {
  if (typeof key !== 'string' || !KNOWN_PERMISSIONS.has(key)) {
    throw new Error(`Unknown permission key from the server: ${String(key)}`);
  }
  return key as Permission;
}

/**
 * The open devices of every station (enrolled, and unexpired codes)
 * and what issuing an enrollment code needs now: Manage Scan Stations,
 * plus Manage correction permissions while the role applied at Scan
 * Stations holds a correction permission.
 */
export async function listStationDevices(): Promise<{
  devices: StationDevice[];
  enrollmentPermissions: Permission[];
}> {
  const wire = await apiRequest<{
    devices: StationDeviceWire[];
    enrollment_permissions: unknown[];
  }>('/api/scan-station-devices');
  if (
    !Array.isArray(wire?.devices) ||
    !Array.isArray(wire.enrollment_permissions)
  ) {
    throw new Error('Malformed Scan Station device list from the server.');
  }
  return {
    devices: wire.devices.map(toStationDevice),
    enrollmentPermissions: wire.enrollment_permissions.map(toPermission),
  };
}

/**
 * Issue a one-time enrollment code (shown once, 15 minutes, single
 * use) for a new device of `stationId`, or — with `replacesDeviceId` —
 * for the device that replaces an enrolled one when the code is used.
 */
export async function issueStationDeviceEnrollment(
  stationId: string,
  input: { label: string; replacesDeviceId?: number | null },
): Promise<{ device: StationDevice; enrollmentCode: string }> {
  const wire = await apiRequest<{
    device: StationDeviceWire;
    enrollment_code: string;
  }>(`/api/scan-stations/${encodeURIComponent(stationId)}/device-enrollments`, {
    method: 'POST',
    body: {
      label: input.label,
      replaces_device_id: input.replacesDeviceId ?? null,
    },
  });
  if (typeof wire?.enrollment_code !== 'string' || !wire.enrollment_code) {
    throw new Error('Malformed enrollment answer from the server.');
  }
  return {
    device: toStationDevice(wire.device),
    enrollmentCode: wire.enrollment_code,
  };
}

/** Revoke a device or cancel a pending code (idempotent). */
export async function revokeStationDevice(
  deviceId: number,
): Promise<StationDevice> {
  const wire = await apiRequest<StationDeviceWire>(
    `/api/scan-station-devices/${deviceId}/revocation`,
    { method: 'POST' },
  );
  return toStationDevice(wire);
}
