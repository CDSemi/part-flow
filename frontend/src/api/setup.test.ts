import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { createFirstAdministrator, getSetupStatus } from './setup';

// The first-run setup API: the status mapping and the exact creation
// body, answered with the new administrator's sign-in.

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

test('getSetupStatus maps the open state and the eligible roles', async () => {
  fetchMock.mockResolvedValue(
    json({ open: true, eligible_roles: [{ id: 1, name: 'Administrator' }] }),
  );
  expect(await getSetupStatus()).toEqual({
    open: true,
    eligibleRoles: [{ id: 1, name: 'Administrator' }],
  });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/setup');

  fetchMock.mockResolvedValue(json({ open: false, eligible_roles: [] }));
  expect(await getSetupStatus()).toEqual({ open: false, eligibleRoles: [] });

  fetchMock.mockResolvedValue(json({ open: 'yes', eligible_roles: [] }));
  await expect(getSetupStatus()).rejects.toThrow(
    'The server answered a malformed setup status.',
  );
});

test('createFirstAdministrator posts the exact body and maps the sign-in', async () => {
  fetchMock.mockResolvedValue(
    json(
      {
        user: {
          id: 1,
          login_name: 'admin',
          display_name: 'Ada Admin',
          role_id: 1,
          role_name: 'Administrator',
          avatar_updated_at: null,
          permissions: ['MANAGE_USERS_AND_ROLES'],
          must_change_password: false,
          session_expires_at: null,
          theme_preference: null,
        },
        setup_open: false,
      },
      201,
    ),
  );
  const state = await createFirstAdministrator({
    setupToken: 'ABCD-EFGH',
    loginName: 'admin',
    displayName: 'Ada Admin',
    roleId: 1,
    password: 'correct horse battery',
  });
  expect(state.user?.displayName).toBe('Ada Admin');
  expect(state.setupOpen).toBe(false);
  const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(path).toBe('/api/setup/administrator');
  expect(init.method).toBe('POST');
  expect(JSON.parse(init.body as string)).toEqual({
    setup_token: 'ABCD-EFGH',
    login_name: 'admin',
    display_name: 'Ada Admin',
    role_id: 1,
    password: 'correct horse battery',
  });
});
