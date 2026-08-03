import type { ReactNode } from 'react';

/**
 * The single development-only notice a mock view may show. It renders
 * only in development builds (`import.meta.env.DEV` — production
 * bundles compile it away together with the mock views) and gives one
 * concise boundary statement per surface, so normal operator copy,
 * toasts and dialogs never repeat mock/persistence explanations.
 */
export function DevNotice({ children }: { children: ReactNode }) {
  if (!import.meta.env.DEV) return null;
  return (
    <p className="dev-notice" role="note">
      <span className="dev-notice-tag">DEV</span> {children}
    </p>
  );
}
