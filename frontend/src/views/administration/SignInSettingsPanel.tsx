import { useState } from 'react';

import { ApiError } from '../../api/client';
import { getSignInPolicy, updateSignInPolicy } from '../../api/policies';
import type { SignInPolicy } from '../../api/policies';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AdminField, PolicySwitch, ServerErrorNote } from './section-widgets';

// Administration → Settings → User sign-in (application Users — never the
// Worker Sessions of the Scan Stations, which live in Policies → Worker
// sessions): how long a user's sign-in lasts (or that it never expires),
// how many failed sign-ins lock an account and for how long, and whether
// a password an administrator set must be replaced. Signed-in users read
// it; users whose role may configure system settings edit it. Save sends
// only the changed settings (the server merges them), so a stale editor
// never reverts another administrator's change. The server validates and
// applies every value.

const DAYS_ERROR =
  'User sign-ins must expire after a whole number of days from 1 to 365.';
const ATTEMPTS_ERROR =
  'The number of failed sign-ins before a lock must be a whole number from 3 to 100.';
const MINUTES_ERROR =
  'The lock duration must be a whole number of minutes from 1 to 1440.';
const UNKNOWN_OUTCOME =
  'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.';

/** The whole number in `text` within [min, max], else null (never
 * rounded or clamped). */
function parseWhole(text: string, min: number, max: number): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= max ? value : null;
}

export function SignInSettingsPanel() {
  const session = useSession();
  return (
    <>
      <h2>User sign-in</h2>
      <p className="ad-confighelp">
        How long a user&apos;s sign-in lasts, when repeated failed sign-ins lock
        a user&apos;s account, and whether users must replace a password an
        administrator set.
      </p>
      {session.status === 'signed-in' ? (
        <SignInPolicyView canEdit={session.can('CONFIGURE_SYSTEM_SETTINGS')} />
      ) : (
        <>
          <p className="ad-confighelp">
            Sign in to see the user sign-in settings.
          </p>
          <div className="row">
            <button className="btn primary" onClick={session.openSignIn}>
              Sign in
            </button>
          </div>
        </>
      )}
    </>
  );
}

function SignInPolicyView({ canEdit }: { canEdit: boolean }) {
  const { status } = useConnectivity();
  const policyData = useApiData(getSignInPolicy);
  const [editing, setEditing] = useState(false);

  if (policyData.state.status === 'loading') {
    return <LoadingState label="Loading user sign-in settings" />;
  }
  if (policyData.state.status === 'error') {
    return (
      <ErrorState
        message="The user sign-in settings could not be loaded."
        detail={policyData.state.message}
        onRetry={policyData.reload}
      />
    );
  }
  const policy = policyData.state.data;
  return (
    <>
      <div className="ad-configpreview">
        <div className="prow">
          <span className="k">User sign-ins expire</span>
          <span className="v">
            {policy.sessionExpires
              ? `After ${policy.sessionDays} days`
              : 'Never'}
          </span>
        </div>
        <div className="prow">
          <span className="k">Failed sign-ins before a lock</span>
          <span className="v">{policy.lockoutAttempts}</span>
        </div>
        <div className="prow">
          <span className="k">Lock duration</span>
          <span className="v">{`${policy.lockoutMinutes} minutes`}</span>
        </div>
        <div className="prow">
          <span className="k">New password at first sign-in</span>
          <span className="v">
            {policy.requirePasswordChange ? 'Required' : 'Not required'}
          </span>
        </div>
      </div>
      {canEdit ? (
        <div className="row">
          <button
            className="bigbtn primary"
            disabled={status !== 'connected'}
            onClick={() => setEditing(true)}
          >
            Edit user sign-in settings…
          </button>
        </div>
      ) : (
        <p className="ad-confighelp">
          Only users whose role may configure system settings can change these.
        </p>
      )}
      {editing ? (
        <SignInPolicyDialog
          saved={policy}
          writeBlocked={status !== 'connected'}
          onClose={(wrote) => {
            setEditing(false);
            if (wrote) policyData.reload();
          }}
        />
      ) : null}
    </>
  );
}

function SignInPolicyDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: SignInPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the settings). */
  onClose: (wrote: boolean) => void;
}) {
  const [expires, setExpires] = useState(saved.sessionExpires);
  const [daysText, setDaysText] = useState(String(saved.sessionDays));
  const [attemptsText, setAttemptsText] = useState(
    String(saved.lockoutAttempts),
  );
  const [minutesText, setMinutesText] = useState(String(saved.lockoutMinutes));
  const [requireChange, setRequireChange] = useState(
    saved.requirePasswordChange,
  );
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const days = parseWhole(daysText, 1, 365);
  const attempts = parseWhole(attemptsText, 3, 100);
  const minutes = parseWhole(minutesText, 1, 1440);
  // While expiry is Off the days field is disabled and its stored value
  // kept: whatever it holds neither blocks the save nor is sent.
  const daysInvalid = expires && days === null;
  const invalid = daysInvalid || attempts === null || minutes === null;

  // Cancel, Escape and the backdrop are ignored while a save is in
  // flight.
  const requestClose = () => {
    if (!busy) onClose(wrote);
  };

  const submit = async () => {
    if (busy || writeBlocked || invalid) return;
    // Only the settings whose value differs from the stored policy.
    const patch = {
      ...(expires !== saved.sessionExpires ? { sessionExpires: expires } : {}),
      ...(expires && days !== null && days !== saved.sessionDays
        ? { sessionDays: days }
        : {}),
      ...(attempts !== saved.lockoutAttempts
        ? { lockoutAttempts: attempts }
        : {}),
      ...(minutes !== saved.lockoutMinutes ? { lockoutMinutes: minutes } : {}),
      ...(requireChange !== saved.requirePasswordChange
        ? { requirePasswordChange: requireChange }
        : {}),
    };
    if (Object.keys(patch).length === 0) {
      onClose(wrote);
      return;
    }
    setBusy(true);
    setServerError(null);
    setWrote(true);
    try {
      await updateSignInPolicy(patch);
      onClose(true);
    } catch (error) {
      setServerError(
        signInWriteOutcomeUnknown(error) || !(error instanceof ApiError)
          ? UNKNOWN_OUTCOME
          : error.message,
      );
      setBusy(false);
    }
  };

  const title = 'User sign-in settings';
  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      <div className="ad-form">
        <div className="ad-switchlist">
          <PolicySwitch
            label="User sign-ins expire"
            description="When Off, a user stays signed in until they sign out."
            ariaLabel="User sign-ins expire"
            on={expires}
            disabled={busy}
            onToggle={() => setExpires((on) => !on)}
          />
        </div>
        <AdminField label="Expire after (days)">
          <input
            className="field mono"
            type="number"
            min={1}
            max={365}
            step={1}
            value={daysText}
            disabled={!expires || busy}
            onChange={(event) => setDaysText(event.target.value)}
          />
        </AdminField>
        {daysInvalid ? (
          <div className="err" role="alert">
            {DAYS_ERROR}
          </div>
        ) : null}
        <AdminField label="Failed sign-ins before a lock">
          <input
            className="field mono"
            type="number"
            min={3}
            max={100}
            step={1}
            value={attemptsText}
            disabled={busy}
            onChange={(event) => setAttemptsText(event.target.value)}
          />
        </AdminField>
        {attempts === null ? (
          <div className="err" role="alert">
            {ATTEMPTS_ERROR}
          </div>
        ) : null}
        <AdminField label="Lock duration (minutes)">
          <input
            className="field mono"
            type="number"
            min={1}
            max={1440}
            step={1}
            value={minutesText}
            disabled={busy}
            onChange={(event) => setMinutesText(event.target.value)}
          />
        </AdminField>
        {minutes === null ? (
          <div className="err" role="alert">
            {MINUTES_ERROR}
          </div>
        ) : null}
        <div className="ad-switchlist">
          <PolicySwitch
            label="Require a new password at first sign-in"
            description="Applies to passwords set by an administrator."
            ariaLabel="Require a new password at first sign-in"
            on={requireChange}
            disabled={busy}
            onToggle={() => setRequireChange((on) => !on)}
          />
        </div>
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked || invalid}
          onClick={() => void submit()}
        >
          Save changes
        </button>
      </div>
    </ModalDialog>
  );
}
