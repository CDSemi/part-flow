// The shared Due Soon policy of a view subtree (GUI_DESIGN §3.12).
//
// Every due countdown derives its `soon` judgement from the server's
// Due Soon warning policy (Administration → Settings). A view loads the
// policy as part of its ready state and mounts `DueSoonPolicyProvider`
// (`due-soon-policy-provider.tsx`) around its due consumers; there is
// NO default policy — a consumer outside a provider fails loudly. Only
// the Scan Station (and its DEV mock view) may state the policy as
// explicitly unavailable (`null`): it never gates a production action
// on a display policy, and withholds only the `soon` tone instead.
//
// Production-safe: no mock data.

import { createContext, useContext } from 'react';

import type { DueSoonPolicy } from '../views/dates';

/** `undefined`: no provider; `null`: the provider states the policy is
 * unavailable (its read failed). */
export const DueSoonPolicyContext = createContext<
  DueSoonPolicy | null | undefined
>(undefined);

/** The loaded server policy. Throws when no provider is mounted or the
 * provider holds `null` — there is no default. */
export function useDueSoonPolicy(): DueSoonPolicy {
  const policy = useContext(DueSoonPolicyContext);
  if (policy === undefined || policy === null) {
    throw new Error(
      'useDueSoonPolicy needs a DueSoonPolicyProvider with the loaded Due Soon policy',
    );
  }
  return policy;
}

/** The policy, or `null` when the provider states it is unavailable.
 * Throws when no provider is mounted. Used only by the shared
 * `CardDueStatus` (Area Board + Scan Station). */
export function useDueSoonPolicyIfLoaded(): DueSoonPolicy | null {
  const policy = useContext(DueSoonPolicyContext);
  if (policy === undefined) {
    throw new Error('useDueSoonPolicyIfLoaded needs a DueSoonPolicyProvider');
  }
  return policy;
}
