import './account-dialogs.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import {
  beginTwoStepSetup,
  confirmTwoStepSetup,
  createRecoveryCodes,
  getTwoStepStatus,
  signInWriteOutcomeUnknown,
  turnOffTwoStep,
} from '../api/session';
import type { TwoStepSetup, TwoStepStatus } from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import { ModalDialog } from './ModalDialog';

const UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Close this window and open it again to see where it stands.';
const LOAD_FAILED = 'Two-step sign-in could not be loaded. Try again.';

type PasswordAction = 'setup' | 'codes' | 'off';

type View =
  | { kind: 'status' }
  | { kind: 'password'; action: PasswordAction }
  | { kind: 'setup'; setup: TwoStepSetup }
  | { kind: 'codes'; codes: string[]; turnedOn: boolean };

const PASSWORD_ACTIONS: Record<
  PasswordAction,
  { title: string; button: string }
> = {
  setup: { title: 'Set up two-step sign-in', button: 'Continue' },
  codes: {
    title: 'Create new recovery codes',
    button: 'Create new codes',
  },
  off: { title: 'Turn off two-step sign-in', button: 'Turn off' },
};

/** The QR code of an `otpauth://` link, always dark on light (scanners
 * need the contrast in both themes), with the standard quiet zone. */
function QrCode({ rows }: { rows: string[] }) {
  const quiet = 4;
  const size = rows.length + quiet * 2;
  let path = '';
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x += 1) {
      if (row[x] === '1') path += `M${x + quiet} ${y + quiet}h1v1h-1z`;
    }
  });
  return (
    <svg
      className="acct-qr"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="QR code for your authenticator app"
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}

/** The secret in groups of four, as authenticator apps show it. */
function grouped(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, '$1 ');
}

/**
 * Two-step sign-in of the signed-in user (the account menu): an
 * authenticator app (one-time codes) and its recovery codes. Never
 * required. Setting it up, creating new recovery codes and turning it
 * off ask for the password again; the server checks it and counts a wrong
 * one as a failed sign-in. Recovery codes are shown once, right after
 * they are made. Every write is disabled while disconnected.
 */
export function TwoStepDialog({ onClose }: { onClose: () => void }) {
  const { status: connectivity } = useConnectivity();
  const offline = connectivity !== 'connected';
  const [state, setState] = useState<TwoStepStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ kind: 'status' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setState(await getTwoStepStatus());
    } catch (failure) {
      setLoadError(failure instanceof ApiError ? failure.message : LOAD_FAILED);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const requestClose = () => {
    if (!busy) onClose();
  };

  const failed = (failure: unknown) => {
    setError(
      signInWriteOutcomeUnknown(failure) || !(failure instanceof ApiError)
        ? UNKNOWN_OUTCOME
        : failure.message,
    );
  };

  const run = async (action: () => Promise<void>) => {
    if (busy || offline) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      failed(failure);
    } finally {
      setBusy(false);
    }
  };

  const withPassword = (action: PasswordAction, password: string) =>
    run(async () => {
      if (action === 'setup') {
        setView({ kind: 'setup', setup: await beginTwoStepSetup(password) });
      } else if (action === 'codes') {
        const codes = await createRecoveryCodes(password);
        setView({ kind: 'codes', codes, turnedOn: false });
        await load();
      } else {
        setState(await turnOffTwoStep(password));
        setView({ kind: 'status' });
      }
    });

  const confirm = (code: string) =>
    run(async () => {
      const codes = await confirmTwoStepSetup(code);
      await load();
      setView(
        codes === null
          ? { kind: 'status' }
          : { kind: 'codes', codes, turnedOn: true },
      );
    });

  const title = 'Two-step sign-in';
  let body;
  if (view.kind === 'password') {
    body = (
      <PasswordStep
        action={view.action}
        busy={busy}
        offline={offline}
        error={error}
        onBack={() => {
          setError(null);
          setView({ kind: 'status' });
        }}
        onSubmit={(password) => void withPassword(view.action, password)}
      />
    );
  } else if (view.kind === 'setup') {
    body = (
      <SetupStep
        setup={view.setup}
        busy={busy}
        offline={offline}
        error={error}
        onBack={() => {
          setError(null);
          setView({ kind: 'status' });
        }}
        onConfirm={(code) => void confirm(code)}
      />
    );
  } else if (view.kind === 'codes') {
    body = (
      <CodesStep
        codes={view.codes}
        turnedOn={view.turnedOn}
        onDone={() => setView({ kind: 'status' })}
      />
    );
  } else {
    body = (
      <StatusStep
        state={state}
        loadError={loadError}
        offline={offline}
        onRetry={() => void load()}
        onClose={requestClose}
        onAction={(action) => {
          setError(null);
          setView({ kind: 'password', action });
        }}
      />
    );
  }

  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      {body}
    </ModalDialog>
  );
}

