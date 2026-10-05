import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError, apiRequest, apiUpload } from './client';

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
  expect(init.headers).toEqual({ 'Content-Type': 'image/png' });
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
  expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
  expect(init.body).toBe('{"name":"Alex Tran","badge_barcode":"ABC1"}');

  // A body-less failure keeps the generic status message.
  fetchMock.mockResolvedValueOnce(new Response('', { status: 502 }));
  await expect(apiRequest('/api/workers')).rejects.toEqual(
    new ApiError(502, 'The request failed (HTTP 502).'),
  );
});
