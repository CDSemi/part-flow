import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError } from './client';
import {
  changeOwnPassword,
  getSession,
  isPasswordCheckBusy,
  signIn,
  signInWriteOutcomeUnknown,
  signOut,
} from './session';

// The user sign-in API: snake_case wire ↔ camelCase state, exact request
// bodies, and a loud failure on a malformed answer or an unknown key.

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const WIRE_USER = {
  id: 7,
  login_name: 'jdoe',
  display_name: 'Jane Doe',
  role_id: 2,
  role_name: 'Manager',
  avatar_updated_at: null,
  permissions: ['MANAGE_USERS_AND_ROLES', 'VIEW_PRODUCTION_DATA'],
  must_change_password: true,
  session_expires_at: '2026-11-05T08:00:00Z',
};

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function sent(): { path: string; method: string; body: unknown } {
  const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return {
    path,
    method: init.method ?? 'GET',
    body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
  };
}

test('getSession maps the signed-in user and the setup state', async () => {
  fetchMock.mockResolvedValue(json({ user: WIRE_USER, setup_open: false }));
  expect(await getSession()).toEqual({
    user: {
      id: 7,
      loginName: 'jdoe',
      displayName: 'Jane Doe',
      roleId: 2,
      roleName: 'Manager',
      avatarUpdatedAt: null,
      permissions: ['MANAGE_USERS_AND_ROLES', 'VIEW_PRODUCTION_DATA'],
      mustChangePassword: true,
      sessionExpiresAt: '2026-11-05T08:00:00Z',
    },
    setupOpen: false,
  });
  expect(sent()).toEqual({
    path: '/api/session',
    method: 'GET',
    body: undefined,
  });

  fetchMock.mockResolvedValue(json({ user: null, setup_open: true }));
  expect(await getSession()).toEqual({ user: null, setupOpen: true });
});

test('signIn, changeOwnPassword and signOut send the exact requests', async () => {
  fetchMock.mockResolvedValue(json({ user: WIRE_USER, setup_open: false }));
  const state = await signIn('JDoe', 'correct horse battery');
  expect(state.user?.loginName).toBe('jdoe');
  expect(sent()).toEqual({
    path: '/api/session',
    method: 'POST',
    body: { login_name: 'JDoe', password: 'correct horse battery' },
  });

  fetchMock.mockResolvedValue(
    json({
      user: { ...WIRE_USER, must_change_password: false },
      setup_open: false,
    }),
  );
  const changed = await changeOwnPassword('old password 1', 'new password 12');
  expect(changed.user?.mustChangePassword).toBe(false);
  expect(sent()).toEqual({
    path: '/api/session/password',
    method: 'PUT',
    body: {
      current_password: 'old password 1',
      new_password: 'new password 12',
    },
  });

  fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
  await expect(signOut()).resolves.toBeUndefined();
  expect(sent()).toEqual({
    path: '/api/session',
    method: 'DELETE',
    body: undefined,
  });
});

test('a malformed answer or an unknown permission key throws', async () => {
  for (const body of [
    { user: null },
    { user: 'jdoe', setup_open: false },
    { user: { ...WIRE_USER, id: '7' }, setup_open: false },
    { user: { ...WIRE_USER, must_change_password: 'no' }, setup_open: false },
    [],
  ]) {
    fetchMock.mockResolvedValueOnce(json(body));
    await expect(getSession()).rejects.toThrow(
      'The server answered a malformed user sign-in state.',
    );
  }
  fetchMock.mockResolvedValueOnce(
    json({
      user: { ...WIRE_USER, permissions: ['FLY_TO_THE_MOON'] },
      setup_open: false,
    }),
  );
  await expect(getSession()).rejects.toThrow(
    'Unknown permission key from the server: FLY_TO_THE_MOON',
  );
});

test('a busy password check is a definite refusal; other 5xx and no answer are unknown', () => {
  const busy = new ApiError(503, 'PartFlow is busy checking other passwords.', {
    detail: 'PartFlow is busy checking other passwords.',
    password_check_busy: true,
  });
  expect(isPasswordCheckBusy(busy)).toBe(true);
  expect(signInWriteOutcomeUnknown(busy)).toBe(false);

  const plain503 = new ApiError(503, 'Unavailable.', { detail: 'x' });
  expect(isPasswordCheckBusy(plain503)).toBe(false);
  expect(signInWriteOutcomeUnknown(plain503)).toBe(true);
  expect(signInWriteOutcomeUnknown(new TypeError('Failed to fetch'))).toBe(
    true,
  );
  expect(
    signInWriteOutcomeUnknown(
      new ApiError(401, 'Sign-in failed.', { sign_in_failed: true }),
    ),
  ).toBe(false);
});
