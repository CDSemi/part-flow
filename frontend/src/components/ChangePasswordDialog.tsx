import './account-dialogs.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { changeOwnPassword, signInWriteOutcomeUnknown } from '../api/session';
import type { SessionState } from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import { newPasswordError } from '../app/password-rules';
import { ModalDialog } from './ModalDialog';

const UNKNOWN_OUTCOME =
  'The server did not answer — your password may or may not have been changed. If signing in with the new password fails, use the old one.';

/**
 * The signed-in user's own password change. Voluntary (from the account
 * menu) it can be cancelled; forced (an administrator set the password
 * and Settings requires a new one) it cannot — Escape and the backdrop
 * are ignored and the only other way out is signing out.
 *
 * An unanswered change is never resent: a committed change ended this
 * sign-in, so a resend would be refused or counted as a failed attempt.
 * The dialog clears the fields and re-reads the sign-in instead — signed
 * out means the change went through (sign in again); still signed in
 * means it did not, and the submit is available again. A re-read that
 * could not be answered is repeated once the connection is regained
 * (once per regained connection — never a polling loop).
 */
export function ChangePasswordDialog({
  forced,
  onCancel,
  onChanged,
  onSignOut,
  onRefresh,
  onEnded,
}: {
  forced: boolean;
  onCancel: () => void;
  onChanged: (state: SessionState) => void;
  onSignOut: () => Promise<void>;
  /** Re-read the sign-in; null when it could not be read. */
  onRefresh: () => Promise<SessionState | null>;
  /** The sign-in ended after an unanswered change: sign in again. */
  onEnded: (notice: string) => void;
}) {
  const { status } = useConnectivity();
  const offline = status !== 'connected';
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  // An unanswered change whose outcome the re-read has not settled.
  const [unsettled, setUnsettled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    currentField.current?.focus();
  }, []);

  // Settle an unanswered change by re-reading the sign-in: signed out
  // means it went through; still signed in means it did not.
  const settling = useRef(false);
  const settle = useCallback(async () => {
    settling.current = true;
    try {
      const after = await onRefresh();
      if (after && after.user === null) {
        onEnded(UNKNOWN_OUTCOME);
      } else if (after?.user) {
        setUnsettled(false);
      }
    } finally {
      settling.current = false;
    }
  }, [onRefresh, onEnded]);

  const wasConnected = useRef(!offline);
  useEffect(() => {
    const regained = !offline && !wasConnected.current;
    wasConnected.current = !offline;
    if (regained && unsettled && !settling.current) void settle();
  }, [offline, unsettled, settle]);

  const currentMissing = current === '';
  const nextError = newPasswordError(next, repeat);

  const requestClose = () => {
    if (!forced && !busy) onCancel();
  };

  const submit = async () => {
    if (busy || offline || unsettled) return;
    if (currentMissing || nextError) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onChanged(await changeOwnPassword(current, next));
    } catch (failure) {
      if (signInWriteOutcomeUnknown(failure)) {
        setCurrent('');
        setNext('');
        setRepeat('');
        setAttempted(false);
        setError(UNKNOWN_OUTCOME);
        setUnsettled(true);
        setBusy(false);
        await settle();
        return;
      }
      // A definite refusal: the current password is entered again.
      setCurrent('');
      setAttempted(false);
      setError(failure instanceof ApiError ? failure.message : UNKNOWN_OUTCOME);
      setBusy(false);
    }
  };

  const title = forced ? 'Choose a new password' : 'Change password';
  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      {forced ? (
        <p className="sub">
          An administrator set your password. Choose a new one to continue.
        </p>
      ) : null}
      <form
        className="acct-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          <span>Current password</span>
          <input
            ref={currentField}
            className="field"
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        {attempted && currentMissing ? (
          <div className="err" role="alert">
            Enter your current password.
          </div>
        ) : null}
        <label>
          <span>New password</span>
          <input
            className="field"
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        <p className="acct-help">At least 12 characters.</p>
        <label>
          <span>Repeat new password</span>
          <input
            className="field"
            type="password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        {attempted && nextError ? (
          <div className="err" role="alert">
            {nextError}
          </div>
        ) : null}
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        <div className="row">
          {forced ? (
            <button
              type="button"
              className="bigbtn ghost"
              disabled={busy || offline}
              onClick={() => void onSignOut()}
            >
              Sign out
            </button>
          ) : (
            <button
              type="button"
              className="bigbtn ghost"
              disabled={busy}
              onClick={requestClose}
            >
              Cancel (Esc)
            </button>
          )}
          <button
            type="submit"
            className="bigbtn primary"
            disabled={busy || offline || unsettled}
          >
            Change password
          </button>
        </div>
      </form>
    </ModalDialog>
  );
}
