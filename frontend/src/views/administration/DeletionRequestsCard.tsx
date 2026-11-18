import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  cancelDeletionRequest,
  listDeletionRequests,
} from '../../api/permanent-deletion';
import type { DeletionRequest } from '../../api/permanent-deletion';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import {
  deletionOutcome,
  deletionRequestCancellable,
  deletionRequestTimes,
  deletionStateLabel,
  deletionStateTone,
  latestDeletionStep,
  requesterName,
  rowsPerTable,
  scopeRecords,
  totalRowsText,
  unnamedWorkOrdersLine,
  WHOLE_NAME_MAX_LENGTH,
} from '../../components/permanent-deletion';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatInstant, PRE_PURGE_BACKUP_LABEL } from './archive-runs';
import { ServerErrorNote, SettingsCard } from './section-widgets';

// Administration → History archival & purge → Permanent deletion requests
// (Phase 16, archival design D17; archival build step 3): shown only to
// holders of Delete Work Orders and Part Numbers permanently, whom every
// route of the requests needs. Every request, newest first, with its state
// in plain words, what it names and the rows its plan deletes, who
// requested it (a User, or an operator on the server) and why, its times,
// and what it came to — the failure reason as the server gives it — with
// the pre-purge backup. Six columns keep the table inside its card down to
// a 1024 px window beside the Administration sidebar: Cancel… sits under
// the state it applies to, and the rows per table and the backup are
// folded. Prose wraps only between words; a Work Order Number or Part
// Number stays whole unless it is too long for a line. A request that
// waits for the server may be cancelled after a confirmation; the server
// decides and its refusal is shown as given, and a cancellation whose
// answer was lost reloads the requests and says so (cancelling again is
// safe).

type Notice =
  { kind: 'done'; text: string } | { kind: 'unknown'; text: string } | null;

