import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  approveArchiveRun,
  cancelArchiveRun,
  confirmArchiveCopy,
  dismissArchiveRun,
} from '../../api/history-archive';
import type { ArchiveRun } from '../../api/history-archive';
import { writeOutcomeUnknown } from '../../api/scan-station';
import type { ApiData } from '../../api/use-api-data';
import { useCloseGuard } from '../../components/close-guard';
import { DiscardInputDialog } from '../../components/close-guard-dialogs';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatIsoDate } from '../dates';
import {
  componentsAndFlows,
  countsLine,
  eligibleRange,
  formatArchiveSize,
  formatInstant,
  latestRunStep,
  movementsText,
  PRE_PURGE_BACKUP_LABEL,
  purgedLine,
  runAwaitsApproval,
  runAwaitsCopyConfirmation,
  runCanBeCancelled,
  runCounts,
  runEffectText,
  runOutcome,
  runRequester,
  runStateLabel,
  runStateTone,
  runTimes,
  startedWithoutApprovalText,
} from './archive-runs';
import { AdminField, ServerErrorNote, SettingsCard } from './section-widgets';

// Administration → History archival & purge → Runs (archival design B
// steps 1, 2 and 4): every run, newest first, with its state in plain
// words, the cutoff with the previewed, exported, purged and skipped
// counts, who requested it and why — or Automatic, with the approval or
// the standing authorization it started under —, its times, and what it
// came to with its archive, the pre-purge backup and the purge receipt.
// Six columns keep the table inside its card down to a 1024 px window
// beside the Administration sidebar, as the requests card below does: the
// actions sit under the state they apply to, the cutoff tops the counts
// and the archive follows the result. A user who may run history
// archival approves a proposed run with a reason of their own or
// dismisses it (no reason, after a confirmation), cancels a requested run
// or one waiting for its off-host copy (after a confirmation) — never a
// proposed one, nor one the job works on — and confirms the off-host copy
// of a run waiting for it against the manifest SHA-256 shown; the job
// then takes the pre-purge backup and purges. The server decides every
// transition and its refusals are shown as given; an action whose answer
// was lost reloads the runs and says so (every action is safe to repeat).

type Notice =
  { kind: 'done'; text: string } | { kind: 'unknown'; text: string } | null;

type RunAction = 'approve' | 'dismiss' | 'cancel' | 'confirm-copy';

export function ArchiveRunsCard({
  runsData,
  canWrite,
  writeBlocked,
}: {
  runsData: ApiData<ArchiveRun[]>;
  canWrite: boolean;
  writeBlocked: boolean;
}) {
  const [acting, setActing] = useState<{
    action: RunAction;
    run: ArchiveRun;
  } | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const { state } = runsData;

  const finished = (next: Notice) => {
    setActing(null);
    setNotice(next);
    runsData.reload();
  };
  const dialogProps = {
    writeBlocked,
    onCancel: () => setActing(null),
    onFinished: finished,
  };

  return (
    <SettingsCard
      title="Runs"
      description="Each run exports the Movement history before its cutoff to archive files on the server and, after the off-host copy and a pre-purge backup, purges it from the database."
      note="Archived commands can no longer be undone."
      action={
        <button
          type="button"
          className="admin-editbtn"
          aria-label="Reload runs"
          disabled={state.status === 'loading'}
          onClick={() => {
            setNotice(null);
            runsData.reload();
          }}
        >
          Reload
        </button>
      }
    >
      {notice?.kind === 'done' ? (
        <div className="admin-savednote" role="status">
          {`✓ ${notice.text}`}
        </div>
      ) : null}
      <ServerErrorNote
        message={notice?.kind === 'unknown' ? notice.text : null}
      />
      {state.status === 'loading' ? (
        <LoadingState label="Loading runs" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The archival runs could not be loaded."
          detail={state.message}
          onRetry={runsData.reload}
        />
      ) : null}
      {state.status === 'ready' && state.data.length === 0 ? (
        <EmptyState message="No archival run has been requested yet." />
      ) : null}
      {state.status === 'ready' && state.data.length > 0 ? (
        <table className="admin-table admin-runtable">
          <thead>
            <tr>
              <th>Run</th>
              <th>State</th>
              <th className="admin-runcounts">Cutoff and counts</th>
              <th>Requested by</th>
              <th>Times</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {state.data.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                canWrite={canWrite}
                writeBlocked={writeBlocked}
                onAction={(action) => {
                  setNotice(null);
                  setActing({ action, run });
                }}
              />
            ))}
          </tbody>
        </table>
      ) : null}
      {acting?.action === 'approve' ? (
        <ApproveRunDialog run={acting.run} {...dialogProps} />
      ) : null}
      {acting?.action === 'dismiss' ? (
        <DismissRunDialog run={acting.run} {...dialogProps} />
      ) : null}
      {acting?.action === 'cancel' ? (
        <CancelRunDialog run={acting.run} {...dialogProps} />
      ) : null}
      {acting?.action === 'confirm-copy' ? (
        <ConfirmCopyDialog run={acting.run} {...dialogProps} />
      ) : null}
    </SettingsCard>
  );
}

