import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { getSignInPolicy, updateSignInPolicy } from './policies';

// The user sign-in policy: wire mapping and the partial PUT body (only
// the provided settings travel, so a stale editor never reverts another
// administrator's change).

let fetchMock: ReturnType<typeof vi.fn>;

const WIRE = {
  user_session_expires: true,
  user_session_days: 30,
  sign_in_lockout_attempts: 10,
  sign_in_lockout_minutes: 15,
  require_password_change: true,
  updated_at: '2026-10-06T08:00:00Z',
};

beforeEach(() => {
  fetchMock = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(WIRE), { status: 200 })),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('getSignInPolicy maps the five settings', async () => {
  expect(await getSignInPolicy()).toEqual({
    sessionExpires: true,
    sessionDays: 30,
    lockoutAttempts: 10,
    lockoutMinutes: 15,
    requirePasswordChange: true,
    updatedAt: '2026-10-06T08:00:00Z',
  });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/policies/sign-in');
});

test('updateSignInPolicy sends only the provided settings, in snake_case', async () => {
  await updateSignInPolicy({ sessionDays: 7 });
  await updateSignInPolicy({ sessionExpires: false });
  await updateSignInPolicy({
    lockoutAttempts: 3,
    lockoutMinutes: 60,
    requirePasswordChange: false,
  });
  const bodies = fetchMock.mock.calls.map(([path, init]) => {
    const request = init as RequestInit;
    expect(path).toBe('/api/policies/sign-in');
    expect(request.method).toBe('PUT');
    return JSON.parse(request.body as string) as unknown;
  });
  expect(bodies).toEqual([
    { user_session_days: 7 },
    { user_session_expires: false },
    {
      sign_in_lockout_attempts: 3,
      sign_in_lockout_minutes: 60,
      require_password_change: false,
    },
  ]);
});
