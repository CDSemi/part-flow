import { useId } from 'react';
import type { ReactNode } from 'react';

import { ModalDialog } from './ModalDialog';

/**
 * Three-way decision shown when a destructive workflow (Retire,
 * Archive…) is started while the surrounding form still has unsaved
 * edits. The pending edits are never saved implicitly: the user
 * explicitly chooses Save or Discard first, or cancels and stays in
 * the form. Save is unavailable while the form fails validation —
 * silently saving an invalid form would be worse than blocking.
 */
export function UnsavedChoiceDialog({
  title,
  children,
  saveLabel,
  discardLabel,
  saveDisabledReason,
  saveDisabled = false,
  discardDisabled = false,
  onSave,
  onDiscard,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  saveLabel: string;
  discardLabel: string;
  /** When set, Save is disabled and this reason is shown. */
  saveDisabledReason?: string;
  /** External gate on Save (e.g. offline write-block) — applies in
   * addition to `saveDisabledReason`, without showing a reason of its
   * own. The caller decides the condition; Cancel (which never persists
   * anything) is never affected. */
  saveDisabled?: boolean;
  /** External gate on Discard — most callers' Discard only abandons
   * local edits and stays available offline, but a few (e.g. Duplicate)
   * use Discard to mean "proceed using the last-saved data", which
   * itself writes; those callers pass this. */
  discardDisabled?: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const headingId = useId();
  return (
    <ModalDialog labelledBy={headingId} onClose={onCancel} className="msgdlg">
      <h3 id={headingId}>{title}</h3>
      <div className="sub">{children}</div>
      {saveDisabledReason ? (
        <div className="sub disabled-reason">{saveDisabledReason}</div>
      ) : null}
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn ghost"
          disabled={discardDisabled}
          onClick={onDiscard}
        >
          {discardLabel}
        </button>
        <button
          className="bigbtn primary"
          disabled={saveDisabledReason !== undefined || saveDisabled}
          onClick={onSave}
        >
          {saveLabel}
        </button>
      </div>
    </ModalDialog>
  );
}
