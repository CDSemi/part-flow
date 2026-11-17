import './account-dialogs.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import {
  changeOwnEmail,
  getTwoStepStatus,
  signInWriteOutcomeUnknown,
} from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import {
  canonicalEmail,
  EMAIL_ADDRESS_ERROR,
} from '../views/administration/email-address';
import { useCloseGuard } from './close-guard';
import { DiscardInputDialog } from './close-guard-dialogs';
import { ModalDialog } from './ModalDialog';

const LOAD_FAILED = 'The PartFlow server could not be reached.';
const UNKNOWN_OUTCOME =
  'The server did not answer — your email address may or may not have been changed. Close this window and open it again to see the stored address.';

type Loaded =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; email: string | null };

/**
 * The signed-in user's own email address (Phase 17, owner decision of
 * 2026-11-17): where PartFlow emails a one-time sign-in code. Changing or
 * removing it takes the password again (a wrong one counts as a failed
 * sign-in); the previous address receives a notice when an outgoing mail
 * server is set up. Opened from the account menu; one dialog from loading
 * to the saved answer, so focus stays in it.
 */
export function EmailDialog({ onClose }: { onClose: () => void }) {
  const { status } = useConnectivity();
  const offline = status !== 'connected';
  const [loaded, setLoaded] = useState<Loaded>({ status: 'loading' });
  const [text, setText] = useState('');
  const [password, setPassword] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ email: string | null } | null>(null);
  const field = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoaded({ status: 'loading' });
    try {
      const state = await getTwoStepStatus();
      setText(state.email ?? '');
      setLoaded({ status: 'ready', email: state.email });
    } catch (failure) {
      setLoaded({
        status: 'error',
        message: failure instanceof ApiError ? failure.message : LOAD_FAILED,
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ready = loaded.status === 'ready';
  useEffect(() => {
    if (ready) field.current?.focus();
  }, [ready]);

  const stored = loaded.status === 'ready' ? loaded.email : null;
  const email = canonicalEmail(text);
  const unchanged = email === (stored ?? '');
  const guard = useCloseGuard({
    dirty:
      ready && saved === null && (text !== (stored ?? '') || password !== ''),
    busy,
    onClose,
  });

  const submit = async () => {
    if (busy || offline || !ready) return;
    if (email === null || password === '' || unchanged) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setSaved({
        email: await changeOwnEmail(password, email === '' ? null : email),
      });
    } catch (failure) {
      setPassword('');
      setAttempted(false);
      setError(
        signInWriteOutcomeUnknown(failure) || !(failure instanceof ApiError)
          ? UNKNOWN_OUTCOME
          : failure.message,
      );
    } finally {
      setBusy(false);
    }
  };

  const title = 'Email address';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      {loaded.status === 'loading' ? (
        <p
          className="sub"
          role="status"
          aria-label="Loading your email address"
        >
          Loading…
        </p>
      ) : null}
      {loaded.status === 'error' ? (
        <div className="acct-form">
          <div className="err" role="alert">
            {`Your email address could not be loaded. ${loaded.message}`}
          </div>
          <div className="row">
            <button type="button" className="bigbtn ghost" onClick={onClose}>
              Close
            </button>
            <button
              type="button"
              className="bigbtn primary"
              onClick={() => void load()}
            >
              Retry
            </button>
          </div>
        </div>
      ) : null}
      {ready && saved !== null ? (
        <div className="acct-form">
          <p className="sub" role="status">
            {saved.email === null
              ? 'Saved: your account has no email address now.'
              : `Saved: your email address is now ${saved.email}.`}
          </p>
          {stored !== null ? (
            <p className="acct-note">
              {`When an outgoing mail server is set up, PartFlow tells ${stored} about the change.`}
            </p>
          ) : null}
          <div className="row">
            <button type="button" className="bigbtn primary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      ) : null}
      {ready && saved === null ? (
        <form
          className="acct-form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <p className="sub">
            PartFlow emails a one-time sign-in code to this address when you
            sign in with two-step sign-in and ask for one. Leave it empty to
            remove it.
          </p>
          <label>
            <span>Email address</span>
            <input
              ref={field}
              className="field"
              type="email"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="e.g. name@example.com"
              autoComplete="email"
              spellCheck={false}
            />
          </label>
          {attempted && email === null ? (
            <div className="err" role="alert">
              {EMAIL_ADDRESS_ERROR}
            </div>
          ) : null}
          {attempted && email !== null && unchanged ? (
            <div className="err" role="alert">
              This is already your email address.
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
          {attempted && password === '' ? (
            <div className="err" role="alert">
              Enter your password.
            </div>
          ) : null}
          {error ? (
            <div className="err" role="alert">
              {error}
            </div>
          ) : null}
          <div className="row">
            <button
              type="button"
              className="bigbtn ghost"
              disabled={busy}
              onClick={guard.requestClose}
            >
              Cancel (Esc)
            </button>
            <button
              type="submit"
              className="bigbtn primary"
              disabled={busy || offline}
            >
              Save email address
            </button>
          </div>
        </form>
      ) : null}
      {/* Outside the form: its buttons must never submit it. */}
      {guard.asking ? (
        <DiscardInputDialog
          title="Discard email address change?"
          onKeepEditing={guard.keepEditing}
          onDiscard={onClose}
        />
      ) : null}
    </ModalDialog>
  );
}
