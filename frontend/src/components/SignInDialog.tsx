import './account-dialogs.css';

import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { signIn, signInWriteOutcomeUnknown } from '../api/session';
import type { SessionState } from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import { ModalDialog } from './ModalDialog';

const UNREACHABLE = 'The PartFlow server could not be reached. Try again.';

/**
 * User sign-in (login name + password) over the current view: success
 * closes the dialog and returns focus to the opener — no navigation.
 * Signing in is a write, so it is disabled while disconnected; nothing
 * is queued. A failed sign-in clears the password and keeps the login
 * name; an unanswered one re-reads the sign-in once (it may have
 * succeeded) and is never resent on its own.
 */
export function SignInDialog({
  notice,
  onCancel,
  onSignedIn,
  onRefresh,
}: {
  /** A note carried from the dialog that opened this one, if any. */
  notice: string | null;
  onCancel: () => void;
  onSignedIn: (state: SessionState) => void;
  /** Re-read the sign-in; null when it could not be read. */
  onRefresh: () => Promise<SessionState | null>;
}) {
  const { status } = useConnectivity();
  const offline = status !== 'connected';
  const [loginName, setLoginName] = useState('');
  const [password, setPassword] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Focused from an effect (after ModalDialog's own), never `autoFocus`:
  // ModalDialog records the opener only while focus is still outside.
  const loginField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    loginField.current?.focus();
  }, []);

  const loginMissing = loginName.trim() === '';
  const passwordMissing = password === '';

  // Cancel, Escape and the backdrop are ignored while signing in.
  const requestClose = () => {
    if (!busy) onCancel();
  };

  const submit = async () => {
    if (busy || offline) return;
    if (loginMissing || passwordMissing) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await signIn(loginName, password));
    } catch (failure) {
      // The password is entered again; the refusal says why.
      setPassword('');
      setAttempted(false);
      if (signInWriteOutcomeUnknown(failure)) {
        setError(UNREACHABLE);
        const after = await onRefresh();
        if (after?.user) {
          onSignedIn(after);
          return;
        }
      } else {
        setError(failure instanceof ApiError ? failure.message : UNREACHABLE);
      }
      setBusy(false);
    }
  };

  return (
    <ModalDialog label="Sign in" onClose={requestClose}>
      <h3>Sign in</h3>
      {notice ? (
        <p className="sub" role="status">
          {notice}
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
          <span>Login name</span>
          <input
            ref={loginField}
            className="field"
            value={loginName}
            onChange={(event) => setLoginName(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
          />
        </label>
        {attempted && loginMissing ? (
          <div className="err" role="alert">
            Enter your login name.
          </div>
        ) : null}
        <label>
          <span>Password</span>
          <input
            className="field"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        {attempted && passwordMissing ? (
          <div className="err" role="alert">
            Enter your password.
          </div>
        ) : null}
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        {offline ? (
          <p className="sub disabled-reason">
            Signing in needs the connection to the PartFlow server.
          </p>
        ) : null}
        <div className="row">
          <button
            type="button"
            className="bigbtn ghost"
            disabled={busy}
            onClick={requestClose}
          >
            Cancel (Esc)
          </button>
          <button
            type="submit"
            className="bigbtn primary"
            disabled={busy || offline}
          >
            Sign in
          </button>
        </div>
      </form>
    </ModalDialog>
  );
}
