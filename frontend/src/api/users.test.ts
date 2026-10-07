import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { listUsers, setUserPassword } from './users';

// The users API additions of user sign-in: the optional sign-in state
// (sent to user administrators only) and the Set password request.

let fetchMock: ReturnType<typeof vi.fn>;

const WIRE = {
  id: 50,
  login_name: 'jdoe',
  display_name: 'Jane Doe',
  role_id: 2,
  role_name: 'Manager',
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

test('an absent sign-in state stays absent; a present one is mapped', async () => {
  fetchMock.mockResolvedValue(
    json([WIRE, { ...WIRE, id: 51, sign_in_state: 'LOCKED' }]),
  );
  const [plain, administered] = await listUsers();
  expect(plain.signInState).toBeUndefined();
  expect('signInState' in plain).toBe(false);
  expect(administered.signInState).toBe('LOCKED');
});

test('an unknown sign-in state throws', async () => {
  fetchMock.mockResolvedValue(json([{ ...WIRE, sign_in_state: 'MAYBE' }]));
  await expect(listUsers()).rejects.toThrow(
    'Unknown sign-in state from the server: MAYBE',
  );
});

test('setUserPassword PUTs exactly the new password', async () => {
  fetchMock.mockResolvedValue(
    json({ ...WIRE, sign_in_state: 'TEMPORARY_PASSWORD' }),
  );
  const user = await setUserPassword(50, 'correct horse battery');
  expect(user.signInState).toBe('TEMPORARY_PASSWORD');
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/users/50/password');
  expect(init.method).toBe('PUT');
  expect(JSON.parse(init.body as string)).toEqual({
    new_password: 'correct horse battery',
  });
});
