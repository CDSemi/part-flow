import './account-dialogs.css';

import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import {
  completeSecondStep,
  secondStepEnded,
  sendSignInCode,
  signIn,
  signInWriteOutcomeUnknown,
} from '../api/session';
import type {
  SecondStep,
  SecondStepMethod,
  SessionState,
} from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import { connectionReason } from '../app/release-copy';
import { ModalDialog } from './ModalDialog';

const UNREACHABLE = 'The PartFlow server could not be reached. Try again.';

const CODE_LABELS: Record<SecondStepMethod, string> = {
  TOTP: 'Code from your authenticator app',
  RECOVERY_CODE: 'Recovery code',
  EMAIL_CODE: 'Code from the email',
};

const CODE_MISSING: Record<SecondStepMethod, string> = {
  TOTP: 'Enter the 6-digit code your authenticator app shows.',
  RECOVERY_CODE: 'Enter one of your recovery codes.',
  EMAIL_CODE: 'Enter the 8-digit code from the email.',
};

/**
 * User sign-in (login name + password) over the current view: success
 * closes the dialog and returns focus to the opener — no navigation.
 * A user with two-step sign-in then takes a second step in the same
 * dialog: a code from the authenticator app, a recovery code, or a code
 * PartFlow emails (when the account has an address and an outgoing mail
 * server is set up). A second step that ended (expired, too many wrong
 * codes) returns to the password. Signing in is a write, so it is
 * disabled while disconnected; nothing is queued. A failed sign-in
 * clears the password (or the code) and keeps the login name; an
 * unanswered one re-reads the sign-in once (it may have succeeded) and is
 * never resent on its own.
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
  const [step, setStep] = useState<SecondStep | null>(null);

  // Focused from an effect (after ModalDialog's own), never `autoFocus`:
  // ModalDialog records the opener only while focus is still outside.
  const loginField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === null) loginField.current?.focus();
  }, [step]);

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
      const result = await signIn(loginName, password);
      if (result.kind === 'signed-in') {
        onSignedIn(result.state);
        return;
      }
      setPassword('');
      setAttempted(false);
      setStep(result.step);
      setBusy(false);
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
      {step !== null ? (
        <SecondStepForm
          step={step}
          offline={offline}
          busy={busy}
          setBusy={setBusy}
          onCancel={requestClose}
          onSignedIn={onSignedIn}
          onRefresh={onRefresh}
          onEnded={(message) => {
            setStep(null);
            setError(message);
          }}
        />
      ) : (
        <>
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
                {connectionReason(
                  status,
                  'Signing in needs the connection to the PartFlow server.',
                )}
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
        </>
      )}
    </ModalDialog>
  );
}

function SecondStepForm({
  step,
  offline,
  busy,
  setBusy,
  onCancel,
  onSignedIn,
  onRefresh,
  onEnded,
}: {
  step: SecondStep;
  offline: boolean;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onCancel: () => void;
  onSignedIn: (state: SessionState) => void;
  onRefresh: () => Promise<SessionState | null>;
  /** The second step is over: back to the password, with the reason. */
  onEnded: (message: string) => void;
}) {
  const [method, setMethod] = useState<SecondStepMethod>('TOTP');
  const [code, setCode] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentNote, setSentNote] = useState<string | null>(null);
  const [sendsLeft, setSendsLeft] = useState<number | null>(null);
  const canRecover = step.methods.includes('RECOVERY_CODE');
  const canEmail = step.methods.includes('EMAIL_CODE');

  const codeField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    codeField.current?.focus();
  }, [method]);

  const choose = (next: SecondStepMethod) => {
    setMethod(next);
    setCode('');
    setAttempted(false);
    setError(null);
  };

  const ended = (failure: unknown) =>
    onEnded(
      failure instanceof ApiError
        ? failure.message
        : 'This sign-in has ended. Enter your password again.',
    );

  const submit = async () => {
    if (busy || offline) return;
    if (code.trim() === '') {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await completeSecondStep(method, code));
    } catch (failure) {
      setCode('');
      setAttempted(false);
      if (secondStepEnded(failure)) {
        setBusy(false);
        ended(failure);
        return;
      }
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

  const emailMe = async () => {
    if (busy || offline) return;
    setBusy(true);
    setError(null);
    try {
      const sent = await sendSignInCode();
      choose('EMAIL_CODE');
      setSentNote(`PartFlow sent a code to ${sent.sentTo}.`);
      setSendsLeft(sent.sendsLeft);
    } catch (failure) {
      if (secondStepEnded(failure)) {
        setBusy(false);
        ended(failure);
        return;
      }
      setError(failure instanceof ApiError ? failure.message : UNREACHABLE);
    }
    setBusy(false);
  };

  return (
    <form
      className="acct-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="sub">
        Two-step sign-in is on for this account. Your password was correct.
      </p>
      {sentNote && method === 'EMAIL_CODE' ? (
        <p className="acct-note" role="status">
          {sentNote}
        </p>
      ) : null}
      <label>
        <span>{CODE_LABELS[method]}</span>
        <input
          ref={codeField}
          className="field mono"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          inputMode={method === 'RECOVERY_CODE' ? 'text' : 'numeric'}
          autoComplete="one-time-code"
          autoCapitalize="none"
          spellCheck={false}
        />
      </label>
      {attempted ? (
        <div className="err" role="alert">
          {CODE_MISSING[method]}
        </div>
      ) : null}
      {error ? (
        <div className="err" role="alert">
          {error}
        </div>
      ) : null}
      <div className="acct-alts">
        {method !== 'TOTP' ? (
          <button
            type="button"
            className="acct-alt"
            disabled={busy}
            onClick={() => choose('TOTP')}
          >
            Use your authenticator app
          </button>
        ) : null}
        {canRecover && method !== 'RECOVERY_CODE' ? (
          <button
            type="button"
            className="acct-alt"
            disabled={busy}
            onClick={() => choose('RECOVERY_CODE')}
          >
            Use a recovery code
          </button>
        ) : null}
        {canEmail && sendsLeft !== 0 ? (
          <button
            type="button"
            className="acct-alt"
            disabled={busy || offline}
            onClick={() => void emailMe()}
          >
            {sentNote === null
              ? `Email me a code at ${step.emailHint ?? 'my address'}`
              : 'Email me another code'}
          </button>
        ) : null}
      </div>
      <div className="row">
        <button
          type="button"
          className="bigbtn ghost"
          disabled={busy}
          onClick={onCancel}
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
  );
}
