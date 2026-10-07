import { createContext, useContext } from 'react';

import type { Permission } from '../api/roles';
import type { SessionUser } from '../api/session';

/**
 * The user sign-in of this browser: `unknown` until the server answered
 * (or while it cannot be reached), then signed out or signed in.
 */
export type SessionStatus = 'unknown' | 'signed-out' | 'signed-in';

export interface SessionValue {
  status: SessionStatus;
  /** The signed-in user; null unless `status` is `signed-in`. */
  user: SessionUser | null;
  /** PartFlow has no administrator yet (first-run setup is available). */
  setupOpen: boolean;
  /** The signed-in user's role holds the permission. Presentation only:
   * the server checks every permission itself. */
  can(permission: Permission): boolean;
  openSignIn(): void;
  openSetup(): void;
  openChangePassword(): void;
  signOut(): Promise<void>;
  /** Re-read the sign-in from the server. */
  refresh(): Promise<void>;
}

export const SessionContext = createContext<SessionValue | null>(null);

/** Whether `user`'s role holds `key` (no user holds nothing). */
export function hasPermission(
  user: SessionUser | null,
  key: Permission,
): boolean {
  return user !== null && user.permissions.includes(key);
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used within SessionProvider');
  return value;
}
