// User sign-in API (the account chip and its dialogs): the current
// user sign-in of this browser, signing in and out, and changing the
// signed-in user's own password. Users are application accounts, never
// Workers — the Scan Station badge sign-in is a different thing with
// its own API.
//
// The sign-in itself travels as an HttpOnly cookie the browser holds;
// no response body carries a token or a credential, so this module only
// maps the wire shape. A malformed answer throws (the session UI then
// treats the state as unknown) and an unknown permission key throws
// rather than being dropped. The server stays authoritative over every
// rule; the permission list here only decides what the UI shows.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest } from './client';
import { PERMISSIONS } from './roles';
import type { Permission } from './roles';
import { writeOutcomeUnknown } from './scan-station';

export interface SessionUser {
  id: number;
  loginName: string;
  displayName: string;
  roleId: number;
  roleName: string;
  /** Avatar cache version (ISO 8601); null when there is no avatar. */
  avatarUpdatedAt: string | null;
  /** The role's permission keys, sorted by key. */
  permissions: Permission[];
  /** An administrator set the password: a new one must be chosen first. */
  mustChangePassword: boolean;
  /** When this sign-in ends (ISO 8601); null = it never expires. */
  sessionExpiresAt: string | null;
}

export interface SessionState {
  /** The signed-in user; null when nobody is signed in here. */
  user: SessionUser | null;
  /** PartFlow has no administrator yet (first-run setup is available). */
  setupOpen: boolean;
}

const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(PERMISSIONS);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function malformed(): Error {
  return new Error('The server answered a malformed user sign-in state.');
}

function toSessionUser(wire: unknown): SessionUser {
  if (!isRecord(wire)) throw malformed();
  const {
    id,
    login_name,
    display_name,
    role_id,
    role_name,
    avatar_updated_at,
    permissions,
    must_change_password,
    session_expires_at,
  } = wire;
  if (
    typeof id !== 'number' ||
    typeof login_name !== 'string' ||
    typeof display_name !== 'string' ||
    typeof role_id !== 'number' ||
    typeof role_name !== 'string' ||
    (avatar_updated_at !== null && typeof avatar_updated_at !== 'string') ||
    !Array.isArray(permissions) ||
    typeof must_change_password !== 'boolean' ||
    (session_expires_at !== null && typeof session_expires_at !== 'string')
  ) {
    throw malformed();
  }
  return {
    id,
    loginName: login_name,
    displayName: display_name,
    roleId: role_id,
    roleName: role_name,
    avatarUpdatedAt: avatar_updated_at,
    permissions: permissions.map((key: unknown) => {
      // The server sends only known keys; anything else means this
      // client is out of date — fail loudly rather than drop a grant.
      if (typeof key !== 'string' || !KNOWN_PERMISSIONS.has(key)) {
        throw new Error(
          `Unknown permission key from the server: ${String(key)}`,
        );
      }
      return key as Permission;
    }),
    mustChangePassword: must_change_password,
    sessionExpiresAt: session_expires_at,
  };
}

/** Map one `SessionStateResponse`; throws on a malformed body. */
export function toSessionState(wire: unknown): SessionState {
  if (!isRecord(wire) || typeof wire.setup_open !== 'boolean') {
    throw malformed();
  }
  if (wire.user !== null && !isRecord(wire.user)) throw malformed();
  return {
    user: wire.user === null ? null : toSessionUser(wire.user),
    setupOpen: wire.setup_open,
  };
}

const SESSION_PATH = '/api/session';

/** The user sign-in of this browser (also renews the cookie lifetime). */
export async function getSession(): Promise<SessionState> {
  return toSessionState(await apiRequest<unknown>(SESSION_PATH));
}

/** Sign in; a new sign-in replaces any earlier one of this browser. */
export async function signIn(
  loginName: string,
  password: string,
): Promise<SessionState> {
  return toSessionState(
    await apiRequest<unknown>(SESSION_PATH, {
      method: 'POST',
      body: { login_name: loginName, password },
    }),
  );
}

/** Sign out; answered the same when nobody was signed in. */
export async function signOut(): Promise<void> {
  await apiRequest<undefined>(SESSION_PATH, { method: 'DELETE' });
}

/** Change the signed-in user's own password; every other sign-in of the
 * user ends and this browser receives a new one. */
export async function changeOwnPassword(
  currentPassword: string,
  newPassword: string,
): Promise<SessionState> {
  return toSessionState(
    await apiRequest<unknown>(`${SESSION_PATH}/password`, {
      method: 'PUT',
      body: { current_password: currentPassword, new_password: newPassword },
    }),
  );
}

/**
 * The server was too busy checking other passwords: a DEFINITE refusal
 * (nothing was written or counted), even though it is a 5xx answer.
 */
export function isPasswordCheckBusy(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 503) return false;
  return (
    isRecord(error.body) &&
    (error.body as { password_check_busy?: unknown }).password_check_busy ===
      true
  );
}

/**
 * The outcome rule of every user sign-in write (sign in, setup,
 * password changes, sign-in settings): the repository's unknown-outcome
 * rule, except that a busy password check is a definite refusal.
 */
export function signInWriteOutcomeUnknown(error: unknown): boolean {
  return !isPasswordCheckBusy(error) && writeOutcomeUnknown(error);
}
