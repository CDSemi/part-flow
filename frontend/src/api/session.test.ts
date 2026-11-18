import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ApiError, setAuthFailureListener } from './client';
import { PERMISSIONS } from './roles';
import {
  changeOwnEmail,
  changeOwnPassword,
  completeSecondStep,
  finishPasskeySignIn,
  getSession,
  getSignInOptions,
  getTwoStepStatus,
  isPasswordCheckBusy,
  saveOwnThemePreference,
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
  theme_preference: null,
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
      themePreference: null,
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

test('a Super User session decodes every key, the permanent deletion key included', async () => {
  // Migration 0042 grants the key to every role that holds every other
  // key: a client that did not know it would refuse the Super User's
  // whole sign-in state.
  fetchMock.mockResolvedValue(
    json({
      user: {
        ...WIRE_USER,
        permissions: [...PERMISSIONS],
      },
      setup_open: false,
    }),
  );
  const state = await getSession();
  expect(state.user?.permissions).toEqual([...PERMISSIONS]);
  expect(state.user?.permissions).toContain(
    'DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY',
  );
});

test('signIn, changeOwnPassword and signOut send the exact requests', async () => {
  fetchMock.mockResolvedValue(json({ user: WIRE_USER, setup_open: false }));
  const result = await signIn('JDoe', 'correct horse battery');
  expect(result.kind === 'signed-in' && result.state.user?.loginName).toBe(
    'jdoe',
  );
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

test('themePreference maps DARK, LIGHT and null; any other value or a missing key throws', async () => {
  for (const [wire, mapped] of [
    ['DARK', 'dark'],
    ['LIGHT', 'light'],
    [null, null],
  ] as const) {
    fetchMock.mockResolvedValueOnce(
      json({
        user: { ...WIRE_USER, theme_preference: wire },
        setup_open: false,
      }),
    );
    expect((await getSession()).user?.themePreference).toBe(mapped);
  }
  const missing: Record<string, unknown> = { ...WIRE_USER };
  delete missing.theme_preference;
  for (const user of [
    missing,
    { ...WIRE_USER, theme_preference: 'dark' },
    { ...WIRE_USER, theme_preference: 'AUTO' },
    { ...WIRE_USER, theme_preference: 1 },
  ]) {
    fetchMock.mockResolvedValueOnce(json({ user, setup_open: false }));
    await expect(getSession()).rejects.toThrow(
      'The server answered a malformed user sign-in state.',
    );
  }
});

test('saveOwnThemePreference sends the exact request without a sign-in prompt and maps the echo', async () => {
  fetchMock.mockResolvedValueOnce(json({ theme_preference: 'LIGHT' }));
  await expect(saveOwnThemePreference('light')).resolves.toBe('light');
  expect(sent()).toEqual({
    path: '/api/session/theme-preference',
    method: 'PUT',
    body: { theme_preference: 'LIGHT' },
  });
  const [, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  expect(init.headers).toEqual({
    'Content-Type': 'application/json',
    'X-PartFlow-CSRF': '1',
    'X-PartFlow-Release': 'development',
  });

  fetchMock.mockResolvedValueOnce(json({ theme_preference: 'DARK' }));
  await expect(saveOwnThemePreference('dark')).resolves.toBe('dark');
  expect(sent().body).toEqual({ theme_preference: 'DARK' });

  fetchMock.mockResolvedValueOnce(json({ theme_preference: 'BLUE' }));
  await expect(saveOwnThemePreference('dark')).rejects.toThrow(
    'The server answered a malformed user sign-in state.',
  );

  // An ended sign-in is reported to the session listener without a prompt.
  const listener = vi.fn();
  setAuthFailureListener(listener);
  try {
    fetchMock.mockResolvedValueOnce(
      json({ detail: 'Sign in.', authentication_required: true }, 401),
    );
    await expect(saveOwnThemePreference('light')).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(listener).toHaveBeenCalledExactlyOnceWith(
      'authentication_required',
      false,
    );
  } finally {
    setAuthFailureListener(null);
  }
});

test('changeOwnEmail sends the password and the address (null removes it) and maps the answer', async () => {
  fetchMock.mockResolvedValueOnce(json({ email: 'Jane@example.com' }));
  await expect(changeOwnEmail('secret', 'Jane@example.com')).resolves.toBe(
    'Jane@example.com',
  );
  expect(sent()).toEqual({
    path: '/api/session/email',
    method: 'PUT',
    body: { current_password: 'secret', email: 'Jane@example.com' },
  });
  fetchMock.mockResolvedValueOnce(json({ email: null }));
  await expect(changeOwnEmail('secret', null)).resolves.toBeNull();
  expect(sent().body).toEqual({ current_password: 'secret', email: null });
  fetchMock.mockResolvedValueOnce(json({ email: 7 }));
  await expect(changeOwnEmail('secret', null)).rejects.toThrow(
    'The server answered a malformed user sign-in state.',
  );
});

test('getTwoStepStatus maps the own address and refuses a masked-only answer', async () => {
  fetchMock.mockResolvedValueOnce(
    json({
      enabled: true,
      authenticator_app: false,
      passkeys: [
        {
          id: 3,
          name: 'Laptop',
          rp_id: 'pf.example.com',
          created_at: '2026-11-17T08:00:00Z',
          last_used_at: null,
        },
      ],
      recovery_codes_left: 9,
      email: 'jane@example.com',
      email_ready: false,
    }),
  );
  await expect(getTwoStepStatus()).resolves.toEqual({
    enabled: true,
    authenticatorApp: false,
    passkeys: [
      {
        id: 3,
        name: 'Laptop',
        rpId: 'pf.example.com',
        createdAt: '2026-11-17T08:00:00Z',
        lastUsedAt: null,
      },
    ],
    recoveryCodesLeft: 9,
    email: 'jane@example.com',
    emailReady: false,
  });
  fetchMock.mockResolvedValueOnce(
    json({
      enabled: true,
      authenticator_app: true,
      passkeys: [],
      recovery_codes_left: 9,
      email_hint: 'j•••@example.com',
      email_ready: false,
    }),
  );
  await expect(getTwoStepStatus()).rejects.toThrow(
    'The server answered a malformed user sign-in state.',
  );
});

test('getSignInOptions maps Remember me and the password rules; a malformed answer throws', async () => {
  const rules = {
    min_length: 8,
    max_length: 64,
    require_digit: true,
    require_special: false,
    require_uppercase: true,
    require_lowercase: false,
  };
  fetchMock.mockResolvedValue(
    json({ remember_me: { days: null }, password_rules: rules }),
  );
  expect(await getSignInOptions()).toEqual({
    rememberMe: { days: null },
    passwordRules: {
      minLength: 8,
      maxLength: 64,
      requireDigit: true,
      requireSpecial: false,
      requireUppercase: true,
      requireLowercase: false,
    },
  });
  expect(sent()).toEqual({
    path: '/api/session/sign-in-options',
    method: 'GET',
    body: undefined,
  });
  fetchMock.mockResolvedValue(
    json({ remember_me: null, password_rules: rules }),
  );
  expect((await getSignInOptions()).rememberMe).toBeNull();
  fetchMock.mockResolvedValue(
    json({ remember_me: { days: '30' }, password_rules: rules }),
  );
  await expect(getSignInOptions()).rejects.toThrow(
    'The server answered a malformed user sign-in state.',
  );
  fetchMock.mockResolvedValue(
    json({ remember_me: null, password_rules: { ...rules, min_length: '8' } }),
  );
  await expect(getSignInOptions()).rejects.toThrow(
    'The server answered a malformed user sign-in state.',
  );
});

test('Remember me travels only when chosen, with whichever request finishes the sign-in', async () => {
  const state = { user: WIRE_USER, setup_open: false };
  fetchMock.mockImplementation(() => Promise.resolve(json(state)));
  await signIn('jdoe', 'secret-password', true);
  expect(sent().body).toEqual({
    login_name: 'jdoe',
    password: 'secret-password',
    remember_me: true,
  });
  await signIn('jdoe', 'secret-password');
  expect(sent().body).toEqual({
    login_name: 'jdoe',
    password: 'secret-password',
  });
  await completeSecondStep('TOTP', '123456', true);
  expect(sent()).toEqual({
    path: '/api/session/second-step',
    method: 'POST',
    body: { method: 'TOTP', code: '123456', remember_me: true },
  });
  const credential = {
    id: 'Y3JlZC0x',
    rawId: 'Y3JlZC0x',
    type: 'public-key',
    response: {},
    clientExtensionResults: {},
  };
  await finishPasskeySignIn(
    credential as unknown as Parameters<typeof finishPasskeySignIn>[0],
    true,
  );
  expect(sent()).toEqual({
    path: '/api/session/passkey',
    method: 'POST',
    body: { credential, remember_me: true },
  });
});