function StatusStep({
  state,
  loadError,
  offline,
  onRetry,
  onClose,
  onAction,
}: {
  state: TwoStepStatus | null;
  loadError: string | null;
  offline: boolean;
  onRetry: () => void;
  onClose: () => void;
  onAction: (action: PasswordAction) => void;
}) {
  if (loadError !== null) {
    return (
      <div className="acct-form">
        <div className="err" role="alert">
          {loadError}
        </div>
        <div className="row">
          <button type="button" className="bigbtn ghost" onClick={onClose}>
            Close (Esc)
          </button>
          <button type="button" className="bigbtn primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      </div>
    );
  }
  if (state === null) {
    return (
      <p className="sub" role="status" aria-label="Loading two-step sign-in">
        Loading…
      </p>
    );
  }
  const email = state.emailReady
    ? `PartFlow can also email a sign-in code to ${state.email ?? 'your address'}.`
    : state.email === null
      ? 'Your account has no email address, so PartFlow cannot email you a sign-in code. Add one with Email address… in your account menu.'
      : 'PartFlow cannot email sign-in codes until an administrator sets up the outgoing mail server.';
  return (
    <div className="acct-form">
      {state.enabled ? (
        <>
          <p className="sub">
            Two-step sign-in is on. After your password, PartFlow asks for the
            6-digit code your authenticator app shows.
          </p>
          <p className="acct-note">
            {`You have ${state.recoveryCodesLeft} recovery ${state.recoveryCodesLeft === 1 ? 'code' : 'codes'} left. Each works once if you lose the app.`}
          </p>
          <p className="acct-note">{email}</p>
        </>
      ) : (
        <p className="sub">
          Two-step sign-in is off. Turned on, signing in takes your password and
          a 6-digit code from an authenticator app such as 1Password, Google
          Authenticator or Microsoft Authenticator. It is never required.
        </p>
      )}
      <div className="row">
        <button type="button" className="bigbtn ghost" onClick={onClose}>
          Close (Esc)
        </button>
        {state.enabled ? (
          <>
            <button
              type="button"
              className="bigbtn"
              disabled={offline}
              onClick={() => onAction('codes')}
            >
              New recovery codes…
            </button>
            <button
              type="button"
              className="bigbtn danger"
              disabled={offline}
              onClick={() => onAction('off')}
            >
              Turn off…
            </button>
          </>
        ) : (
          <button
            type="button"
            className="bigbtn primary"
            disabled={offline}
            onClick={() => onAction('setup')}
          >
            Set up…
          </button>
        )}
      </div>
    </div>
  );
}

function PasswordStep({
  action,
  busy,
  offline,
  error,
  onBack,
  onSubmit,
}: {
  action: PasswordAction;
  busy: boolean;
  offline: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: (password: string) => void;
}) {
  const [password, setPassword] = useState('');
  const [attempted, setAttempted] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  const { title, button } = PASSWORD_ACTIONS[action];
  return (
    <form
      className="acct-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (password === '') {
          setAttempted(true);
          return;
        }
        onSubmit(password);
        setPassword('');
      }}
    >
      <p className="sub">{`${title}: enter your password to continue.`}</p>
      {action === 'codes' ? (
        <p className="acct-note">
          Your current recovery codes stop working when the new ones are made.
        </p>
      ) : null}
      {action === 'off' ? (
        <p className="acct-note">
          Signing in will take only your password, and your recovery codes are
          deleted.
        </p>
      ) : null}
      <label>
        <span>Password</span>
        <input
          ref={field}
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
          onClick={onBack}
        >
          Back
        </button>
        <button
          type="submit"
          className={action === 'off' ? 'bigbtn danger' : 'bigbtn primary'}
          disabled={busy || offline}
        >
          {button}
        </button>
      </div>
    </form>
  );
}

function SetupStep({
  setup,
  busy,
  offline,
  error,
  onBack,
  onConfirm,
}: {
  setup: TwoStepSetup;
  busy: boolean;
  offline: boolean;
  error: string | null;
  onBack: () => void;
  onConfirm: (code: string) => void;
}) {
  const [code, setCode] = useState('');
  const [attempted, setAttempted] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  return (
    <form
      className="acct-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (code.trim() === '') {
          setAttempted(true);
          return;
        }
        onConfirm(code);
        setCode('');
      }}
    >
      <p className="sub">
        Scan this code with your authenticator app, or add the key by hand. Then
        type the 6-digit code the app shows.
      </p>
      <div className="acct-qrbox">
        <QrCode rows={setup.qr} />
        <div className="acct-key">
          <span className="acct-keylabel">Key</span>
          <span className="mono">{grouped(setup.secret)}</span>
          <a className="acct-alt" href={setup.uri}>
            Open in an authenticator app on this device
          </a>
        </div>
      </div>
      <label>
        <span>Code from your authenticator app</span>
        <input
          ref={field}
          className="field mono"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
        />
      </label>
      {attempted && code.trim() === '' ? (
        <div className="err" role="alert">
          Enter the 6-digit code your authenticator app shows.
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
          onClick={onBack}
        >
          Back
        </button>
        <button
          type="submit"
          className="bigbtn primary"
          disabled={busy || offline}
        >
          Turn on
        </button>
      </div>
    </form>
  );
}

function CodesStep({
  codes,
  turnedOn,
  onDone,
}: {
  codes: string[];
  turnedOn: boolean;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join('\n'));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="acct-form">
      <p className="sub">
        {turnedOn
          ? 'Two-step sign-in is on. Save these recovery codes somewhere safe, such as your password manager.'
          : 'Your new recovery codes. The old ones no longer work.'}
      </p>
      <p className="acct-note">
        Each code signs you in once if you lose your authenticator app. They are
        shown only now.
      </p>
      <ul className="acct-codes" aria-label="Recovery codes">
        {codes.map((code) => (
          <li key={code} className="mono">
            {code}
          </li>
        ))}
      </ul>
      {copied ? (
        <p className="acct-note" role="status">
          Copied.
        </p>
      ) : null}
      <div className="row">
        <button type="button" className="bigbtn" onClick={() => void copy()}>
          Copy codes
        </button>
        <button type="button" className="bigbtn primary" onClick={onDone}>
          I saved them
        </button>
      </div>
    </div>
  );
}
