import './account-dialogs.css';

import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { signInWriteOutcomeUnknown } from '../api/session';
import type { SessionState } from '../api/session';
import { createFirstAdministrator, getSetupStatus } from '../api/setup';
import type { SetupRole } from '../api/setup';
import { useApiData } from '../api/use-api-data';
import { useConnectivity } from '../app/connectivity-context';
import { newPasswordError } from '../app/password-rules';
import {
  canonicalLoginName,
  loginNameError,
} from '../views/administration/user-login';
import { ModalDialog } from './ModalDialog';
import { ErrorState, LoadingState } from './view-states';

const SETUP_CLOSED = 'PartFlow already has an administrator. Sign in instead.';
const UNREACHABLE = 'The PartFlow server could not be reached. Try again.';

/** The 409 answer that setup is closed (also to a repeated request). */
function isSetupClosed(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    typeof error.body === 'object' &&
    error.body !== null &&
    (error.body as { setup_closed?: unknown }).setup_closed === true
  );
}

/**
 * First-run setup (`Set up PartFlow`): available only while PartFlow has
 * no administrator. The first administrator account is created with the
 * one-time setup token from the PartFlow server log; the server closes
 * setup with that creation and signs the new administrator in. Cancel
 * sends nothing; a refusal keeps every entry. An unanswered creation
 * re-reads the sign-in once and is never resent on its own (asked again,
 * the server answers that setup is closed).
 */
export function FirstRunSetupDialog({
  onCancel,
  onCreated,
  onOpenSignIn,
  onRefresh,
}: {
  onCancel: () => void;
  onCreated: (state: SessionState) => void;
  onOpenSignIn: () => void;
  /** Re-read the sign-in; null when it could not be read. */
  onRefresh: () => Promise<SessionState | null>;
}) {
  const statusData = useApiData(getSetupStatus);
  const [closed, setClosed] = useState(false);
  const [busy, setBusy] = useState(false);

  const setupClosed =
    closed ||
    (statusData.state.status === 'ready' && !statusData.state.data.open);

  // A closed setup also updates the account chip (no setup entry).
  useEffect(() => {
    if (setupClosed) void onRefresh();
  }, [setupClosed, onRefresh]);

  const requestClose = () => {
    if (!busy) onCancel();
  };

  let body;
  if (setupClosed) {
    body = (
      <>
        <p className="sub" role="status">
          {SETUP_CLOSED}
        </p>
        <div className="row">
          <button type="button" className="bigbtn ghost" onClick={onCancel}>
            Cancel (Esc)
          </button>
          <button
            type="button"
            className="bigbtn primary"
            onClick={onOpenSignIn}
          >
            Sign in
          </button>
        </div>
      </>
    );
  } else if (statusData.state.status === 'loading') {
    body = <LoadingState label="Loading setup" />;
  } else if (statusData.state.status === 'error') {
    body = (
      <ErrorState
        message="The setup status could not be loaded."
        detail={statusData.state.message}
        onRetry={statusData.reload}
      />
    );
  } else {
    body = (
      <SetupForm
        roles={statusData.state.data.eligibleRoles}
        busy={busy}
        onBusyChange={setBusy}
        onCancel={requestClose}
        onCreated={onCreated}
        onClosed={() => setClosed(true)}
        onRefresh={onRefresh}
      />
    );
  }

  return (
    <ModalDialog label="Set up PartFlow" size="wide" onClose={requestClose}>
      <h3>Set up PartFlow</h3>
      {body}
    </ModalDialog>
  );
}

