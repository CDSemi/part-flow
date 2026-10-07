import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  ApiError,
  apiRequest,
  apiUpload,
  setAuthFailureListener,
  setStationDeviceRefusalListener,
} from './client';

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
  expect(init.headers).toEqual({ 'X-PartFlow-CSRF': '1' });
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
    expect(listener).toHaveBeenLastCalledWith('authentication_required');

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
    expect(listener).toHaveBeenLastCalledWith('password_change_required');

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
    'X-PartFlow-Station-Device': 'tok-1',
  });
  expect(Object.keys(init.headers as object)).toEqual([
    'Content-Type',
    'X-PartFlow-CSRF',
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
    expect(authListener).toHaveBeenLastCalledWith('authentication_required');
  } finally {
    setStationDeviceRefusalListener(null);
    setAuthFailureListener(null);
  }
});
