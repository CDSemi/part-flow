// Minimal typed API client core (Phase 3.5).
//
// Every request goes to the same-origin `/api` surface (the dev server
// and Docker Compose proxy it to the backend — vite.config.ts). The
// client is deliberately thin: JSON in/out, and the backend's central
// `{"detail": ...}` error shape translated into one typed `ApiError`
// carrying the safe, user-facing message. No caching, no retries, no
// business rules — validation and transactions live in the backend
// Application layer. The one binary path is `apiUpload`: a raw image
// or import-file body labelled with its own media type (no multipart),
// answered with JSON like every other call.
//
// Every request carries `X-PartFlow-CSRF: 1`: the server refuses a
// state-changing request that carries the user sign-in cookie without
// it, and no other origin can send it (no CORS grant exists). The
// browser's default `credentials` (same-origin) send that cookie. One
// optional listener hears the two refusals that change what the session
// UI shows — the sign-in has ended, or a new password must be chosen
// first — before the `ApiError` is thrown as usual. A Scan Station
// call adds its enrolled-device header (`X-PartFlow-Station-Device`,
// never overriding the two headers above), and a second listener hears
// the two station-device refusals — this device is not enrolled for the
// station (any more), or it is enrolled for another one — with the
// device header value that request actually sent.
//
// Production-safe: no mock data, no framework imports.

/** One failed API call: HTTP status plus the user-facing message. */
export class ApiError extends Error {
  readonly status: number;

  /**
   * The parsed JSON error body, when there was one. Almost every
   * caller only needs `message`; the release flow additionally reads
   * the confirmation-required payload (`confirmation_required` +
   * `existing_active_quantity`) the backend attaches to its 409.
   */
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** Marks a request as sent by the PartFlow application. */
const CSRF_HEADERS: Readonly<Record<string, string>> = {
  'X-PartFlow-CSRF': '1',
};

/** The header carrying a Scan Station's enrolled-device token. */
export const STATION_DEVICE_HEADER = 'X-PartFlow-Station-Device';

/** The two station-device refusals the Scan Station reacts to. */
export type StationDeviceRefusalKind = 'required' | 'mismatch';

let stationDeviceRefusalListener:
  ((kind: StationDeviceRefusalKind, sentToken: string | null) => void) | null =
  null;

/**
 * Register (or clear, with null) the one listener told about a 401
 * `station_device_required` or a 403 `station_device_mismatch`
 * answer, with the `X-PartFlow-Station-Device` value the refused
 * request sent (null when it sent none). No other refusal calls it —
 * not a sign-in refusal, a stale station context or a missing station
 * permission.
 */
export function setStationDeviceRefusalListener(
  listener:
    ((kind: StationDeviceRefusalKind, sentToken: string | null) => void) | null,
): void {
  stationDeviceRefusalListener = listener;
}

/** The station-device refusal a failed answer carries, if any. */
function stationDeviceRefusalKind(
  status: number,
  body: unknown,
): StationDeviceRefusalKind | null {
  if (!body || typeof body !== 'object') return null;
  const flags = body as Record<string, unknown>;
  if (status === 401 && flags.station_device_required === true) {
    return 'required';
  }
  if (status === 403 && flags.station_device_mismatch === true) {
    return 'mismatch';
  }
  return null;
}

/** The two authentication refusals the session UI reacts to. */
export type AuthFailureKind =
  'authentication_required' | 'password_change_required';

let authFailureListener:
  ((kind: AuthFailureKind, prompt: boolean) => void) | null = null;

/**
 * Register (or clear, with null) the one listener told about a 401
 * `authentication_required` or a 403 `password_change_required`
 * answer. Every other refusal (a failed sign-in, a missing permission,
 * a refused request origin) never calls it. `prompt` is false only for
 * an ended sign-in refused on a request sent with `promptSignIn: false`.
 */
export function setAuthFailureListener(
  listener: ((kind: AuthFailureKind, prompt: boolean) => void) | null,
): void {
  authFailureListener = listener;
}

/** The authentication refusal a failed answer carries, if any. */
function authFailureKind(
  status: number,
  body: unknown,
): AuthFailureKind | null {
  if (!body || typeof body !== 'object') return null;
  const flags = body as Record<string, unknown>;
  if (status === 401 && flags.authentication_required === true) {
    return 'authentication_required';
  }
  if (status === 403 && flags.password_change_required === true) {
    return 'password_change_required';
  }
  return null;
}

/**
 * Whether a refusal carries the boolean flag `flag` in its JSON body
 * (e.g. `authentication_required`, `permission_denied`,
 * `recorded_by_another_user`).
 */
export function refusalFlag(error: unknown, flag: string): boolean {
  return (
    error instanceof ApiError &&
    typeof error.body === 'object' &&
    error.body !== null &&
    (error.body as Record<string, unknown>)[flag] === true
  );
}

/**
 * User-facing message of a failed call: the backend's own message for
 * an `ApiError`, one generic unreachable-server sentence for network
 * failures (never a raw internal error).
 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'The PartFlow server could not be reached. Nothing was changed.';
}

/**
 * FastAPI's `detail` may be a string (application errors) or an array
 * of field issues (request validation). Reduce both to one sentence.
 */
function detailToMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown } | undefined;
    if (first && typeof first.msg === 'string') return first.msg;
  }
  return `The request failed (HTTP ${status}).`;
}

