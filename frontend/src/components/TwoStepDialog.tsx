import './account-dialogs.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import {
  createPasskey,
  PasskeyCancelledError,
  passkeysSupported,
} from '../api/passkeys';
import type { PasskeyOptionsJson } from '../api/passkeys';
import {
  addPasskey,
  beginPasskeyAdd,
  beginTwoStepSetup,
  confirmTwoStepSetup,
  createRecoveryCodes,
  getTwoStepStatus,
  removePasskey,
  signInWriteOutcomeUnknown,
  turnOffTwoStep,
} from '../api/session';
import type { PasskeyInfo, TwoStepSetup, TwoStepStatus } from '../api/session';
import { useConnectivity } from '../app/connectivity-context';
import { formatTimestampShort } from '../views/dates';
import { ModalDialog } from './ModalDialog';

const UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Close this window and open it again to see where it stands.';
const LOAD_FAILED = 'Two-step sign-in could not be loaded. Try again.';
const PASSKEY_FAILED =
  'The passkey could not be created. Try again, and let your device or password manager save it.';
const NAME_RULE =
  'Name the passkey with 1 to 80 characters, without line breaks or other control characters.';

type PasswordAction =
  | { kind: 'setup' }
  | { kind: 'codes' }
  | { kind: 'remove-app' }
  | { kind: 'add-passkey' }
  | { kind: 'remove-passkey'; passkey: PasskeyInfo };

type View =
  | { kind: 'status' }
  | { kind: 'password'; action: PasswordAction }
  | { kind: 'setup'; setup: TwoStepSetup }
  | { kind: 'passkey'; options: PasskeyOptionsJson }
  | { kind: 'codes'; codes: string[]; turnedOn: boolean };

function actionWords(
  action: PasswordAction,
  state: TwoStepStatus | null,
): { title: string; button: string; danger: boolean; note: string | null } {
  const lastFactor =
    state !== null &&
    (state.authenticatorApp ? 1 : 0) + state.passkeys.length === 1;
  const offNote =
    'Signing in will take only your password, and your recovery codes are deleted.';
  switch (action.kind) {
    case 'setup':
      return {
        title: 'Set up an authenticator app',
        button: 'Continue',
        danger: false,
        note: null,
      };
    case 'codes':
      return {
        title: 'Create new recovery codes',
        button: 'Create new codes',
        danger: false,
        note: 'Your current recovery codes stop working when the new ones are made.',
      };
    case 'remove-app':
      return {
        title: 'Remove the authenticator app',
        button: 'Remove app',
        danger: true,
        note: lastFactor
          ? offNote
          : 'Two-step sign-in stays on with your passkeys.',
      };
    case 'add-passkey':
      return {
        title: 'Add a passkey',
        button: 'Continue',
        danger: false,
        note: null,
      };
    case 'remove-passkey':
      return {
        title: `Remove the passkey ${action.passkey.name}`,
        button: 'Remove passkey',
        danger: true,
        note: lastFactor ? offNote : 'Two-step sign-in stays on.',
      };
  }
}

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
 * authenticator app (one-time codes), passkeys and the recovery codes that
 * come with them. Never required; on while an app or a passkey is set up.
 * Setting up the app, adding or removing a passkey, removing the app and
 * creating new recovery codes ask for the password again; the server
 * checks it and counts a wrong one as a failed sign-in. Recovery codes are
 * shown once, right after they are made. Passkeys can be added only where
 * the browser can use them (PartFlow at its HTTPS address). Every write is
 * disabled while disconnected.
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
      switch (action.kind) {
        case 'setup':
          setView({ kind: 'setup', setup: await beginTwoStepSetup(password) });
          return;
        case 'codes': {
          const codes = await createRecoveryCodes(password);
          setView({ kind: 'codes', codes, turnedOn: false });
          await load();
          return;
        }
        case 'remove-app':
          setState(await turnOffTwoStep(password));
          setView({ kind: 'status' });
          return;
        case 'add-passkey':
          setView({
            kind: 'passkey',
            options: await beginPasskeyAdd(password),
          });
          return;
        case 'remove-passkey':
          setState(await removePasskey(action.passkey.id, password));
          setView({ kind: 'status' });
          return;
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

  const create = (options: PasskeyOptionsJson, name: string) => {
    if (busy || offline) return;
    setBusy(true);
    setError(null);
    void (async () => {
      try {
        const credential = await createPasskey(options);
        const codes = await addPasskey(name, credential);
        await load();
        setView(
          codes === null
            ? { kind: 'status' }
            : { kind: 'codes', codes, turnedOn: true },
        );
      } catch (failure) {
        if (failure instanceof PasskeyCancelledError) {
          setError(null);
        } else if (failure instanceof ApiError) {
          failed(failure);
        } else {
          setError(PASSKEY_FAILED);
        }
      } finally {
        setBusy(false);
      }
    })();
  };

  const back = () => {
    setError(null);
    setView({ kind: 'status' });
  };

  const title = 'Two-step sign-in';
  let body;
  if (view.kind === 'password') {
    body = (
      <PasswordStep
        words={actionWords(view.action, state)}
        busy={busy}
        offline={offline}
        error={error}
        onBack={back}
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
        onBack={back}
        onConfirm={(code) => void confirm(code)}
      />
    );
  } else if (view.kind === 'passkey') {
    body = (
      <PasskeyStep
        busy={busy}
        offline={offline}
        error={error}
        onBack={back}
        onCreate={(name) => create(view.options, name)}
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
        <p className="sub">
          Two-step sign-in is on. After your password, PartFlow asks for a code
          from your authenticator app or one of your passkeys.
        </p>
      ) : (
        <p className="sub">
          Two-step sign-in is off. Turned on, signing in takes your password and
          a second step: a 6-digit code from an authenticator app such as
          1Password, Google Authenticator or Microsoft Authenticator, or a
          passkey. It is never required.
        </p>
      )}

      <h4 className="acct-subhead">Authenticator app</h4>
      <div className="acct-line">
        <span>{state.authenticatorApp ? 'Set up' : 'Not set up'}</span>
        {state.authenticatorApp ? (
          <button
            type="button"
            className="bigbtn danger"
            aria-label="Remove the authenticator app…"
            disabled={offline}
            onClick={() => onAction({ kind: 'remove-app' })}
          >
            Remove…
          </button>
        ) : (
          <button
            type="button"
            className="bigbtn primary"
            disabled={offline}
            onClick={() => onAction({ kind: 'setup' })}
          >
            Set up…
          </button>
        )}
      </div>

      <h4 className="acct-subhead">Passkeys</h4>
      {state.passkeys.length === 0 ? (
        <p className="acct-note">
          No passkey yet. A passkey signs you in without your password, using
          your device, your phone or your password manager.
        </p>
      ) : (
        <ul className="acct-passkeys" aria-label="Passkeys">
          {state.passkeys.map((passkey) => (
            <li key={passkey.id} className="acct-line">
              <span>
                <strong>{passkey.name}</strong>
                <span className="acct-note">
                  {`${passkey.rpId} · added ${formatTimestampShort(passkey.createdAt)}${
                    passkey.lastUsedAt === null
                      ? ''
                      : ` · last used ${formatTimestampShort(passkey.lastUsedAt)}`
                  }`}
                </span>
              </span>
              <button
                type="button"
                className="bigbtn danger"
                aria-label={`Remove the passkey ${passkey.name}…`}
                disabled={offline}
                onClick={() => onAction({ kind: 'remove-passkey', passkey })}
              >
                Remove…
              </button>
            </li>
          ))}
        </ul>
      )}
      {passkeysSupported() ? (
        <div className="row">
          <button
            type="button"
            className="bigbtn"
            disabled={offline}
            onClick={() => onAction({ kind: 'add-passkey' })}
          >
            Add a passkey…
          </button>
        </div>
      ) : (
        <p className="acct-note">
          Passkeys can be added when PartFlow is opened at its HTTPS address.
        </p>
      )}

      {state.enabled ? (
        <>
          <h4 className="acct-subhead">Recovery codes</h4>
          <div className="acct-line">
            <span>
              {`You have ${state.recoveryCodesLeft} recovery ${state.recoveryCodesLeft === 1 ? 'code' : 'codes'} left. Each works once if you lose your app and passkeys.`}
            </span>
            <button
              type="button"
              className="bigbtn"
              disabled={offline}
              onClick={() => onAction({ kind: 'codes' })}
            >
              New recovery codes…
            </button>
          </div>
        </>
      ) : null}
      <p className="acct-note">{email}</p>
      <div className="row">
        <button type="button" className="bigbtn ghost" onClick={onClose}>
          Close (Esc)
        </button>
      </div>
    </div>
  );
}

