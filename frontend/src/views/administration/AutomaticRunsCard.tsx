import { useId, useState } from 'react';

import { ApiError } from '../../api/client';
import { ARCHIVE_MEASURES, updateRetentionPolicy } from '../../api/policies';
import type {
  ArchiveMeasure,
  RetentionPatch,
  RetentionPolicy,
  StandingAuthorization,
} from '../../api/policies';
import { writeOutcomeUnknown } from '../../api/scan-station';
import type { ApiData } from '../../api/use-api-data';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  APPROVAL_OFF_CONFIRMATION,
  isArchiveMeasure,
  LIMIT_FIELDS,
  limitText,
  MEASURE_LABELS,
  MEASURE_LIMIT,
  measureLabel,
  standingAuthorizationText,
} from './automatic-runs';
import {
  parseWhole,
  SETTINGS_OUTCOME_UNKNOWN,
  WHOLE_NUMBER_ERROR,
} from './retention-period';
import {
  AdminField,
  OnOffValue,
  PolicySwitch,
  ServerErrorNote,
  SettingsCard,
} from './section-widgets';

// Administration → History archival & purge → Automatic runs (archival
// design B step 4, D9–D11): whether the server's archival job creates runs
// by itself, the measure that decides it with that measure's own limit,
// the days between automatic runs, and the approval step. With the step
// on, an automatic run waits as a proposal for a person to approve or
// dismiss it (the Runs card); switching it off asks for a reason and the
// typed confirmation RUN WITHOUT APPROVAL, and that audited change is the
// standing authorization the runs then start under. The card shows who
// gave it, when and why, and whether it is still valid, with the server's
// words when it is not; while the step stays off the same form renews it.
// Switching automatic runs off or the step on makes the server stop a run
// started without approval before it purges; the dialog says so.
// The values come from `/api/policies/data-retention`, shared with the
// Retention and schedule card. Save sends only the changed values; the
// server validates every value, checks the reason and the confirmation,
// and its messages are shown as given.

