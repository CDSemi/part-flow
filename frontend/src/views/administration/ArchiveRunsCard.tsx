import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  cancelArchiveRun,
  confirmArchiveCopy,
} from '../../api/history-archive';
import type { ArchiveRun } from '../../api/history-archive';
import { writeOutcomeUnknown } from '../../api/scan-station';
import type { ApiData } from '../../api/use-api-data';
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
  formatArchiveSize,
  formatInstant,
  latestRunStep,
  movementsText,
  PRE_PURGE_BACKUP_LABEL,
  purgedLine,
  runAwaitsCopyConfirmation,
  runCanBeCancelled,
  runCounts,
  runOutcome,
  runStateLabel,
  runStateTone,
  runTimes,
} from './archive-runs';
import { ServerErrorNote, SettingsCard } from './section-widgets';

// Administration → History archival & purge → Runs (archival design B
// steps 1 and 2): every run, newest first, with its state in plain words,
// the cutoff, the previewed, exported, purged and skipped counts, who
// requested it and why, its times, its archive with the pre-purge backup
// and the purge receipt, and what it came to. A user who may run history
// archival cancels a requested run or one waiting for its off-host copy
// (after a confirmation) — never one the job works on — and confirms the
// off-host copy of a run waiting for it against the manifest SHA-256
// shown; the job then takes the pre-purge backup and purges. The server
// decides every transition and its refusals are shown as given; an action
// whose answer was lost reloads the runs and says so (both actions are
// safe to repeat).

type Notice =
  { kind: 'done'; text: string } | { kind: 'unknown'; text: string } | null;

export function ArchiveRunsCard({
  runsData,
  canWrite,
  writeBlocked,
}: {
  runsData: ApiData<ArchiveRun[]>;
  canWrite: boolean;
  writeBlocked: boolean;
}) {
  const [cancelling, setCancelling] = useState<ArchiveRun | null>(null);
  const [confirming, setConfirming] = useState<ArchiveRun | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const { state } = runsData;

  const finished = (next: Notice) => {
    setCancelling(null);
    setConfirming(null);
    setNotice(next);
    runsData.reload();
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
              <th>Cutoff date</th>
              <th>Counts</th>
              <th>Requested by</th>
              <th>Times</th>
              <th>Archive</th>
              <th>Result</th>
              {canWrite ? <th>Actions</th> : null}
            </tr>
          </thead>
          <tbody>
            {state.data.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                canWrite={canWrite}
                writeBlocked={writeBlocked}
                onCancel={() => {
                  setNotice(null);
                  setCancelling(run);
                }}
                onConfirmCopy={() => {
                  setNotice(null);
                  setConfirming(run);
                }}
              />
            ))}
          </tbody>
        </table>
      ) : null}
      {cancelling !== null ? (
        <CancelRunDialog
          run={cancelling}
          writeBlocked={writeBlocked}
          onCancel={() => setCancelling(null)}
          onFinished={finished}
        />
      ) : null}
      {confirming !== null ? (
        <ConfirmCopyDialog
          run={confirming}
          writeBlocked={writeBlocked}
          onCancel={() => setConfirming(null)}
          onFinished={finished}
        />
      ) : null}
    </SettingsCard>
  );
}

function RunRow({
  run,
  canWrite,
  writeBlocked,
  onCancel,
  onConfirmCopy,
}: {
  run: ArchiveRun;
  canWrite: boolean;
  writeBlocked: boolean;
  onCancel: () => void;
  onConfirmCopy: () => void;
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
  return (
    <tr>
      <td data-label="Run" className="mono">
        {run.id}
      </td>
      <td data-label="State">
        <span className={`pillnav ${runStateTone(run)}`}>
          {runStateLabel(run)}
        </span>
      </td>
      <td data-label="Cutoff date">{formatIsoDate(run.cutoffDate)}</td>
      <td data-label="Counts">
        <div className="admin-runlines">
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
          <span>{run.requestedBy.displayName}</span>
          <span className="admin-runreason">{run.reason}</span>
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
      <td data-label="Archive">
        {run.archiveName === null ? (
          '—'
        ) : (
          <div className="admin-runlines">
            <span className="admin-runarchive">{run.archiveName}</span>
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
      <td data-label="Result">{runOutcome(run) ?? '—'}</td>
      {canWrite ? (
        <td data-label="Actions">
          <div className="admin-runactions">
            {runAwaitsCopyConfirmation(run) ? (
              <button
                type="button"
                className="admin-btn"
                aria-label={`Confirm the off-host copy of run ${run.id}…`}
                disabled={writeBlocked}
                onClick={onConfirmCopy}
              >
                Confirm copy…
              </button>
            ) : null}
            {runCanBeCancelled(run) ? (
              <button
                type="button"
                className="admin-btn"
                aria-label={`Cancel run ${run.id}…`}
                disabled={writeBlocked}
                onClick={onCancel}
              >
                Cancel…
              </button>
            ) : null}
          </div>
        </td>
      ) : null}
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

function CancelRunDialog({
  run,
  writeBlocked,
  onCancel,
  onFinished,
}: {
  run: ArchiveRun;
  writeBlocked: boolean;
  onCancel: () => void;
  onFinished: (notice: Notice) => void;
}) {
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
}: {
  run: ArchiveRun;
  writeBlocked: boolean;
  onCancel: () => void;
  onFinished: (notice: Notice) => void;
}) {
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