/** The options of one JSON API request. */
export interface ApiRequestInit {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  /** Extra request headers (a Scan Station's device header); added
   * after `Content-Type` and the request-origin header, never
   * overriding either. */
  headers?: Readonly<Record<string, string>>;
  /**
   * Pass `promptSignIn: false` for a background save whose caller
   * reports an ended sign-in itself (the theme preference). The listener
   * still records that the sign-in ended, but it opens no Sign-in
   * dialog: a theme click never raises one, and Scan Station routes and
   * the Production Board never ask for sign-in. A required password
   * change is unaffected. Default true.
   */
  promptSignIn?: boolean;
}

/**
 * Perform one JSON API request; the result keeps the HTTP status
 * beside the parsed body for the rare caller that distinguishes
 * statuses (created 201 vs. idempotent replay 200).
 */
export async function apiRequestWithStatus<T>(
  path: string,
  init?: ApiRequestInit,
): Promise<{ status: number; data: T }> {
  const headers: Record<string, string> =
    init?.body !== undefined
      ? { 'Content-Type': 'application/json', ...CSRF_HEADERS }
      : { ...CSRF_HEADERS };
  for (const [name, value] of Object.entries(init?.headers ?? {})) {
    if (!(name in headers)) headers[name] = value;
  }
  const response = await fetch(path, {
    method: init?.method ?? 'GET',
    headers,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  return readResponse<T>(response, headers, init?.promptSignIn ?? true);
}

/**
 * Upload one raw binary body (an image, a Work Order import file)
 * labelled with the blob's own media type, and parse the JSON response
 * body. The caller makes sure `blob.type` matches the bytes; the server
 * re-checks both. Extra `headers` (the import's check token) are added
 * after `Content-Type` and the request-origin header, never overriding
 * either.
 */
export async function apiUpload<T>(
  path: string,
  blob: Blob,
  method: 'PUT' | 'POST' = 'PUT',
  headers?: Readonly<Record<string, string>>,
): Promise<T> {
  const sent: Record<string, string> = {
    'Content-Type': blob.type,
    ...CSRF_HEADERS,
  };
  for (const [name, value] of Object.entries(headers ?? {})) {
    if (!(name in sent)) sent[name] = value;
  }
  const response = await fetch(path, { method, headers: sent, body: blob });
  return (await readResponse<T>(response, sent, true)).data;
}

/**
 * Shared response handling of every call: a non-2xx answer becomes an
 * `ApiError` carrying the backend's message, a 2xx answer is parsed as
 * JSON (204 has no body). `sentHeaders` are the headers the request
 * carried (the station-device listener is told the token it sent);
 * `promptSignIn` is the request's option (`ApiRequestInit`).
 */
async function readResponse<T>(
  response: Response,
  sentHeaders: Readonly<Record<string, string>>,
  promptSignIn: boolean,
): Promise<{ status: number; data: T }> {
  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    const detail =
      body && typeof body === 'object'
        ? (body as { detail?: unknown }).detail
        : undefined;
    const kind = authFailureKind(response.status, body);
    if (kind !== null) {
      // A required password change always prompts (the forced dialog).
      authFailureListener?.(
        kind,
        kind === 'authentication_required' ? promptSignIn : true,
      );
    }
    const deviceRefusal = stationDeviceRefusalKind(response.status, body);
    if (deviceRefusal !== null) {
      stationDeviceRefusalListener?.(
        deviceRefusal,
        sentHeaders[STATION_DEVICE_HEADER] ?? null,
      );
    }
    throw new ApiError(
      response.status,
      detailToMessage(detail, response.status),
      body,
    );
  }
  // 204 No Content (e.g. a demand-line removal) has no body to parse.
  if (response.status === 204) {
    return { status: response.status, data: undefined as T };
  }
  return { status: response.status, data: (await response.json()) as T };
}

/** Perform one JSON API request and parse the JSON response body. */
export async function apiRequest<T>(
  path: string,
  init?: ApiRequestInit,
): Promise<T> {
  return (await apiRequestWithStatus<T>(path, init)).data;
}
