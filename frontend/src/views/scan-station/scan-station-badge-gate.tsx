import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

import { ModalDialog } from '../../components/ModalDialog';
import { normalizeScanInput } from './barcode';
import { DevBadgesSlot } from './scan-station-dev-badges-slot';
import { Guidance } from './scan-station-presentation';

/**
 * Badge-scan final confirmation of a sensitive action (GUI_DESIGN §4.6,
 * §4.12; PROJECT_PROFILE §16, §19): the last step of DONE, QUEUE return
 * or Undo when the server reports the BADGE form for the station. The
 * key facts of the pending action stay visible; ANY active Worker badge
 * confirms — the SERVER matches it, signs that Worker in and records it
 * on the action, all in the action's own transaction, or refuses with
 * nothing recorded. The scan itself is the confirmation: there is no
 * further question. Cancel returns to the summary (ignored while the
 * request is in flight); nothing is queued while disconnected.
 */
export function BadgeGateDialog({
  title,
  tone,
  facts,
  busy,
  writeBlocked,
  error,
  notice,
  onBadge,
  onCancel,
}: {
  title: string;
  /** `info` for DONE, `warning` for QUEUE return and Undo. */
  tone: 'info' | 'warning';
  /** The action's key facts (the question gate's text). */
  facts: ReactNode;
  busy: boolean;
  writeBlocked: boolean;
  /** The badge was not recognized — shown in place. */
  error: string | null;
  /** Why the gate switched to a badge (the server's refusal of the
   * question). An error replaces it: one notice at a time. */
  notice: string | null;
  onBadge: (badge: string) => void;
  onCancel: () => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);

  // The badge field owns focus whenever it can take a scan: on open,
  // after a refused badge, and on reconnection.
  useEffect(() => {
    if (!busy && !writeBlocked) fieldRef.current?.focus();
  }, [busy, writeBlocked, error]);

  function submit() {
    const field = fieldRef.current;
    if (!field || busy || writeBlocked) return;
    const value = normalizeScanInput(field.value);
    field.value = '';
    if (!value) return;
    onBadge(value);
  }

  // Development-only: a click on a demo badge is the exact equivalent
  // of a wedge badge scan ending in Enter — the SAME submit path.
  function simulate(value: string) {
    if (busy || writeBlocked || !fieldRef.current) return;
    fieldRef.current.value = value;
    submit();
  }

  return (
    <ModalDialog
      label={title}
      onClose={busy ? () => undefined : onCancel}
      className={`msgdlg alertdlg tone-${tone}`}
    >
      <span className="alertbadge" aria-hidden="true">
        {tone === 'info' ? 'i' : '!'}
      </span>
      <h3>{title}</h3>
      <div className="sub">{facts}</div>
      <Guidance tone="action">
        Scan a Worker badge to confirm — the badge identifies the confirming
        Worker and completes the action.
      </Guidance>
      <input
        aria-label="Scan Worker badge"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        disabled={writeBlocked || busy}
        placeholder={
          writeBlocked
            ? 'Disconnected — scanning disabled'
            : busy
              ? 'Recording…'
              : 'Scan Worker badge · Press Enter'
        }
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit();
        }}
      />
      {error ? (
        <Guidance tone="error">{error}</Guidance>
      ) : notice ? (
        <Guidance tone="warn">{notice}</Guidance>
      ) : null}
      <DevBadgesSlot onScan={simulate} disabled={busy || writeBlocked} />
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={onCancel}>
          Cancel (Esc)
        </button>
      </div>
    </ModalDialog>
  );
}
