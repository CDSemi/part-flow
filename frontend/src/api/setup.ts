// First-run setup API (the `Set up PartFlow` dialog): available only
// while PartFlow has no administrator. Creating the first administrator
// needs the one-time setup token the server prints to its log; the
// server closes setup atomically with that creation and signs the new
// administrator in. The token is sent once and never stored here.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';
import { toSessionState } from './session';
import type { SessionState } from './session';

export interface SetupRole {
  id: number;
  name: string;
}

export interface SetupStatus {
  /** No administrator exists: the first one may be created. */
  open: boolean;
  /** The roles that may administer PartFlow; empty when closed. */
  eligibleRoles: SetupRole[];
}

function malformed(): Error {
  return new Error('The server answered a malformed setup status.');
}

function toSetupStatus(wire: unknown): SetupStatus {
  if (typeof wire !== 'object' || wire === null) throw malformed();
  const { open, eligible_roles } = wire as Record<string, unknown>;
  if (typeof open !== 'boolean' || !Array.isArray(eligible_roles)) {
    throw malformed();
  }
  return {
    open,
    eligibleRoles: eligible_roles.map((role: unknown) => {
      const { id, name } = (role ?? {}) as Record<string, unknown>;
      if (typeof id !== 'number' || typeof name !== 'string') {
        throw malformed();
      }
      return { id, name };
    }),
  };
}

export async function getSetupStatus(): Promise<SetupStatus> {
  return toSetupStatus(await apiRequest<unknown>('/api/setup'));
}

/** Create the first administrator; answered with the new sign-in. */
export async function createFirstAdministrator(input: {
  setupToken: string;
  loginName: string;
  displayName: string;
  roleId: number;
  password: string;
}): Promise<SessionState> {
  return toSessionState(
    await apiRequest<unknown>('/api/setup/administrator', {
      method: 'POST',
      body: {
        setup_token: input.setupToken,
        login_name: input.loginName,
        display_name: input.displayName,
        role_id: input.roleId,
        password: input.password,
      },
    }),
  );
}
