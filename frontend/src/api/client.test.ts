import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  ApiError,
  apiRequest,
  apiUpload,
  BUNDLE_RELEASE,
  isReleaseMismatch,
  RATE_LIMITED_MESSAGE,
  refusalFlag,
  REQUEST_TOO_LARGE_MESSAGE,
  setAuthFailureListener,
  setReleaseMismatchListener,
  setStationDeviceRefusalListener,
} from './client';
import { writeOutcomeUnknown } from './scan-station';

// The API client core: the JSON path and the one raw-body upload path
// share the same response handling — the backend's `{"detail": ...}`
// error body becomes one typed ApiError with the user-facing message.

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('apiUpload sends the blob as the raw body labelled with its own type', async () => {
  fetchMock.mockResolvedValue(json({ id: 3, name: 'Mai' }));
  const image = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], {
    type: 'image/png',
  });

  const result = await apiUpload<{ id: number }>(
    '/api/workers/3/avatar',
    image,
  );

  expect(result).toEqual({ id: 3, name: 'Mai' });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/workers/3/avatar');
  expect(init.method).toBe('PUT');
  expect(init.headers).toEqual({
    'Content-Type': 'image/png',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
  });
  expect(init.body).toBe(image);
});

test('apiUpload turns a rejected upload into an ApiError with the server message', async () => {
  fetchMock.mockResolvedValue(
    json(
      { detail: 'The image is larger than 2 MB. Choose a smaller image.' },
      413,
    ),
  );

  const failure = await apiUpload(
    '/api/workers/3/avatar',
    new Blob(['x'], { type: 'image/jpeg' }),
  ).catch((error: unknown) => error);

  expect(failure).toBeInstanceOf(ApiError);
  expect((failure as ApiError).status).toBe(413);
  expect((failure as ApiError).message).toBe(
    'The image is larger than 2 MB. Choose a smaller image.',
  );
});

test('FU-3: apiUpload with POST adds extra headers after Content-Type and the request-origin header, never replacing them', async () => {
  fetchMock.mockResolvedValue(json({ dry_run: false }));
  const file = new Blob(['a,b\r\n'], { type: 'text/csv' });

  await apiUpload('/api/work-orders/import', file, 'POST', {
    'X-PartFlow-Import-Check': 'f'.repeat(64),
    'Content-Type': 'application/octet-stream',
    'X-PartFlow-CSRF': '0',
  });

  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/work-orders/import');
  expect(init.method).toBe('POST');
  expect(init.headers).toEqual({
    'Content-Type': 'text/csv',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
    'X-PartFlow-Import-Check': 'f'.repeat(64),
  });
  expect(Object.keys(init.headers as object)).toEqual([
    'Content-Type',
    'X-PartFlow-CSRF',
    'X-PartFlow-Release',
    'X-PartFlow-Import-Check',
  ]);
  expect(init.body).toBe(file);
});

test('FU-3: an ended sign-in on an upload still reaches the auth-failure listener', async () => {
  const listener = vi.fn();
  setAuthFailureListener(listener);
  try {
    fetchMock.mockResolvedValue(
      json({ detail: 'Sign in.', authentication_required: true }, 401),
    );
    await expect(
      apiUpload(
        '/api/work-orders/import/preview',
        new Blob(['x'], { type: 'text/csv' }),
        'POST',
      ),
    ).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledWith('authentication_required', true);
  } finally {
    setAuthFailureListener(null);
  }
});

test('the JSON path is unchanged: JSON body out, parsed JSON or ApiError back', async () => {
  fetchMock.mockResolvedValueOnce(json({ id: 1, name: 'Alex Tran' }, 201));
  const created = await apiRequest<{ id: number }>('/api/workers', {
    method: 'POST',
    body: { name: 'Alex Tran', badge_barcode: 'ABC1' },
  });
  expect(created).toEqual({ id: 1, name: 'Alex Tran' });
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.method).toBe('POST');
  expect(init.headers).toEqual({
    'Content-Type': 'application/json',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
  });
  expect(init.body).toBe('{"name":"Alex Tran","badge_barcode":"ABC1"}');

  // A body-less failure keeps the generic status message.
  fetchMock.mockResolvedValueOnce(new Response('', { status: 502 }));
  await expect(apiRequest('/api/workers')).rejects.toEqual(
    new ApiError(502, 'The request failed (HTTP 502).'),
  );
});

test('a body-less GET carries only the request-origin header', async () => {
  fetchMock.mockResolvedValue(json([]));
  await apiRequest('/api/workers');
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.method).toBe('GET');
  expect(init.headers).toEqual({
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
  });
  expect(init.body).toBeUndefined();
  // The browser default (same-origin) sends the sign-in cookie.
  expect(init.credentials).toBeUndefined();
});