export function AutomaticRunsCard({
  policyData,
  canWrite,
  writeBlocked,
}: {
  policyData: ApiData<RetentionPolicy>;
  canWrite: boolean;
  writeBlocked: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const { state } = policyData;
  const policy = state.status === 'ready' ? state.data : null;

  return (
    <SettingsCard
      title="Automatic runs"
      description="When the server's archival job starts a run by itself, and whether a person approves each run first."
      edit={
        canWrite && policy
          ? {
              label: 'Edit automatic runs…',
              disabled: writeBlocked,
              onClick: () => setEditing(true),
            }
          : undefined
      }
      note="The archival job checks the measure in its hourly work; a run it proposes waits in the Runs table until someone with Run history archival and purge approves or dismisses it."
    >
      {state.status === 'loading' ? (
        <LoadingState label="Loading automatic runs" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The automatic run settings could not be loaded."
          detail={state.message}
          onRetry={policyData.reload}
        />
      ) : null}
      {policy ? <AutomaticRunsValues policy={policy} /> : null}
      {editing && policy ? (
        <AutomaticRunsDialog
          saved={policy}
          writeBlocked={writeBlocked}
          onClose={(wrote) => {
            setEditing(false);
            if (wrote) policyData.reload();
          }}
        />
      ) : null}
    </SettingsCard>
  );
}

function days(count: number): string {
  return `${count} ${count === 1 ? 'day' : 'days'}`;
}

function AutomaticRunsValues({ policy }: { policy: RetentionPolicy }) {
  const authorization = policy.standingAuthorization;
  return (
    <>
      <div className="admin-configpreview">
        <div className="prow">
          <span className="k">Automatic runs</span>
          <span className="v">
            <OnOffValue on={policy.automaticRuns} />
          </span>
        </div>
        <div className="prow">
          <span className="k">Measure</span>
          <span className="v">{measureLabel(policy.measure)}</span>
        </div>
        <div className="prow">
          <span className="k">Limit</span>
          <span className="v">{limitText(policy)}</span>
        </div>
        <div className="prow">
          <span className="k">Days between automatic runs</span>
          <span className="v">{days(policy.intervalDays)}</span>
        </div>
        <div className="prow">
          <span className="k">Approval step</span>
          <span className="v">
            <OnOffValue on={policy.approvalRequired} />
          </span>
        </div>
        {authorization !== null ? (
          <div className="prow">
            <span className="k">Standing authorization</span>
            <span className="v">
              <OnOffValue
                on={authorization.valid}
                onText="Valid"
                offText="Not valid"
              />
            </span>
          </div>
        ) : null}
      </div>
      {authorization !== null ? (
        <StandingAuthorizationNote authorization={authorization} />
      ) : null}
    </>
  );
}

/** Who gave the standing authorization, when and why, and — while it is
 * not valid — why, as the server words it. */
function StandingAuthorizationNote({
  authorization,
}: {
  authorization: StandingAuthorization;
}) {
  return (
    <>
      <p className="admin-confighelp">
        {standingAuthorizationText(authorization)}
      </p>
      {authorization.problem !== null ? (
        <div className="err">{authorization.problem}</div>
      ) : null}
    </>
  );
}

function AutomaticRunsDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: RetentionPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the settings). */
  onClose: (wrote: boolean) => void;
}) {
  const confirmationId = useId();
  const [automatic, setAutomatic] = useState(saved.automaticRuns);
  const [measure, setMeasure] = useState(saved.measure);
  // Each measure keeps its own limit; the dialog shows the chosen one's.
  const [limitTexts, setLimitTexts] = useState<Record<ArchiveMeasure, string>>({
    LIVE_MOVEMENT_ROWS: String(saved.limitMovementRows),
    RETAINED_HISTORY_AGE: String(saved.limitHistoryMonths),
    DATABASE_SIZE: String(saved.limitDatabaseGib),
  });
  const [intervalText, setIntervalText] = useState(String(saved.intervalDays));
  const [approvalRequired, setApprovalRequired] = useState(
    saved.approvalRequired,
  );
  const [renew, setRenew] = useState(false);
  const [reason, setReason] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const chosen = isArchiveMeasure(measure) ? measure : null;
  const limit = chosen === null ? null : parseWhole(limitTexts[chosen]);
  const interval = parseWhole(intervalText);
  const invalid = (chosen !== null && limit === null) || interval === null;
  // Switching the approval step off, or renewing its standing
  // authorization while it stays off: both send the reason and the typed
  // confirmation, which the server checks.
  const authorizing = !approvalRequired && (saved.approvalRequired || renew);

  // Only the valid settings whose value differs from the stored ones.
  const patch: RetentionPatch = {};
  if (automatic !== saved.automaticRuns) patch.automaticRuns = automatic;
  if (measure !== saved.measure) patch.measure = measure;
  if (chosen !== null && limit !== null) {
    const setting = MEASURE_LIMIT[chosen];
    if (limit !== saved[setting]) patch[setting] = limit;
  }
  if (interval !== null && interval !== saved.intervalDays) {
    patch.intervalDays = interval;
  }
  if (approvalRequired !== saved.approvalRequired) {
    patch.approvalRequired = approvalRequired;
  }
  const changed = Object.keys(patch).length > 0 || authorizing;

  const guard = useCloseGuard({
    dirty: invalid || changed,
    busy,
    onClose: () => onClose(wrote),
  });

  const submit = async () => {
    if (busy || writeBlocked || invalid) return;
    if (!changed) {
      onClose(wrote);
      return;
    }
    setBusy(true);
    setServerError(null);
    setWrote(true);
    try {
      await updateRetentionPolicy(
        patch,
        authorizing ? reason : undefined,
        authorizing ? confirmation : undefined,
      );
      onClose(true);
    } catch (error) {
      setServerError(
        writeOutcomeUnknown(error) || !(error instanceof ApiError)
          ? SETTINGS_OUTCOME_UNKNOWN
          : error.message,
      );
      setBusy(false);
    }
  };

  const title = 'Automatic runs';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="admin-form">
        <PolicySwitch
          label="Automatic runs"
          description="The archival job on the server checks the measure in its hourly work and starts a run when the measure passes its limit and something is eligible at the retention cutoff."
          ariaLabel="Automatic runs"
          on={automatic}
          disabled={busy}
          onToggle={() => setAutomatic((on) => !on)}
        />
        {/* Only while the approval step is off can a run started without
            approval be pending; the server stops it. */}
        {saved.automaticRuns && !automatic && !saved.approvalRequired ? (
          <p className="admin-fieldhelp">
            Switching automatic runs off also stops a run started without
            approval before it purges any history.
          </p>
        ) : null}
        <AdminField label="Measure">
          <select
            className="field"
            value={measure}
            disabled={busy}
            onChange={(event) => setMeasure(event.target.value)}
          >
            {ARCHIVE_MEASURES.map((value) => (
              <option key={value} value={value}>
                {MEASURE_LABELS[value]}
              </option>
            ))}
            {chosen === null ? (
              <option value={measure}>{measure}</option>
            ) : null}
          </select>
        </AdminField>
        {chosen !== null ? (
          <>
            <AdminField label={LIMIT_FIELDS[chosen].label}>
              <input
                className="field mono"
                type="number"
                min={0}
                step={1}
                value={limitTexts[chosen]}
                disabled={busy}
                onChange={(event) => {
                  const text = event.target.value;
                  setLimitTexts((texts) => ({ ...texts, [chosen]: text }));
                }}
              />
            </AdminField>
            {limit === null ? (
              <div className="err" role="alert">
                {WHOLE_NUMBER_ERROR}
              </div>
            ) : (
              <p className="admin-fieldhelp">{LIMIT_FIELDS[chosen].help}</p>
            )}
          </>
        ) : null}
        <AdminField label="Days between automatic runs">
          <input
            className="field mono"
            type="number"
            min={1}
            step={1}
            value={intervalText}
            disabled={busy}
            onChange={(event) => setIntervalText(event.target.value)}
          />
        </AdminField>
        {interval === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            Counted from the end of the last automatic run, also a dismissed or
            failed one; requested runs do not count.
          </p>
        )}
        <PolicySwitch
          label="Approval step"
          description="When On, each automatic run waits until someone with Run history archival and purge approves it with a reason of their own, or dismisses it."
          ariaLabel="Approval step"
          on={approvalRequired}
          disabled={busy}
          onToggle={() => setApprovalRequired((on) => !on)}
        />
        {saved.standingAuthorization !== null ? (
          <>
            <p className="admin-fieldhelp">
              {`Standing authorization: ${standingAuthorizationText(saved.standingAuthorization)}`}
            </p>
            {saved.standingAuthorization.problem !== null ? (
              <div className="err">{saved.standingAuthorization.problem}</div>
            ) : null}
          </>
        ) : null}
        {!saved.approvalRequired && approvalRequired ? (
          <p className="admin-fieldhelp">
            Switching the approval step on withdraws the standing authorization:
            a run started without approval stops before it purges any history,
            and each later automatic run waits for approval.
          </p>
        ) : null}
        {!saved.approvalRequired && !approvalRequired ? (
          <label className="admin-check">
            <input
              type="checkbox"
              checked={renew}
              disabled={busy}
              onChange={(event) => setRenew(event.target.checked)}
            />
            <span>Renew the standing authorization under my name</span>
          </label>
        ) : null}
        {authorizing ? (
          <div className="admin-authpanel">
            <p className="admin-authwarn">
              Without the approval step, automatic runs archive and purge
              Movement history without anyone approving them first.
            </p>
            <p className="admin-fieldhelp">
              The standing authorization is recorded under your name with your
              reason. If you are deactivated or lose Run history archival and
              purge, automatic runs stop until someone who holds it saves this
              setting again.
            </p>
            <AdminField
              label={
                <>
                  Reason for letting automatic runs start without approval{' '}
                  <span className="field-required">(required)</span>
                </>
              }
            >
              <textarea
                className="field"
                rows={3}
                maxLength={500}
                value={reason}
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
              />
            </AdminField>
            <div className="typedconfirm">
              <label htmlFor={confirmationId}>
                <span>
                  Type <b>{APPROVAL_OFF_CONFIRMATION}</b> to confirm
                </span>
              </label>
              <input
                id={confirmationId}
                type="text"
                value={confirmation}
                autoComplete="off"
                spellCheck={false}
                placeholder={APPROVAL_OFF_CONFIRMATION}
                disabled={busy}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </div>
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
          disabled={busy || writeBlocked || invalid}
          onClick={() => void submit()}
        >
          Save changes
        </button>
      </div>
      {guard.asking ? (
        <UnsavedChangesDialog
          saveDisabled={writeBlocked || invalid}
          onKeepEditing={guard.keepEditing}
          onSave={() => void submit()}
          onDiscard={() => onClose(wrote)}
        >
          You have unsaved changes to the automatic runs.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}
