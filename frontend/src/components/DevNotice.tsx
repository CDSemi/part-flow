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
  // Two-part structure: the DEV tag and ONE content element. The
  // content is a single inline text flow (never per-fragment flex
  // items), so long copy wraps at natural word boundaries while the
  // notice itself fills the available width of its parent block.
  return (
    <p className="dev-notice" role="note">
      <span className="dev-notice-tag">DEV</span>
      <span className="dev-notice-content">{children}</span>
    </p>
  );
}
