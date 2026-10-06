import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  deletePartNumber,
  listPartNumberPage,
  partNumberImageUrl,
  partNumberSecondaryLine,
  removePartNumberImage,
  resolvePartNumber,
  updatePartNumber,
  uploadPartNumberImage,
} from './part-numbers';

// The Part Number API module: the PN travels only in query parameters,
// always encoded — an unencoded `+` would arrive as a space and `&` /
// `#` / `%` would break the query.

const WIRE = {
  part_number: 'A+B',
  barcode_value: 'PF:PN:A+B',
  name: null,
  current_revision: null,
  erp_id: null,
  image_updated_at: null,
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-10-01T08:00:00+00:00',
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(
    async (_input: RequestInfo | URL, init?: RequestInit) =>
      new Response(
        init?.method === 'DELETE' && !String(_input).includes('/image')
          ? null
          : JSON.stringify(init?.method ? WIRE : [WIRE]),
        {
          status:
            init?.method === 'DELETE' && !String(_input).includes('/image')
              ? 204
              : 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const requestedUrls = () =>
  fetchMock.mock.calls.map(([input]) => String(input));

test('every PN is encoded into the number query parameter', async () => {
  await resolvePartNumber('A+B');
  await updatePartNumber('A+B', { name: 'X' });
  await deletePartNumber('A+B');
  await uploadPartNumberImage('A+B', new Blob(['x'], { type: 'image/png' }));
  await removePartNumberImage('A+B');

  expect(requestedUrls()).toEqual([
    '/api/part-numbers?number=A%2BB',
    '/api/part-numbers?number=A%2BB',
    '/api/part-numbers?number=A%2BB',
    '/api/part-numbers/image?number=A%2BB',
    '/api/part-numbers/image?number=A%2BB',
  ]);
  expect(
    partNumberImageUrl({
      partNumber: 'A+B',
      imageUpdatedAt: '2026-10-05T10:00:00+00:00',
    }),
  ).toBe(
    '/api/part-numbers/image?number=A%2BB&v=2026-10-05T10%3A00%3A00%2B00%3A00',
  );
  expect(
    partNumberImageUrl({ partNumber: 'A+B', imageUpdatedAt: null }),
  ).toBeNull();
});

test('the page search is encoded and the PATCH sends only the given keys', async () => {
  fetchMock.mockImplementationOnce(
    async () =>
      new Response(
        JSON.stringify({
          rows: [WIRE],
          total: 1,
          offset: 0,
          limit: 100,
          has_more: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
  );
  const page = await listPartNumberPage(' 50% & #7 ', 100);
  expect(requestedUrls()[0]).toBe(
    '/api/part-numbers/page?search=50%25%20%26%20%237&limit=100',
  );
  expect(page).toEqual({
    rows: [
      {
        partNumber: 'A+B',
        barcodeValue: 'PF:PN:A+B',
        name: null,
        currentRevision: null,
        erpId: null,
        imageUpdatedAt: null,
      },
    ],
    total: 1,
    offset: 0,
    limit: 100,
    hasMore: false,
  });

  await updatePartNumber('Q?1', { currentRevision: null });
  const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(url).toBe('/api/part-numbers?number=Q%3F1');
  expect(init.method).toBe('PATCH');
  expect(JSON.parse(String(init.body))).toEqual({ current_revision: null });
});

test('the secondary line joins the saved name and revision', () => {
  expect(partNumberSecondaryLine('BRACKET', 'C')).toBe('BRACKET · rev C');
  expect(partNumberSecondaryLine('BRACKET', null)).toBe('BRACKET');
  expect(partNumberSecondaryLine(null, 'C')).toBe('rev C');
  expect(partNumberSecondaryLine(null, null)).toBeUndefined();
});
