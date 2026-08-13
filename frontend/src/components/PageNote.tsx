import type { ReactNode } from 'react';

/**
 * Page-level informational note — the ONE note presentation shared by
 * every view (Machines, Work Orders, Priority, …): the info tone
 * surface with a small NOTE tag leading a single inline content flow,
 * filling the available width of its parent block. Reference reading —
 * never a warning and never a development-only notice (DevNotice owns
 * that boundary).
 */
export function PageNote({ children }: { children: ReactNode }) {
  return (
    <p className="page-note" role="note">
      <span className="page-note-tag">Note</span>
      <span className="page-note-content">{children}</span>
    </p>
  );
}