test('the auth-failure listener hears an ended sign-in and a required password change only', async () => {
  const listener = vi.fn();
  setAuthFailureListener(listener);
  try {
    fetchMock.mockResolvedValueOnce(
      json(
        {
          detail:
            'You are not signed in, or your sign-in has ended. Sign in to continue.',
          authentication_required: true,
        },
        401,
      ),
    );
    const ended = await apiRequest('/api/policies/sign-in').catch(
      (error: unknown) => error,
    );
    expect(ended).toBeInstanceOf(ApiError);
    expect((ended as ApiError).status).toBe(401);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenLastCalledWith('authentication_required', true);

    fetchMock.mockResolvedValueOnce(
      json(
        {
          detail: 'Choose a new password before you continue.',
          password_change_required: true,
        },
        403,
      ),
    );
    await expect(apiRequest('/api/policies/sign-in')).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(listener).toHaveBeenCalledTimes(2);
    expect(listener).toHaveBeenLastCalledWith('password_change_required', true);

    // Every other refusal leaves the listener alone.
    for (const [body, status] of [
      [{ detail: 'Sign-in failed.', sign_in_failed: true }, 401],
      [
        {
          detail: 'Your account does not have permission to do this.',
          permission_denied: true,
          required_permissions: ['MANAGE_USERS_AND_ROLES'],
        },
        403,
      ],
      [{ detail: 'This request was refused.', csrf_rejected: true }, 403],
      [{ detail: 'This login name is already used.' }, 409],
    ] as const) {
      fetchMock.mockResolvedValueOnce(json(body, status));
      await expect(
        apiRequest('/api/session', { method: 'POST', body: {} }),
      ).rejects.toBeInstanceOf(ApiError);
    }
    expect(listener).toHaveBeenCalledTimes(2);
  } finally {
    setAuthFailureListener(null);
  }
});

