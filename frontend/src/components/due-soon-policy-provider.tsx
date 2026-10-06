import type { ReactNode } from 'react';

import type { DueSoonPolicy } from '../views/dates';
import { DueSoonPolicyContext } from './due-soon-policy-context';

/**
 * Provide the Due Soon policy to the due consumers below. `policy` is
 * the loaded server policy, or `null` for "explicitly unavailable" —
 * the policy read failed. Only the Scan Station (and its DEV mock view)
 * may pass `null`; every other consumer mounts the provider only with a
 * loaded policy.
 */
export function DueSoonPolicyProvider({
  policy,
  children,
}: {
  policy: DueSoonPolicy | null;
  children: ReactNode;
}) {
  return (
    <DueSoonPolicyContext.Provider value={policy}>
      {children}
    </DueSoonPolicyContext.Provider>
  );
}
