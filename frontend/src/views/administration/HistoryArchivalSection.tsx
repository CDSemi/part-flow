import { useState } from 'react';

import { ApiError } from '../../api/client';
import { listArchiveRuns } from '../../api/history-archive';
import { getRetentionPolicy, updateRetentionPolicy } from '../../api/policies';
import type { RetentionPolicy } from '../../api/policies';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import type { ApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { ArchivePreviewCard } from './ArchivePreviewCard';
import { ArchiveRunsCard } from './ArchiveRunsCard';
import { DeletionRequestsCard } from './DeletionRequestsCard';
import { formatRetentionPeriod } from './retention-period';
import {
  AdminField,
  SectionHeader,
  ServerErrorNote,
  SettingsCard,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → History archival & purge (Phase 16, archival design B
// steps 1 and 2; GUI_DESIGN §9, PROJECT_PROFILE §28): the settings of the
// Movement-history archival (`/api/policies/data-retention` — the
// retention period, the shortest allowed period, the Start minute of the
// server's archival job, the preview time limit, the wait limit and the
// purge batch limits), the preview of what a run would archive at a
// cutoff and the request of a run, and the table of runs with Cancel and
// Confirm off-host copy. A run exports the history to archive files and,
// after the off-host copy and a pre-purge backup, purges it from the
// database. Every change needs the Run history archival and purge
// permission (the server checks it); every signed-in user reads the
// settings and the runs, with one view-only line under the header when
// the user may not change them. The server validates every value and
// owns every rule: the section shows its answers as given.
//
// Below the runs, the Permanent deletion requests card (archival design
// D17, build step 3) lists the requests of Delete permanently… with
// Cancel; it is shown only to holders of Delete Work Orders and Part
// Numbers permanently, whom every route of the requests needs.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'data-retention')?.subtitle ??
  '';

const PERMISSION = 'RUN_HISTORY_ARCHIVAL_AND_PURGE';

export function HistoryArchivalSection() {
  const session = useSession();
  const canWrite = session.can(PERMISSION);
  const canDeletePermanently = session.can(
    'DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY',
  );
  const { status } = useConnectivity();
  const [editing, setEditing] = useState(false);
  const writeBlocked = status !== 'connected';
  const policyData = useApiData(getRetentionPolicy);
  const runsData = useApiData(listArchiveRuns);
  const policy =
    policyData.state.status === 'ready' ? policyData.state.data : null;

  return (
    <>
      <SectionHeader title="History archival & purge" subtitle={SUBTITLE} />
      {canWrite ? null : <ViewOnlyNote permission={PERMISSION} />}
      <div className="admin-cards admin-cards-two">
        <SettingsCard
          title="Retention and schedule"
          description="How long Movement history stays in the database, when the server's archival job does its work, and how much it purges at a time."
          edit={
            canWrite && policy
              ? {
                  label: 'Edit retention and schedule…',
                  disabled: writeBlocked,
                  onClick: () => setEditing(true),
                }
              : undefined
          }
        >
          <RetentionSettingsBody
            policyData={policyData}
            editing={editing}
            writeBlocked={writeBlocked}
            onClose={(wrote) => {
              setEditing(false);
              if (wrote) policyData.reload();
            }}
          />
        </SettingsCard>
        {canWrite ? (
          <ArchivePreviewCard
            previewSeconds={policy?.previewSeconds ?? null}
            writeBlocked={writeBlocked}
            onRunsChanged={runsData.reload}
          />
        ) : null}
      </div>
      <div className="admin-cards admin-cards-stack">
        <ArchiveRunsCard
          runsData={runsData}
          canWrite={canWrite}
          writeBlocked={writeBlocked}
        />
        {canDeletePermanently ? (
          <DeletionRequestsCard writeBlocked={writeBlocked} />
        ) : null}
      </div>
    </>
  );
}

/** The months of a period in words: `6 months`, `24 months (2 years)`. */
function formatPeriodValue(months: number): string {
  return months < 12
    ? formatRetentionPeriod(months)
    : `${months} months (${formatRetentionPeriod(months)})`;
}

function RetentionSettingsBody({
  policyData,
  editing,
  writeBlocked,
  onClose,
}: {
  policyData: ApiData<RetentionPolicy>;
  editing: boolean;
  writeBlocked: boolean;
  onClose: (wrote: boolean) => void;
}) {
  const { state } = policyData;
  if (state.status === 'loading') {
    return <LoadingState label="Loading retention and schedule" />;
  }
  if (state.status === 'error') {
    return (
      <ErrorState
        message="The retention and schedule settings could not be loaded."
        detail={state.message}
        onRetry={policyData.reload}
      />
    );
  }
  const policy = state.data;
  return (
    <>
      <div className="admin-configpreview">
        <div className="prow">
          <span className="k">Retention period</span>
          <span className="v">
            {policy.retentionPeriodMonths === null
              ? 'No retention period'
              : formatPeriodValue(policy.retentionPeriodMonths)}
          </span>
        </div>
        <div className="prow">
          <span className="k">Shortest allowed period</span>
          <span className="v">
            {formatPeriodValue(policy.shortestRetentionMonths)}
          </span>
        </div>
        <div className="prow">
          <span className="k">Start minute</span>
          <span className="v">{`${policy.startMinute} minutes past each hour`}</span>
        </div>
        <div className="prow">
          <span className="k">Preview time limit</span>
          <span className="v">{`${policy.previewSeconds} seconds`}</span>
        </div>
        <div className="prow">
          <span className="k">Report a waiting run after</span>
          <span className="v">{`${policy.maxWaitDays} days`}</span>
        </div>
        <div className="prow">
          <span className="k">Movements per purge batch</span>
          <span className="v">
            {policy.purgeBatchMovements.toLocaleString('en-US')}
          </span>
        </div>
        <div className="prow">
          <span className="k">Part Numbers per purge batch</span>
          <span className="v">
            {policy.purgeBatchPartNumbers.toLocaleString('en-US')}
          </span>
        </div>
      </div>
      {editing ? (
        <RetentionSettingsDialog
          saved={policy}
          writeBlocked={writeBlocked}
          onClose={onClose}
        />
      ) : null}
    </>
  );
}

const WHOLE_NUMBER_ERROR = 'Enter a whole number.';
const UNKNOWN_OUTCOME =
  'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.';

/** The whole number in `text`, else null (never rounded; the server
 * checks the range and answers its own message). */
function parseWhole(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) ? value : null;
}

function RetentionSettingsDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: RetentionPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the settings). */
  onClose: (wrote: boolean) => void;
}) {
  const [keepPeriod, setKeepPeriod] = useState(
    saved.retentionPeriodMonths !== null,
  );
  const [periodText, setPeriodText] = useState(
    String(saved.retentionPeriodMonths ?? ''),
  );
  const [shortestText, setShortestText] = useState(
    String(saved.shortestRetentionMonths),
  );
  const [reason, setReason] = useState('');
  const [minuteText, setMinuteText] = useState(String(saved.startMinute));
  const [secondsText, setSecondsText] = useState(String(saved.previewSeconds));
  const [daysText, setDaysText] = useState(String(saved.maxWaitDays));
  const [batchMovementsText, setBatchMovementsText] = useState(
    String(saved.purgeBatchMovements),
  );
  const [batchPartNumbersText, setBatchPartNumbersText] = useState(
    String(saved.purgeBatchPartNumbers),
  );
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const period = keepPeriod ? parseWhole(periodText) : null;
  const shortest = parseWhole(shortestText);
  const minute = parseWhole(minuteText);
  const seconds = parseWhole(secondsText);
  const days = parseWhole(daysText);
  const batchMovements = parseWhole(batchMovementsText);
  const batchPartNumbers = parseWhole(batchPartNumbersText);
  const periodInvalid = keepPeriod && period === null;
  const invalid =
    periodInvalid ||
    shortest === null ||
    minute === null ||
    seconds === null ||
    days === null ||
    batchMovements === null ||
    batchPartNumbers === null;
  const shortestChanged =
    shortest !== null && shortest !== saved.shortestRetentionMonths;
  // Only the valid settings whose value differs from the stored ones.
  const patch: Partial<Omit<RetentionPolicy, 'updatedAt'>> = {
    ...(!periodInvalid && period !== saved.retentionPeriodMonths
      ? { retentionPeriodMonths: period }
      : {}),
    ...(shortestChanged ? { shortestRetentionMonths: shortest } : {}),
    ...(minute !== null && minute !== saved.startMinute
      ? { startMinute: minute }
      : {}),
    ...(seconds !== null && seconds !== saved.previewSeconds
      ? { previewSeconds: seconds }
      : {}),
    ...(days !== null && days !== saved.maxWaitDays
      ? { maxWaitDays: days }
      : {}),
    ...(batchMovements !== null && batchMovements !== saved.purgeBatchMovements
      ? { purgeBatchMovements: batchMovements }
      : {}),
    ...(batchPartNumbers !== null &&
    batchPartNumbers !== saved.purgeBatchPartNumbers
      ? { purgeBatchPartNumbers: batchPartNumbers }
      : {}),
  };
  const changed = Object.keys(patch).length > 0;

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
      // The reason travels with a change of the shortest allowed period
      // only; the server requires it then and answers its own message.
      await updateRetentionPolicy(patch, shortestChanged ? reason : undefined);
      onClose(true);
    } catch (error) {
      setServerError(
        writeOutcomeUnknown(error) || !(error instanceof ApiError)
          ? UNKNOWN_OUTCOME
          : error.message,
      );
      setBusy(false);
    }
  };

  const title = 'Retention and schedule';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="admin-form">
        <label className="admin-check">
          <input
            type="radio"
            name="retention-period-choice"
            checked={!keepPeriod}
            disabled={busy}
            onChange={() => setKeepPeriod(false)}
          />
          <span>No retention period</span>
        </label>
        <label className="admin-check">
          <input
            type="radio"
            name="retention-period-choice"
            checked={keepPeriod}
            disabled={busy}
            onChange={() => setKeepPeriod(true)}
          />
          <span>Keep a set period of history</span>
        </label>
        {keepPeriod ? (
          <>
            <AdminField label="Retention period (months)">
              <input
                className="field mono"
                type="number"
                step={1}
                value={periodText}
                disabled={busy}
                onChange={(event) => setPeriodText(event.target.value)}
              />
            </AdminField>
            {period === null ? (
              <div className="err" role="alert">
                {WHOLE_NUMBER_ERROR}
              </div>
            ) : (
              <p className="admin-fieldhelp">
                {`Movement history older than ${formatRetentionPeriod(period)} can be archived.`}
              </p>
            )}
          </>
        ) : (
          <p className="admin-fieldhelp">
            Nothing is eligible for archival without a retention period.
          </p>
        )}
        <AdminField label="Shortest allowed period (months)">
          <input
            className="field mono"
            type="number"
            step={1}
            value={shortestText}
            disabled={busy}
            onChange={(event) => setShortestText(event.target.value)}
          />
        </AdminField>
        {shortest === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : null}
        {shortestChanged ? (
          <AdminField
            label={
              <>
                Reason for changing the shortest allowed period{' '}
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
        ) : null}
        <AdminField label="Start minute (0–59)">
          <input
            className="field mono"
            type="number"
            min={0}
            max={59}
            step={1}
            value={minuteText}
            disabled={busy}
            onChange={(event) => setMinuteText(event.target.value)}
          />
        </AdminField>
        {minute === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            The archival job on the server does its hourly work from this
            minute.
          </p>
        )}
        <AdminField label="Preview time limit (seconds)">
          <input
            className="field mono"
            type="number"
            step={1}
            value={secondsText}
            disabled={busy}
            onChange={(event) => setSecondsText(event.target.value)}
          />
        </AdminField>
        {seconds === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            Keep it below the wait of every proxy in front of PartFlow: through
            Cloudflare a request ends after 100 seconds.
          </p>
        )}
        <AdminField label="Report a waiting run after (days)">
          <input
            className="field mono"
            type="number"
            step={1}
            value={daysText}
            disabled={busy}
            onChange={(event) => setDaysText(event.target.value)}
          />
        </AdminField>
        {days === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            When a run waits longer than this for its off-host copy to be
            confirmed, the server's monitoring check reports it.
          </p>
        )}
        <AdminField label="Movements per purge batch">
          <input
            className="field mono"
            type="number"
            step={1}
            value={batchMovementsText}
            disabled={busy}
            onChange={(event) => setBatchMovementsText(event.target.value)}
          />
        </AdminField>
        {batchMovements === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            The purge deletes whole components, in batches of at most this many
            Movements; a larger component is a batch of its own.
          </p>
        )}
        <AdminField label="Part Numbers per purge batch">
          <input
            className="field mono"
            type="number"
            step={1}
            value={batchPartNumbersText}
            disabled={busy}
            onChange={(event) => setBatchPartNumbersText(event.target.value)}
          />
        </AdminField>
        {batchPartNumbers === null ? (
          <div className="err" role="alert">
            {WHOLE_NUMBER_ERROR}
          </div>
        ) : (
          <p className="admin-fieldhelp">
            A batch locks at most this many Part Numbers; work on them waits
            until the batch ends.
          </p>
        )}
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
          You have unsaved changes to the retention and schedule settings.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}