function PasswordStep({
  words,
  busy,
  offline,
  error,
  onBack,
  onSubmit,
}: {
  words: ReturnType<typeof actionWords>;
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
      <p className="sub">{`${words.title}: enter your password to continue.`}</p>
      {words.note !== null ? <p className="acct-note">{words.note}</p> : null}
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
          className={words.danger ? 'bigbtn danger' : 'bigbtn primary'}
          disabled={busy || offline}
        >
          {words.button}
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

/** The name of a passkey as the server takes it: trimmed, 1 to 80
 * printable characters. */
function passkeyName(text: string): string | null {
  const name = text.trim();
  const control = [...name].some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  });
  return name.length >= 1 && name.length <= 80 && !control ? name : null;
}

function PasskeyStep({
  busy,
  offline,
  error,
  onBack,
  onCreate,
}: {
  busy: boolean;
  offline: boolean;
  error: string | null;
  onBack: () => void;
  onCreate: (name: string) => void;
}) {
  const [text, setText] = useState('');
  const [attempted, setAttempted] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  const name = passkeyName(text);
  return (
    <form
      className="acct-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (name === null) {
          setAttempted(true);
          return;
        }
        onCreate(name);
      }}
    >
      <p className="sub">
        Name the passkey so you can tell it apart, for example the device or the
        password manager that keeps it. Then choose Create passkey: your browser
        asks where to save it.
      </p>
      <label>
        <span>Passkey name</span>
        <input
          ref={field}
          className="field"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="e.g. Work laptop"
          maxLength={200}
          autoComplete="off"
        />
      </label>
      {attempted && name === null ? (
        <div className="err" role="alert">
          {NAME_RULE}
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
          Create passkey
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
        Each code signs you in once if you lose your authenticator app and
        passkeys. They are shown only now.
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