export function DeletionRequestsCard({
  writeBlocked,
}: {
  writeBlocked: boolean;
}) {
  const requestsData = useApiData(listDeletionRequests);
  const [cancelling, setCancelling] = useState<DeletionRequest | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const { state } = requestsData;

  const finished = (next: Notice) => {
    setCancelling(null);
    setNotice(next);
    requestsData.reload();
  };

  return (
    <SettingsCard
      title="Permanent deletion requests"
      description="Each request deletes Work Orders or a Part Number with all their data from the database, as if they had never been entered; the server carries it out after a pre-purge backup."
      note="Delete permanently… in Work Order Details and in the Part Number dialog of Management → Part Numbers requests a deletion."
      action={
        <button
          type="button"
          className="admin-editbtn"
          aria-label="Reload deletion requests"
          disabled={state.status === 'loading'}
          onClick={() => {
            setNotice(null);
            requestsData.reload();
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
        <LoadingState label="Loading permanent deletion requests" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The permanent deletion requests could not be loaded."
          detail={state.message}
          onRetry={requestsData.reload}
        />
      ) : null}
      {state.status === 'ready' && state.data.length === 0 ? (
        <EmptyState message="No permanent deletion has been requested yet." />
      ) : null}
      {state.status === 'ready' && state.data.length > 0 ? (
        <table className="admin-table admin-runtable">
          <thead>
            <tr>
              <th>Request</th>
              <th>State</th>
              <th>Deletes</th>
              <th>Requested by</th>
              <th>Times</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {state.data.map((request) => (
              <RequestRow
                key={request.id}
                request={request}
                writeBlocked={writeBlocked}
                onCancel={() => {
                  setNotice(null);
                  setCancelling(request);
                }}
              />
            ))}
          </tbody>
        </table>
      ) : null}
      {cancelling !== null ? (
        <CancelRequestDialog
          request={cancelling}
          writeBlocked={writeBlocked}
          onCancel={() => setCancelling(null)}
          onFinished={finished}
        />
      ) : null}
    </SettingsCard>
  );
}

function RequestRow({
  request,
  writeBlocked,
  onCancel,
}: {
  request: DeletionRequest;
  writeBlocked: boolean;
  onCancel: () => void;
}) {
  const unnamed = unnamedWorkOrdersLine(request.workOrders);
  const latest = latestDeletionStep(request);
  const times = deletionRequestTimes(request);
  // Requested and the latest step are shown; the rest only under All times.
  const hiddenTimes = times.length - (latest === null ? 1 : 2);
  return (
    <tr>
      <td data-label="Request" className="mono">
        {request.id}
      </td>
      <td data-label="State">
        <div className="admin-runactions">
          <span className={`pillnav ${deletionStateTone(request.state)}`}>
            {deletionStateLabel(request.state)}
          </span>
          {deletionRequestCancellable(request) ? (
            <button
              type="button"
              className="admin-btn"
              aria-label={`Cancel deletion request ${request.id}…`}
              disabled={writeBlocked}
              onClick={onCancel}
            >
              Cancel…
            </button>
          ) : null}
        </div>
      </td>
      <td data-label="Deletes">
        <div className="admin-runlines">
          {scopeRecords(request.scope).map(({ kind, name }) => (
            <span key={`${kind}:${name}`} className="admin-runscope">
              {`${kind} `}
              <span
                className={
                  name.length > WHOLE_NAME_MAX_LENGTH
                    ? 'admin-runname long'
                    : 'admin-runname'
                }
              >
                {name}
              </span>
            </span>
          ))}
          {unnamed !== null ? (
            <span className="admin-runsub">{unnamed}</span>
          ) : null}
          <span className="admin-runsub">{totalRowsText(request.rows)}</span>
          <details className="admin-runevidence">
            <summary>Rows per table</summary>
            <div className="admin-runlines">
              {rowsPerTable(request.rows).map((row) => (
                <span
                  key={row.table}
                  className="admin-runsub"
                >{`${row.label}: ${row.count.toLocaleString('en-US')}`}</span>
              ))}
            </div>
          </details>
        </div>
      </td>
      <td data-label="Requested by">
        <div className="admin-runlines">
          <span>{requesterName(request)}</span>
          <span className="admin-runreason">{request.reason}</span>
        </div>
      </td>
      <td data-label="Times">
        <div className="admin-runlines">
          <RequestTime label="Requested" at={request.requestedAt} />
          {latest !== null ? (
            <RequestTime label={latest.label} at={latest.at} />
          ) : null}
          {hiddenTimes > 0 ? (
            <details className="admin-runtimes">
              <summary>All times</summary>
              <div className="admin-runlines">
                {times.map((time) => (
                  <RequestTime
                    key={time.label}
                    label={time.label}
                    at={time.at}
                  />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      </td>
      <td data-label="Result">
        <div className="admin-runlines">
          <span className="admin-runresult">
            {deletionOutcome(request) ?? '—'}
          </span>
          {/* The backup's evidence is folded like the runs' archive. */}
          {request.prePurgeBackup !== null ? (
            <details className="admin-runevidence">
              <summary>{PRE_PURGE_BACKUP_LABEL}</summary>
              <div className="admin-runlines">
                <span className="admin-runarchive">
                  {request.prePurgeBackup.name}
                </span>
              </div>
            </details>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/** One time of a request: its label, then the date and time, which never
 * break apart. */
function RequestTime({ label, at }: { label: string; at: string }) {
  return (
    <span className="admin-runtime">
      {label} <span className="admin-runwhen">{formatInstant(at)}</span>
    </span>
  );
}

function CancelRequestDialog({
  request,
  writeBlocked,
  onCancel,
  onFinished,
}: {
  request: DeletionRequest;
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
      await cancelDeletionRequest(request.id);
      onFinished({
        kind: 'done',
        text: `Deletion request ${request.id} is cancelled. Nothing was deleted.`,
      });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        onFinished({
          kind: 'unknown',
          text: `The server did not answer, so deletion request ${request.id} may or may not have been cancelled. Check its state in the table; cancelling it again is safe.`,
        });
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`Cancel deletion request ${request.id}?`}
      cancelLabel="Keep request (Esc)"
      confirmLabel="Cancel request"
      confirmDisabled={writeBlocked || busy}
      onConfirm={() => void confirm()}
      onCancel={busy ? () => undefined : onCancel}
    >
      Nothing is deleted, and the records stay in the database. To delete them
      later, request the deletion again.
      <ServerErrorNote message={serverError} />
    </ConfirmDialog>
  );
}
