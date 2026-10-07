import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { listWorkers } from './workers';

// The Worker badge barcode on the wire: sent only to users who may
// manage Workers (the key is absent for everyone else).

let fetchMock: ReturnType<typeof vi.fn>;

const WIRE = {
  id: 7,
  name: 'Alex Tran',
  badge_barcode: '100482',
  is_active: true,
  avatar_updated_at: null,
  created_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:00:00Z',
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('a present badge is mapped; an absent badge is null', async () => {
  const withheld: Record<string, unknown> = { ...WIRE, id: 8 };
  delete withheld.badge_barcode;
  fetchMock.mockResolvedValue(json([WIRE, withheld]));
  const [shown, hidden] = await listWorkers();
  expect(shown.badgeBarcode).toBe('100482');
  expect(hidden.badgeBarcode).toBeNull();
  expect(hidden.name).toBe('Alex Tran');
});

test('a badge that is not a string throws', async () => {
  fetchMock.mockResolvedValue(json([{ ...WIRE, badge_barcode: 100482 }]));
  await expect(listWorkers()).rejects.toThrow(
    'Unexpected badge barcode from the server.',
  );
  fetchMock.mockResolvedValue(json([{ ...WIRE, badge_barcode: null }]));
  await expect(listWorkers()).rejects.toThrow(
    'Unexpected badge barcode from the server.',
  );
});