function SetupForm({
  roles,
  busy,
  onBusyChange,
  onCancel,
  onCreated,
  onClosed,
  onRefresh,
}: {
  roles: SetupRole[];
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onCancel: () => void;
  onCreated: (state: SessionState) => void;
  onClosed: () => void;
  onRefresh: () => Promise<SessionState | null>;
}) {
  const { status } = useConnectivity();
  const offline = status !== 'connected';
  const [token, setToken] = useState('');
  const [name, setName] = useState('');
  const [login, setLogin] = useState('');
  const [roleId, setRoleId] = useState<number | null>(
    roles.length === 1 ? roles[0].id : null,
  );
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tokenField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    tokenField.current?.focus();
  }, []);

  const trimmedName = name.trim();
  const canonical = canonicalLoginName(login);
  const tokenMissing = token.trim() === '';
  const loginInvalid = loginNameError(login);
  const passwordInvalid = newPasswordError(password, repeat);
  const noRole = roles.length === 0;

  const submit = async () => {
    if (busy || offline || noRole) return;
    if (
      tokenMissing ||
      !trimmedName ||
      loginInvalid ||
      roleId === null ||
      passwordInvalid
    ) {
      setAttempted(true);
      return;
    }
    onBusyChange(true);
    setError(null);
    try {
      onCreated(
        await createFirstAdministrator({
          setupToken: token,
          loginName: canonical,
          displayName: trimmedName,
          roleId,
          password,
        }),
      );
    } catch (failure) {
      if (signInWriteOutcomeUnknown(failure)) {
        setError(UNREACHABLE);
        const after = await onRefresh();
        if (after?.user) {
          onCreated(after);
          return;
        }
      } else if (isSetupClosed(failure)) {
        onClosed();
      } else {
        setError(failure instanceof ApiError ? failure.message : UNREACHABLE);
      }
      onBusyChange(false);
    }
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
        PartFlow has no administrator yet. Enter the setup token from the
        PartFlow server log, then create the first administrator account.
      </p>
      <label>
        <span>Setup token</span>
        <input
          ref={tokenField}
          className="field mono"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      {attempted && tokenMissing ? (
        <div className="err" role="alert">
          Enter the setup token.
        </div>
      ) : null}
      <label>
        <span>Name</span>
        <input
          className="field"
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoComplete="name"
        />
      </label>
      {attempted && !trimmedName ? (
        <div className="err" role="alert">
          A name is required.
        </div>
      ) : null}
      <label>
        <span>Login name</span>
        <input
          className="field mono"
          value={login}
          onChange={(event) => setLogin(event.target.value)}
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
        />
      </label>
      <div aria-live="polite">
        {canonical && canonical !== login ? (
          <p className="acct-note">
            Saved as: <span className="mono">{canonical}</span>
          </p>
        ) : null}
      </div>
      {attempted && loginInvalid ? (
        <div className="err" role="alert">
          {loginInvalid}
        </div>
      ) : null}
      <label>
        <span>Role</span>
        <select
          className="field"
          value={roleId === null ? '' : String(roleId)}
          disabled={noRole}
          onChange={(event) => setRoleId(Number(event.target.value))}
        >
          {roleId === null ? (
            <option value="" disabled>
              Choose a role…
            </option>
          ) : null}
          {roles.map((role) => (
            <option key={role.id} value={String(role.id)}>
              {role.name}
            </option>
          ))}
        </select>
      </label>
      {noRole ? (
        <div className="err" role="alert">
          No role can administer PartFlow. A role must be allowed to manage
          users and roles and correction permissions.
        </div>
      ) : attempted && roleId === null ? (
        <div className="err" role="alert">
          Choose a role.
        </div>
      ) : null}
      <label>
        <span>Password</span>
        <input
          className="field"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="new-password"
        />
      </label>
      <p className="acct-help">At least 12 characters.</p>
      <label>
        <span>Repeat password</span>
        <input
          className="field"
          type="password"
          value={repeat}
          onChange={(event) => setRepeat(event.target.value)}
          autoComplete="new-password"
        />
      </label>
      {attempted && passwordInvalid ? (
        <div className="err" role="alert">
          {passwordInvalid}
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
          onClick={onCancel}
        >
          Cancel (Esc)
        </button>
        <button
          type="submit"
          className="bigbtn primary"
          disabled={busy || offline || noRole}
        >
          Create administrator
        </button>
      </div>
    </form>
  );
}
