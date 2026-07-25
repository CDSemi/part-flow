import { useId } from 'react';
import type { ReactNode } from 'react';

import { ModalDialog } from './ModalDialog';

/**
 * Small confirmation layer on top of ModalDialog: a visible heading
 * names the dialog (aria-labelledby), Cancel never changes anything,
 * and the confirming action is explicit. Presentation only — Phase 2
 * confirmations change local mock state at most.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel,
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const headingId = useId();
  return (
    <ModalDialog labelledBy={headingId} onClose={onCancel}>
      <h3 id={headingId}>{title}</h3>
      <div className="sub">{children}</div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          className={`bigbtn ${danger ? 'danger' : 'primary'}`}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
}