function RunRow({
  run,
  canWrite,
  writeBlocked,
  onAction,
}: {
  run: ArchiveRun;
  canWrite: boolean;
  writeBlocked: boolean;
  onAction: (action: RunAction) => void;
}) {
  const skipped =
    run.skipped !== null &&
    (run.skipped.components > 0 || run.skipped.flows > 0)
      ? run.skipped
      : null;
  const counts = runCounts(run);
  const purged = purgedLine(run);
  const latest = latestRunStep(run);
  const times = runTimes(run);
  // Requested and the latest step are shown; the rest only under All times.
  const hiddenTimes = times.length - (latest === null ? 1 : 2);
  const withoutApproval = startedWithoutApprovalText(run);
  const outcome = runOutcome(run);
  const actionButton = (action: RunAction, label: string, name: string) => (
    <button
      type="button"
      className="admin-btn"
      aria-label={name}
      disabled={writeBlocked}
      onClick={() => onAction(action)}
    >
      {label}
    </button>
  );
  return (
    <tr>
      <td data-label="Run" className="mono">
        {run.id}
      </td>
      <td data-label="State">
        {/* The actions sit under the state they apply to. */}
        <div className="admin-runactions">
          <span className={`pillnav ${runStateTone(run)}`}>
            {runStateLabel(run)}
          </span>
          {canWrite && runAwaitsApproval(run) ? (
            <>
              {actionButton('approve', 'Approve…', `Approve run ${run.id}…`)}
              {actionButton('dismiss', 'Dismiss…', `Dismiss run ${run.id}…`)}
            </>
          ) : null}
          {canWrite && runAwaitsCopyConfirmation(run)
            ? actionButton(
                'confirm-copy',
                'Confirm copy…',
                `Confirm the off-host copy of run ${run.id}…`,
              )
            : null}
          {canWrite && runCanBeCancelled(run)
            ? actionButton('cancel', 'Cancel…', `Cancel run ${run.id}…`)
            : null}
        </div>
      </td>
      <td data-label="Cutoff and counts">
        <div className="admin-runlines">
          <span className="admin-runwhen">{formatIsoDate(run.cutoffDate)}</span>
          <span className="admin-runmain">
            {movementsText(counts.shown.movements)}
          </span>
          <span className="admin-runsub">
            {componentsAndFlows(counts.shown)}
          </span>
          {counts.previewLine === 'only' ? (
            <span className="admin-runsub">From the preview</span>
          ) : null}
          {counts.previewLine === 'differs' ? (
            <span className="admin-runsub">{`Preview: ${movementsText(run.preview.movements)} · ${componentsAndFlows(run.preview)}`}</span>
          ) : null}
          {purged !== null ? (
            <span className="admin-runsub">{purged}</span>
          ) : null}
          {skipped !== null ? (
            <span className="admin-runsub">{`Skipped (changed during the run): ${componentsAndFlows(skipped)}`}</span>
          ) : null}
        </div>
      </td>
      <td data-label="Requested by">
        <div className="admin-runlines">
          <span>{runRequester(run)}</span>
          <span className="admin-runreason">{run.reason}</span>
          {run.approval !== null ? (
            <>
              <span className="admin-runtime">
                {`Approved by ${run.approval.approvedBy.displayName}, `}
                <span className="admin-runwhen">
                  {formatInstant(run.approval.approvedAt)}
                </span>
              </span>
              <span className="admin-runreason">{run.approval.reason}</span>
            </>
          ) : null}
          {withoutApproval !== null ? (
            <span className="admin-runreason">{withoutApproval}</span>
          ) : null}
        </div>
      </td>
      <td data-label="Times">
        <div className="admin-runlines">
          <RunTime label="Requested" at={run.requestedAt} />
          {latest !== null ? (
            <RunTime label={latest.label} at={latest.at} />
          ) : null}
          {hiddenTimes > 0 ? (
            <details className="admin-runtimes">
              <summary>All times</summary>
              <div className="admin-runlines">
                {times.map((time) => (
                  <RunTime key={time.label} label={time.label} at={time.at} />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      </td>
      <td data-label="Result">
        {outcome === null && run.archiveName === null ? (
          '—'
        ) : (
          <div className="admin-runlines">
            {outcome !== null ? (
              <span className="admin-runresult">{outcome}</span>
            ) : null}
            {run.archiveName !== null ? (
              <span className="admin-runarchive">{run.archiveName}</span>
            ) : null}
            {run.archiveBytes !== null ? (
              <span className="admin-runsub">
                {formatArchiveSize(run.archiveBytes)}
              </span>
            ) : null}
            {/* The purge's evidence, each folded like All times so a
                typical run keeps its few lines. */}
            {run.prePurgeBackup !== null ? (
              <details className="admin-runevidence">
                <summary>{PRE_PURGE_BACKUP_LABEL}</summary>
                <div className="admin-runlines">
                  <span className="admin-runarchive">
                    {run.prePurgeBackup.name}
                  </span>
                </div>
              </details>
            ) : null}
            {run.receiptName !== null ? (
              <details className="admin-runevidence">
                <summary>Purge receipt</summary>
                <div className="admin-runlines">
                  <span className="admin-runarchive">{run.receiptName}</span>
                  {run.receiptSha256 !== null ? (
                    <span className="admin-runarchive">{`SHA-256 ${run.receiptSha256}`}</span>
                  ) : null}
                </div>
              </details>
            ) : null}
          </div>
        )}
      </td>
    </tr>
  );
}

/** One time of a run: its label, then the date and time, which never
 * break apart. */
function RunTime({ label, at }: { label: string; at: string }) {
  return (
    <span className="admin-runtime">
      {label} <span className="admin-runwhen">{formatInstant(at)}</span>
    </span>
  );
}

interface RunDialogProps {
  run: ArchiveRun;
  writeBlocked: boolean;
  onCancel: () => void;
  onFinished: (notice: Notice) => void;
}

function ApproveRunDialog({
  run,
  writeBlocked,
  onCancel,
  onFinished,
}: RunDialogProps) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const guard = useCloseGuard({
    dirty: reason.trim() !== '',
    busy,
    onClose: onCancel,
  });

  const submit = async () => {
    if (busy || writeBlocked) return;
    setBusy(true);
    setServerError(null);
    try {
      // The server checks the reason and the run's state, and answers its
      // own message.
      await approveArchiveRun(run.id, reason);
      onFinished({
        kind: 'done',
        text: `Run ${run.id} is approved. The archival job on the server starts it at its next Start minute.`,
      });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        onFinished({
          kind: 'unknown',
          text: `The server did not answer, so run ${run.id} may or may not have been approved. Check its state in the table; approving it again is safe.`,
        });
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  const title = `Approve run ${run.id}`;
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <p className="admin-idnote">{runEffectText(run.cutoffDate)}</p>
      <div className="admin-identity">
        <div className="idrow">
          <span className="k">Proposed because</span>
          <span className="v">{run.reason}</span>
        </div>
        <div className="idrow">
          <span className="k">Cutoff date</span>
          <span className="v">{formatIsoDate(run.cutoffDate)}</span>
        </div>
        <div className="idrow">
          <span className="k">Preview</span>
          <span className="v">{countsLine(run.preview)}</span>
        </div>
        <div className="idrow">
          <span className="k">Movements from</span>
          <span className="v">{eligibleRange(run.preview)}</span>
        </div>
        <div className="idrow">
          <span className="k">Estimated size</span>
          <span className="v">
            {formatArchiveSize(run.preview.estimatedArchiveBytes)}
          </span>
        </div>
      </div>
      <div className="admin-form">
        <AdminField
          label={
            <>
              Reason for approving{' '}
              <span className="field-required">(required)</span>
            </>
          }
        >
          <textarea
            className="field"
            rows={3}
            maxLength={500}
            placeholder="Why should this run go ahead?"
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </AdminField>
        <p className="admin-fieldhelp">
          The archival job on the server starts the run at its next Start
          minute. Before the pre-purge backup and before the purge, it checks
          that you are still active and still hold Run history archival and
          purge.
        </p>
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
          disabled={busy || writeBlocked}
          onClick={() => void submit()}
        >
          Approve run
        </button>
      </div>
      {guard.asking ? (
        <DiscardInputDialog
          title={`Discard the approval of run ${run.id}?`}
          onKeepEditing={guard.keepEditing}
          onDiscard={onCancel}
        />
      ) : null}
    </ModalDialog>
  );
}

function DismissRunDialog({
  run,
  writeBlocked,
  onCancel,
  onFinished,
}: RunDialogProps) {
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const confirm = async () => {
    if (busy || writeBlocked) return;
    setBusy(true);
    setServerError(null);
    try {
      await dismissArchiveRun(run.id);
      onFinished({ kind: 'done', text: `Run ${run.id} is dismissed.` });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        onFinished({
          kind: 'unknown',
          text: `The server did not answer, so run ${run.id} may or may not have been dismissed. Check its state in the table; dismissing it again is safe.`,
        });
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`Dismiss run ${run.id}?`}
      tone="danger"
      cancelLabel="Keep run (Esc)"
      confirmLabel="Dismiss run"
      confirmDisabled={writeBlocked || busy}
      onConfirm={() => void confirm()}
      onCancel={busy ? () => undefined : onCancel}
    >
      The run will not start, and the Movement history stays in the database.
      The days between automatic runs count from this dismissal.
      <ServerErrorNote message={serverError} />
    </ConfirmDialog>
  );
}

function CancelRunDialog({
  run,
  writeBlocked,
  onCancel,
  onFinished,
}: RunDialogProps) {
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const confirm = async () => {
    if (busy || writeBlocked) return;
    setBusy(true);
    setServerError(null);
    try {
      await cancelArchiveRun(run.id);
      onFinished({ kind: 'done', text: `Run ${run.id} is cancelled.` });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        onFinished({
          kind: 'unknown',
          text: `The server did not answer, so run ${run.id} may or may not have been cancelled. Check its state in the table; cancelling it again is safe.`,
        });
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`Cancel run ${run.id}?`}
      tone="danger"
      cancelLabel="Keep run (Esc)"
      confirmLabel="Cancel run"
      confirmDisabled={writeBlocked || busy}
      onConfirm={() => void confirm()}
      onCancel={busy ? () => undefined : onCancel}
    >
      {run.state === 'WAITING_FOR_COPY'
        ? 'The run ends without completing. Its archive folder stays on the server, and the Movement history stays in the database. To archive this history later, request a new run.'
        : 'The run will not start, and the Movement history stays in the database. To archive this history later, request a new run.'}
      <ServerErrorNote message={serverError} />
    </ConfirmDialog>
  );
}

function ConfirmCopyDialog({
  run,
  writeBlocked,
  onCancel,
  onFinished,
}: RunDialogProps) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const sha256 = run.manifestSha256 ?? '';

  const confirm = async () => {
    if (busy || writeBlocked || !copied) return;
    setBusy(true);
    setServerError(null);
    try {
      await confirmArchiveCopy(run.id, sha256);
      onFinished({
        kind: 'done',
        text: `The off-host copy of run ${run.id} is confirmed. At its next start the archival job on the server takes the pre-purge backup and purges the archived history.`,
      });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        onFinished({
          kind: 'unknown',
          text: `The server did not answer, so the off-host copy of run ${run.id} may or may not have been confirmed. Check its state in the table; confirming it again is safe.`,
        });
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  const title = `Confirm the off-host copy of run ${run.id}`;
  return (
    <ModalDialog label={title} onClose={busy ? () => undefined : onCancel}>
      <h3>{title}</h3>
      <p className="admin-idnote">
        First copy this run&apos;s folder from the server&apos;s archive folder
        to storage off the server, for example by adding the archive folder to
        the Hyper Backup task, and check that the copy is complete.
      </p>
      <div className="admin-identity">
        <div className="idrow">
          <span className="k">Archive folder</span>
          <span className="v admin-runarchive">{run.archiveName}</span>
        </div>
        <div className="idrow">
          <span className="k">Manifest SHA-256</span>
          <span className="v admin-runarchive">{sha256}</span>
        </div>
      </div>
      <div className="admin-form">
        <label className="admin-check">
          <input
            type="checkbox"
            checked={copied}
            disabled={busy}
            onChange={(event) => setCopied(event.target.checked)}
          />
          <span>The archive folder is copied off the server</span>
        </label>
        <p className="admin-fieldhelp">
          At its next start the archival job on the server then takes the
          pre-purge backup and purges the archived history from the database.
        </p>
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked || !copied}
          onClick={() => void confirm()}
        >
          Confirm copy
        </button>
      </div>
    </ModalDialog>
  );
}