test('promptSignIn: false still tells the listener about an ended sign-in, without a prompt; a required password change always prompts', async () => {
  const listener = vi.fn();
  setAuthFailureListener(listener);
  try {
    const ended = { detail: 'Sign in.', authentication_required: true };
    const forced = {
      detail: 'Choose a new password before you continue.',
      password_change_required: true,
    };

    fetchMock.mockResolvedValueOnce(json(ended, 401));
    const error = await apiRequest('/api/session/theme-preference', {
      method: 'PUT',
      body: { theme_preference: 'LIGHT' },
      promptSignIn: false,
    }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(listener).toHaveBeenLastCalledWith('authentication_required', false);

    fetchMock.mockResolvedValueOnce(json(ended, 401));
    await expect(
      apiRequest('/api/policies/sign-in', { promptSignIn: true }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenLastCalledWith('authentication_required', true);

    for (const promptSignIn of [false, true, undefined]) {
      fetchMock.mockResolvedValueOnce(json(forced, 403));
      await expect(
        apiRequest('/api/session/theme-preference', {
          method: 'PUT',
          body: { theme_preference: 'DARK' },
          promptSignIn,
        }),
      ).rejects.toBeInstanceOf(ApiError);
      expect(listener).toHaveBeenLastCalledWith(
        'password_change_required',
        true,
      );
    }
    expect(listener).toHaveBeenCalledTimes(5);

    // The option never travels as a header or in the body.
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({
      'Content-Type': 'application/json',
      'X-PartFlow-CSRF': '1',
      'X-PartFlow-Release': 'development',
    });
    expect(init.body).toBe('{"theme_preference":"LIGHT"}');
  } finally {
    setAuthFailureListener(null);
  }
});

test('FS-1: extra headers follow Content-Type and the request-origin header, never replacing them', async () => {
  fetchMock.mockResolvedValue(json({}));
  await apiRequest('/api/scan-stations/ST-1/transfers', {
    method: 'POST',
    body: { quantity: 1 },
    headers: {
      'X-PartFlow-Station-Device': 'tok-1',
      'X-PartFlow-CSRF': '0',
      'Content-Type': 'text/plain',
    },
  });
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.headers).toEqual({
    'Content-Type': 'application/json',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
    'X-PartFlow-Station-Device': 'tok-1',
  });
  expect(Object.keys(init.headers as object)).toEqual([
    'Content-Type',
    'X-PartFlow-CSRF',
    'X-PartFlow-Release',
    'X-PartFlow-Station-Device',
  ]);
});

test('FS-1: the station-device listener hears exactly the two device refusals, with the token the request sent', async () => {
  const listener = vi.fn();
  const authListener = vi.fn();
  setStationDeviceRefusalListener(listener);
  setAuthFailureListener(authListener);
  try {
    fetchMock.mockResolvedValueOnce(
      json({ detail: 'not enrolled', station_device_required: true }, 401),
    );
    await expect(
      apiRequest('/api/scan-stations/ST-1/context', {
        headers: { 'X-PartFlow-Station-Device': 'tok-old' },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenLastCalledWith('required', 'tok-old');

    fetchMock.mockResolvedValueOnce(
      json({ detail: 'not enrolled', station_device_required: true }, 401),
    );
    await expect(
      apiRequest('/api/scan-stations/ST-1/context'),
    ).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenLastCalledWith('required', null);

    fetchMock.mockResolvedValueOnce(
      json({ detail: 'other station', station_device_mismatch: true }, 403),
    );
    await expect(
      apiRequest('/api/scan-stations/ST-1/context', {
        headers: { 'X-PartFlow-Station-Device': 'tok-b' },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenLastCalledWith('mismatch', 'tok-b');
    expect(listener).toHaveBeenCalledTimes(3);

    // Never for a stale station context, a missing station permission
    // or the user sign-in refusal.
    for (const [body, status] of [
      [
        {
          detail: "This Scan Station's Area changed.",
          station_context_changed: true,
        },
        409,
      ],
      [
        {
          detail: 'Scan Stations are not allowed to confirm quantity.',
          station_permission_denied: true,
          required_permissions: ['CONFIRM_QUANTITY'],
        },
        403,
      ],
      [{ detail: 'Sign in.', authentication_required: true }, 401],
    ] as const) {
      fetchMock.mockResolvedValueOnce(json(body, status));
      await expect(
        apiRequest('/api/areas/2/inventory', {
          headers: { 'X-PartFlow-Station-Device': 'tok-1' },
        }),
      ).rejects.toBeInstanceOf(ApiError);
    }
    expect(listener).toHaveBeenCalledTimes(3);
    // The device refusals never reach the sign-in listener.
    expect(authListener).toHaveBeenCalledTimes(1);
    expect(authListener).toHaveBeenLastCalledWith(
      'authentication_required',
      true,
    );
  } finally {
    setStationDeviceRefusalListener(null);
    setAuthFailureListener(null);
  }
});

// Answers the production `web` tier (nginx) generates itself: the same
// `{"detail": ...}` shape as the backend, plus a status fallback for a
// 413/429 whose body carries no usable detail (an HTML proxy page).

function html(status: number): Response {
  return new Response(
    `<html><body><h1>${status}</h1><hr><center>nginx</center></body></html>`,
    { status, headers: { 'Content-Type': 'text/html' } },
  );
}

async function failureOf(call: Promise<unknown>): Promise<ApiError> {
  const failure = await call.catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(ApiError);
  return failure as ApiError;
}

test('FC-1: a web 429 keeps its detail and flag and is a definite refusal', async () => {
  fetchMock.mockResolvedValue(
    json(
      {
        detail:
          'Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.',
        rate_limited: true,
      },
      429,
    ),
  );

  const failure = await failureOf(
    apiRequest('/api/session', {
      method: 'POST',
      body: { login: 'mai', password: 'x' },
    }),
  );

  expect(failure.status).toBe(429);
  expect(failure.message).toBe(
    'Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.',
  );
  expect(refusalFlag(failure, 'rate_limited')).toBe(true);
  expect(writeOutcomeUnknown(failure)).toBe(false);
});

test('FC-2: a 429 with an HTML body falls back to the rate-limit copy', async () => {
  fetchMock.mockResolvedValue(html(429));

  const failure = await failureOf(
    apiRequest('/api/session', { method: 'POST', body: {} }),
  );

  expect(failure.status).toBe(429);
  expect(failure.message).toBe(RATE_LIMITED_MESSAGE);
  expect(RATE_LIMITED_MESSAGE).toBe(
    'Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.',
  );
  expect(writeOutcomeUnknown(failure)).toBe(false);
});

test('FC-3: a 413 HTML body falls back to the too-large copy; a backend JSON 413 keeps its detail', async () => {
  fetchMock.mockResolvedValueOnce(html(413));
  const proxyRefusal = await failureOf(
    apiUpload('/api/users/4/avatar', new Blob(['x'], { type: 'image/png' })),
  );
  expect(proxyRefusal.status).toBe(413);
  expect(proxyRefusal.message).toBe(REQUEST_TOO_LARGE_MESSAGE);
  expect(REQUEST_TOO_LARGE_MESSAGE).toBe(
    'This request is too large for PartFlow. Nothing was changed.',
  );
  expect(writeOutcomeUnknown(proxyRefusal)).toBe(false);

  fetchMock.mockResolvedValueOnce(
    json(
      { detail: 'The image is larger than 2 MB. Choose a smaller image.' },
      413,
    ),
  );
  const appRefusal = await failureOf(
    apiUpload('/api/users/4/avatar', new Blob(['x'], { type: 'image/png' })),
  );
  expect(appRefusal.status).toBe(413);
  expect(appRefusal.message).toBe(
    'The image is larger than 2 MB. Choose a smaller image.',
  );
  expect(refusalFlag(appRefusal, 'request_too_large')).toBe(false);
  expect(writeOutcomeUnknown(appRefusal)).toBe(false);
});

test('FC-4: a 502 HTML body keeps the generic copy; web JSON 502 keeps its detail; both are unknown outcomes', async () => {
  fetchMock.mockResolvedValueOnce(html(502));
  const htmlFailure = await failureOf(
    apiRequest('/api/workers', { method: 'POST', body: {} }),
  );
  expect(htmlFailure.status).toBe(502);
  expect(htmlFailure.message).toBe('The request failed (HTTP 502).');
  expect(writeOutcomeUnknown(htmlFailure)).toBe(true);

  const detail =
    'The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.';
  fetchMock.mockResolvedValueOnce(
    json({ detail, server_unavailable: true }, 502),
  );
  const webFailure = await failureOf(
    apiRequest('/api/workers', { method: 'POST', body: {} }),
  );
  expect(webFailure.status).toBe(502);
  expect(webFailure.message).toBe(detail);
  expect(refusalFlag(webFailure, 'server_unavailable')).toBe(true);
  expect(writeOutcomeUnknown(webFailure)).toBe(true);
});

const RELEASE_MISMATCH_DETAIL =
  'PartFlow was updated while this page was open, so this request was refused and nothing was changed by it. Reload the page to continue. If an earlier attempt had no answer, check whether it was recorded before repeating it.';

test('FR-1: every request names the bundle release beside the request-origin header; callers never override either', async () => {
  expect(BUNDLE_RELEASE).toBe('development');
  fetchMock.mockImplementation(() => Promise.resolve(json({})));

  await apiRequest('/api/workers');
  await apiRequest('/api/workers', {
    method: 'POST',
    body: {},
    headers: { 'X-PartFlow-Release': 'v0.0.1', 'X-PartFlow-CSRF': '0' },
  });
  await apiUpload(
    '/api/workers/3/avatar',
    new Blob(['x'], { type: 'image/png' }),
    'PUT',
    { 'X-PartFlow-Release': 'v0.0.1' },
  );

  expect(fetchMock).toHaveBeenCalledTimes(3);
  for (const [, init] of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(init.headers).toMatchObject({
      'X-PartFlow-CSRF': '1',
      'X-PartFlow-Release': 'development',
    });
  }
});

test('FR-2: a 409 release_mismatch reaches the listener once and stays a definite refusal', async () => {
  const listener = vi.fn();
  setReleaseMismatchListener(listener);
  try {
    fetchMock.mockResolvedValueOnce(
      json({ detail: RELEASE_MISMATCH_DETAIL, release_mismatch: true }, 409),
    );
    const failure = await failureOf(
      apiRequest('/api/workers', { method: 'POST', body: {} }),
    );
    expect(listener).toHaveBeenCalledTimes(1);
    expect(failure.status).toBe(409);
    expect(failure.message).toBe(RELEASE_MISMATCH_DETAIL);
    expect(writeOutcomeUnknown(failure)).toBe(false);
    expect(isReleaseMismatch(failure)).toBe(true);

    // Neither a 409 without the flag nor a 503 is a release mismatch.
    expect(isReleaseMismatch(new ApiError(409, 'x', { detail: 'x' }))).toBe(
      false,
    );
    expect(
      isReleaseMismatch(new ApiError(503, 'x', { release_mismatch: true })),
    ).toBe(false);
    expect(isReleaseMismatch(new Error('x'))).toBe(false);
  } finally {
    setReleaseMismatchListener(null);
  }
});

test('FR-3: a release-flow 409 confirmation_required never reaches the release listener', async () => {
  const listener = vi.fn();
  setReleaseMismatchListener(listener);
  try {
    fetchMock.mockResolvedValueOnce(
      json(
        {
          detail: 'Confirm the release.',
          confirmation_required: true,
          existing_active_quantity: 3,
        },
        409,
      ),
    );
    const failure = await failureOf(
      apiRequest('/api/scan-stations/S1/releases', {
        method: 'POST',
        body: {},
      }),
    );
    expect(failure.status).toBe(409);
    expect(isReleaseMismatch(failure)).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  } finally {
    setReleaseMismatchListener(null);
  }
});

test('FR-4: a 503 not_ready stays an unknown outcome', async () => {
  fetchMock.mockResolvedValueOnce(
    json(
      {
        detail:
          'PartFlow is not ready for changes: the database does not match this release. Nothing was changed. An administrator must complete or roll back the release.',
        not_ready: true,
      },
      503,
    ),
  );
  const failure = await failureOf(
    apiRequest('/api/workers', { method: 'POST', body: {} }),
  );
  expect(failure.status).toBe(503);
  expect(isReleaseMismatch(failure)).toBe(false);
  expect(writeOutcomeUnknown(failure)).toBe(true);
});
