// Minimal typed API client core (Phase 3.5).
//
// Every request goes to the same-origin `/api` surface (the dev server
// and Docker Compose proxy it to the backend — vite.config.ts). The
// client is deliberately thin: JSON in/out, and the backend's central
// `{"detail": ...}` error shape translated into one typed `ApiError`
// carrying the safe, user-facing message. No caching, no retries, no
// business rules — validation and transactions live in the backend
// Application layer.
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

/**
 * Perform one JSON API request; the result keeps the HTTP status
 * beside the parsed body for the rare caller that distinguishes
 * statuses (created 201 vs. idempotent replay 200).
 */
export async function apiRequestWithStatus<T>(
  path: string,
  init?: {
    method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
    body?: unknown;
  },
): Promise<{ status: number; data: T }> {
  const response = await fetch(path, {
    method: init?.method ?? 'GET',
    headers:
      init?.body !== undefined
        ? { 'Content-Type': 'application/json' }
        : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
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
  init?: {
    method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
    body?: unknown;
  },
): Promise<T> {
  return (await apiRequestWithStatus<T>(path, init)).data;
}
