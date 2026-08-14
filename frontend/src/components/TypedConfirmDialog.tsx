import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { ModalDialog } from './ModalDialog';

/**
 * Normalized comparison for typed confirmations: exact-value recall is
 * not the point — deliberate acknowledgement is. Trimmed and
 * case-insensitive so shop-floor tablets are not punished for casing.
 */
function matches(expected: string, entered: string): boolean {
  return entered.trim().toLowerCase() === expected.trim().toLowerCase();
}

/**
 * Destructive confirmation gated by typing an identifier. Used where a
 * one-click confirm is not enough (Machine retirement, Planned Route
 * archiving): the warning lists the consequences, and the confirming
 * button stays disabled until the expected token is typed. The token is
 * shown in the prompt — this is a deliberate-acknowledgement gate, not
 * a memory test. Presentation only in Phase 2 (mock state at most).
 */
export function TypedConfirmDialog({
  title,
  children,
  expectedValue,
  valueLabel,
  confirmLabel,
  danger = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  /** Warning body — states exactly what the action affects. */
  children: ReactNode;
  /** Identifier that must be typed to enable the confirming action. */
  expectedValue: string;
  /** Human name of the identifier, e.g. "Asset Tag" or "route name". */
  valueLabel: string;
  confirmLabel: string;
  /** Danger-tone the WHOLE dialog (error border, tinted surface, error
   * heading) so the destructive context never reads as routine —
   * Machine retirement uses this. */
  danger?: boolean;
  /** External gate on the confirming action (e.g. offline write-block)
   * — applies in addition to the typed-match gate. The caller decides
   * the condition; Cancel is never affected. */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const headingId = useId();
  const fieldId = useId();
  const [entered, setEntered] = useState('');
  const confirmed = matches(expectedValue, entered);
  // The typed gate IS the dialog's task — initial focus goes straight
  // to the input. This effect runs after ModalDialog's own mount
  // effect (child effects fire first), so the opener capture for focus
  // restoration on close stays intact.
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={onCancel}
      className={`msgdlg${danger ? ' dangerdlg' : ''}`}
    >
      <h3 id={headingId}>{title}</h3>
      <div className="sub">{children}</div>
      <div className="typedconfirm">
        <label htmlFor={fieldId}>
          Type <b>{expectedValue}</b>{' '}
          <span className="field-note">({valueLabel})</span> to confirm
        </label>
        <input
          id={fieldId}
          ref={inputRef}
          type="text"
          value={entered}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setEntered(event.target.value)}
          placeholder={expectedValue}
        />
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn danger"
          disabled={!confirmed || confirmDisabled}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
}
