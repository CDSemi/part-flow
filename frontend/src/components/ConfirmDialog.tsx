import { useId } from 'react';
import type { ReactNode } from 'react';

import { ModalDialog } from './ModalDialog';

/**
 * Small confirmation layer on top of ModalDialog: a visible heading
 * names the dialog (aria-labelledby), Cancel never changes anything,
 * and the confirming action is explicit. Presentation only — Phase 2
 * confirmations change local mock state at most.
 *
 * `tone` opts into the ATTENTION variant for final, permanent
 * decisions (adding / retiring / reactivating a Machine): a centered
 * icon badge and an emphasized title in the decision's semantic tone
 * interrupt reflexive confirmation without adding steps. `danger`
 * (color of the confirming action) is implied by `tone="danger"`.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel,
  danger = false,
  tone,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  /** Attention variant: `danger` for destructive finals, `warning`
   * for permanent-but-constructive finals. */
  tone?: 'danger' | 'warning';
  /** External gate on the confirming action (e.g. offline write-block)
   * — the caller decides the condition, this component only reflects
   * it. Cancel is never affected: closing/backing out always works. */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const headingId = useId();
  const destructive = danger || tone === 'danger';
  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={onCancel}
      className={`msgdlg${tone ? ` alertdlg tone-${tone}` : ''}`}
    >
      {tone ? (
        <span className="alertbadge" aria-hidden="true">
          !
        </span>
      ) : null}
      <h3 id={headingId}>{title}</h3>
      <div className="sub">{children}</div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          className={`bigbtn ${destructive ? 'danger' : 'primary'}`}
          disabled={confirmDisabled}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
}
