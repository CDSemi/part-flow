import { useState } from 'react';

/**
 * Close guard of a data-entry dialog (GUI_DESIGN "Forms and dialogs" —
 * closing with unsaved input). Every close request — Escape, a backdrop
 * click, `Cancel (Esc)` — goes through `requestClose`:
 *
 * - entries that still equal what the dialog opened with (or the record
 *   the server last confirmed) close it at once, nothing asked;
 * - changed entries open the owner's decision first (`asking`): an
 *   existing record offers Save changes / Discard changes / Cancel
 *   (`UnsavedChangesDialog`), a new record or a one-time entry Keep
 *   editing / Discard input (`DiscardInputDialog`) — entered input is
 *   never lost silently;
 * - while a save is in flight (`busy`) requests are ignored: its write
 *   cannot be recalled, so the dialog stays open until it settles.
 *
 * `dirty` compares the entries with those starting values, never "was a
 * field touched": typing a value and restoring it closes without asking.
 */
export function useCloseGuard({
  dirty,
  busy = false,
  onClose,
}: {
  dirty: boolean;
  busy?: boolean;
  onClose: () => void;
}): {
  /** The unsaved-input decision is open. */
  asking: boolean;
  /** Escape, the backdrop and `Cancel (Esc)` all call this. */
  requestClose: () => void;
  /** Close the decision and return to the form, entries intact. */
  keepEditing: () => void;
} {
  const [asking, setAsking] = useState(false);
  const requestClose = () => {
    if (busy) return;
    if (dirty) {
      setAsking(true);
      return;
    }
    onClose();
  };
  return { asking, requestClose, keepEditing: () => setAsking(false) };
}
