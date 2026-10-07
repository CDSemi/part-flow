// Users API (Administration → Users): application accounts for
// Management, Administration and the other non-Scan-Station views —
// name, login name, one role, optional avatar and active status. Users
// are never Workers (the Scan Station audit identity) and are
// deactivated, never deleted.
//
// Configuration only: sign-in and permission checks are not available
// yet, and no credential exists. The stored User theme preference has
// no reader or writer here.
//
// Wire responses are the backend's snake_case schema; this module maps
// them to the camelCase application type. The server canonicalizes the
// login name (trim, lowercase) and stays authoritative over every rule.
// The avatar travels as a raw image body on its own endpoint and is
// displayed through a cache-versioned URL; no response carries bytes.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, apiUpload } from './client';

export interface User {
  id: number;
  /** Stored canonical login name (trimmed, lowercase). */
  loginName: string;
  displayName: string;
  roleId: number;
  roleName: string;
  isActive: boolean;
  /** Avatar cache version (ISO 8601); null when there is no avatar. */
  avatarUpdatedAt: string | null;
}

interface UserWire {
  id: number;
  login_name: string;
  display_name: string;
  role_id: number;
  role_name: string;
  is_active: boolean;
  avatar_updated_at: string | null;
  created_at: string;
  updated_at: string;
}

function toUser(wire: UserWire): User {
  return {
    id: wire.id,
    loginName: wire.login_name,
    displayName: wire.display_name,
    roleId: wire.role_id,
    roleName: wire.role_name,
    isActive: wire.is_active,
    avatarUpdatedAt: wire.avatar_updated_at,
  };
}

/** Every User, active and inactive, ordered by name. */
export async function listUsers(): Promise<User[]> {
  const wires = await apiRequest<UserWire[]>('/api/users');
  return wires.map(toUser);
}

export async function createUser(input: {
  loginName: string;
  displayName: string;
  roleId: number;
}): Promise<User> {
  const wire = await apiRequest<UserWire>('/api/users', {
    method: 'POST',
    body: {
      login_name: input.loginName,
      display_name: input.displayName,
      role_id: input.roleId,
    },
  });
  return toUser(wire);
}

/** Send only the provided profile fields; the server answers a no-op
 * with the unchanged record. */
export async function updateUser(
  id: number,
  patch: {
    loginName?: string;
    displayName?: string;
    roleId?: number;
    isActive?: boolean;
  },
): Promise<User> {
  const wire = await apiRequest<UserWire>(`/api/users/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.loginName !== undefined ? { login_name: patch.loginName } : {}),
      ...(patch.displayName !== undefined
        ? { display_name: patch.displayName }
        : {}),
      ...(patch.roleId !== undefined ? { role_id: patch.roleId } : {}),
      ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
    },
  });
  return toUser(wire);
}

/** Replace the avatar with one PNG, JPEG or WebP image (raw body). */
export async function uploadUserAvatar(id: number, image: Blob): Promise<User> {
  const wire = await apiUpload<UserWire>(`/api/users/${id}/avatar`, image);
  return toUser(wire);
}

/** Remove the avatar; answered with the record (also when none existed). */
export async function removeUserAvatar(id: number): Promise<User> {
  const wire = await apiRequest<UserWire>(`/api/users/${id}/avatar`, {
    method: 'DELETE',
  });
  return toUser(wire);
}

/**
 * Display URL of a User's avatar, or null when there is none. The `v`
 * parameter carries the avatar version so a replaced image is never
 * served from a stale cache (the server ignores it).
 */
export function userAvatarUrl(
  user: Pick<User, 'id' | 'avatarUpdatedAt'>,
): string | null {
  if (user.avatarUpdatedAt === null) return null;
  return `/api/users/${user.id}/avatar?v=${encodeURIComponent(
    user.avatarUpdatedAt,
  )}`;
}
