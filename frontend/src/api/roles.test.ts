import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { listRoles, updateRole } from './roles';
import { updateUser } from './users';

// The roles and users API modules: PATCH bodies carry only the parts
// that were provided (snake_case; empty permission lists and absent
// fields are omitted, never sent as undefined), and a permission key
// this client does not know fails loudly.

const ROLE_WIRE = {
  id: 2,
  name: 'Manager',
  permissions: ['EXPORT_REPORTS', 'VIEW_PRODUCTION_DATA'],
  user_count: 1,
  applies_at_scan_stations: false,
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-10-01T08:00:00+00:00',
};

const USER_WIRE = {
  id: 5,
  login_name: 'jdoe',
  display_name: 'Jane Doe',
  role_id: 2,
  role_name: 'Manager',
  is_active: true,
  avatar_updated_at: null,
  created_at: '2026-10-01T08:00:00+00:00',
  updated_at: '2026-10-01T08:00:00+00:00',
};

let answer: unknown;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  answer = ROLE_WIRE;
  fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(answer), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const sentBodies = () =>
  fetchMock.mock.calls.map(([, init]) =>
    JSON.parse((init as RequestInit).body as string),
  );

test('updateRole sends only the provided parts and omits empty lists', async () => {
  await updateRole(2, { name: 'Production Manager' });
  await updateRole(2, {
    grantPermissions: ['MANAGE_MACHINES'],
    revokePermissions: [],
  });
  await updateRole(2, { revokePermissions: ['EXPORT_REPORTS'] });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/roles/2');
  expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('PATCH');
  expect(sentBodies()).toEqual([
    { name: 'Production Manager' },
    { grant_permissions: ['MANAGE_MACHINES'] },
    { revoke_permissions: ['EXPORT_REPORTS'] },
  ]);
});

test('a role converts to the application shape; an unknown key fails loudly', async () => {
  answer = [ROLE_WIRE];
  expect(await listRoles()).toEqual([
    {
      id: 2,
      name: 'Manager',
      permissions: ['EXPORT_REPORTS', 'VIEW_PRODUCTION_DATA'],
      userCount: 1,
      appliesAtScanStations: false,
    },
  ]);
  answer = [{ ...ROLE_WIRE, applies_at_scan_stations: true }];
  expect((await listRoles())[0].appliesAtScanStations).toBe(true);
  answer = [{ ...ROLE_WIRE, permissions: ['NOPE'] }];
  await expect(listRoles()).rejects.toThrow(/NOPE/);
  // The Scan Station flag is part of the contract: a role without it
  // means this client is out of date.
  const { applies_at_scan_stations: _omitted, ...withoutFlag } = ROLE_WIRE;
  void _omitted;
  answer = [withoutFlag];
  await expect(listRoles()).rejects.toThrow(/Malformed role/);
});

test('updateUser sends only the provided keys, in snake_case', async () => {
  answer = USER_WIRE;
  await updateUser(5, { roleId: 3, isActive: false });
  await updateUser(5, { displayName: 'Jane D.' });
  await updateUser(5, { loginName: 'jane', displayName: undefined });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/users/5');
  expect(sentBodies()).toEqual([
    { role_id: 3, is_active: false },
    { display_name: 'Jane D.' },
    { login_name: 'jane' },
  ]);
  const raw = (fetchMock.mock.calls[2][1] as RequestInit).body as string;
  expect(raw).not.toContain('display_name');
});
