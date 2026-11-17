import { useState } from 'react';

import { ApiError } from '../../api/client';
import {
  getRememberMePolicy,
  updateRememberMePolicy,
} from '../../api/policies';
import type { RememberMePolicy } from '../../api/policies';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AdminField, PolicySwitch, ServerErrorNote } from './section-widgets';

// Administration → Security → Remember me (owner request of 2026-11-17):
// whether the Sign in dialog offers Remember me and how long a remembered
// sign-in lasts — whole days from 1 to 365, or until the user signs out.
// While it is offered, a sign-in without Remember me ends when the browser
// closes (and at the latest when User sign-in says); a remembered one
// survives closing the browser for its own time. A remembered sign-in
// that lasts until sign-out stays as long as PartFlow is opened at least
// once every 400 days (the longest a browser keeps a cookie; each visit
// renews it). Signing out, a password change or reset, deactivation and a
// two-step reset end remembered sign-ins like any other. Every signed-in
// user reads it; users whose role may configure system settings edit it.
// Save sends only the changed settings; the server validates them.

const DAYS_RANGE = [1, 365] as const;
const DAYS_ERROR =
  'A remembered sign-in must last a whole number of days from 1 to 365.';
const UNKNOWN_OUTCOME =
  'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.';

/** The whole number of days in `text` within the range, else null. */
function parseDays(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) &&
    value >= DAYS_RANGE[0] &&
    value <= DAYS_RANGE[1]
    ? value
    : null;
}

function lasts(policy: RememberMePolicy): string {
  if (!policy.expires) return 'Until the user signs out';
  return `${policy.days} ${policy.days === 1 ? 'day' : 'days'}`;
}

export function RememberMePanel() {
  const session = useSession();
  return (
    <>
      <h2>Remember me</h2>
      <p className="admin-confighelp">
        Whether the Sign in dialog offers Remember me, and how long a remembered
        sign-in lasts. While it is offered, a sign-in without Remember me ends
        when the browser closes, or earlier when User sign-in says so. A sign-in
        remembered until the user signs out stays as long as PartFlow is opened
        at least once every 400 days.
      </p>
      <RememberMeView canEdit={session.can('CONFIGURE_SYSTEM_SETTINGS')} />
    </>
  );
}

function RememberMeView({ canEdit }: { canEdit: boolean }) {
  const { status } = useConnectivity();
  const policyData = useApiData(getRememberMePolicy);
  const [editing, setEditing] = useState(false);

  if (policyData.state.status === 'loading') {
    return <LoadingState label="Loading Remember me settings" />;
  }
  if (policyData.state.status === 'error') {
    return (
      <ErrorState
        message="The Remember me settings could not be loaded."
        detail={policyData.state.message}
        onRetry={policyData.reload}
      />
    );
  }
  const policy = policyData.state.data;
  return (
    <>
      <div className="admin-configpreview">
        <div className="prow">
          <span className="k">Offer Remember me</span>
          <span className="v">{policy.offered ? 'On' : 'Off'}</span>
        </div>
        <div className="prow">
          <span className="k">A remembered sign-in lasts</span>
          <span className="v">{lasts(policy)}</span>
        </div>
      </div>
      {canEdit ? (
        <div className="row">
          <button
            className="bigbtn primary"
            disabled={status !== 'connected'}
            onClick={() => setEditing(true)}
          >
            Edit Remember me settings…
          </button>
        </div>
      ) : null}
      {editing ? (
        <RememberMeDialog
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

function RememberMeDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: RememberMePolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the settings). */
  onClose: (wrote: boolean) => void;
}) {
  const [offered, setOffered] = useState(saved.offered);
  const [untilSignOut, setUntilSignOut] = useState(!saved.expires);
  const [daysText, setDaysText] = useState(String(saved.days));
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const days = parseDays(daysText);
  // While a remembered sign-in lasts until sign-out the days field is
  // disabled and its stored value kept: it neither blocks nor is sent.
  const daysInvalid = !untilSignOut && days === null;
  const patch = {
    ...(offered !== saved.offered ? { offered } : {}),
    ...(!untilSignOut !== saved.expires ? { expires: !untilSignOut } : {}),
    ...(!untilSignOut && days !== null && days !== saved.days ? { days } : {}),
  };

  const guard = useCloseGuard({
    dirty: daysInvalid || Object.keys(patch).length > 0,
    busy,
    onClose: () => onClose(wrote),
  });

  const submit = async () => {
    if (busy || writeBlocked || daysInvalid) return;
    if (Object.keys(patch).length === 0) {
      onClose(wrote);
      return;
    }
    setBusy(true);
    setServerError(null);
    setWrote(true);
    try {
      await updateRememberMePolicy(patch);
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

  const title = 'Remember me settings';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="admin-form">
        <div className="admin-switchlist">
          <PolicySwitch
            label="Offer Remember me"
            description="Show a Remember me checkbox in the Sign in dialog."
            ariaLabel="Offer Remember me"
            on={offered}
            disabled={busy}
            onToggle={() => setOffered((on) => !on)}
          />
          <PolicySwitch
            label="Remember until the user signs out"
            description="When Off, a remembered sign-in ends after the days below."
            ariaLabel="Remember until the user signs out"
            on={untilSignOut}
            disabled={busy}
            onToggle={() => setUntilSignOut((on) => !on)}
          />
        </div>
        <AdminField label="Remember for (days)">
          <input
            className="field mono"
            type="number"
            min={DAYS_RANGE[0]}
            max={DAYS_RANGE[1]}
            step={1}
            value={daysText}
            disabled={untilSignOut || busy}
            onChange={(event) => setDaysText(event.target.value)}
          />
        </AdminField>
        {daysInvalid ? (
          <div className="err" role="alert">
            {DAYS_ERROR}
          </div>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button
          className="bigbtn ghost"
          disabled={busy}
          onClick={guard.requestClose}
        >
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked || daysInvalid}
          onClick={() => void submit()}
        >
          Save changes
        </button>
      </div>
      {guard.asking ? (
        <UnsavedChangesDialog
          saveDisabled={writeBlocked || daysInvalid}
          onKeepEditing={guard.keepEditing}
          onSave={() => void submit()}
          onDiscard={() => onClose(wrote)}
        >
          You have unsaved changes to the Remember me settings.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}
