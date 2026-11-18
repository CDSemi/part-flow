import './DeletePermanentlyDialog.css';

import { useContext, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { errorMessage, refusalFlag } from '../api/client';
import {
  cancelDeletionRequest,
  deletionRefusals,
  listDeletionRequests,
  previewDeletion,
  requestDeletion,
} from '../api/permanent-deletion';
import type {
  DeletionPreview,
  DeletionRefusal,
  DeletionRequest,
  DeletionTarget,
} from '../api/permanent-deletion';
import { writeOutcomeUnknown } from '../api/scan-station';
import { RetryFailedLoadsContext } from '../api/use-api-data';
import { formatInstant } from '../views/administration/archive-runs';
import { useCloseGuard } from './close-guard';
import { DiscardInputDialog } from './close-guard-dialogs';
import { ModalDialog } from './ModalDialog';
import {
  DELETION_POLL_LIMIT,
  DELETION_POLL_MS,
  DELETION_WAIT_NOTE,
  deletionRequestCancellable,
  deletionRequestIsOpen,
  deletionStateLabel,
  deletionStateTone,
  keptAuditRowText,
  requestNamesOnly,
  requesterName,
  rowsPerTable,
  scopeLines,
  workOrderLinesText,
} from './permanent-deletion';
import type { PermanentDeletionRecord } from './permanent-deletion';
import { ErrorState, LoadingState } from './view-states';

// Delete permanently… (Phase 16, history archival design D17; archival
// build step 3; GUI_DESIGN §11.2 and §14.2): ONE dialog for the Work Order
// of Work Order Details and the Part Number of the Part Number dialog.
//
// It reads what the deletion would delete — the Work Orders and Part
// Numbers, the rows per table and the audit rows kept with the reason each
// is kept — or every refusal as the server words it, or the open request
// that blocks a new one. A request needs a reason and the Work Order Number
// or the Part Number typed again; both travel as entered and the server
// checks them, so its 409 and 422 texts are shown exactly. The request is
// recorded APPROVED and the server's history archive task carries it out
// after a pre-purge backup: while the dialog is open and the request is
// open, the dialog reads its state again every few seconds, offers Cancel
// while it waits, and once it is COMPLETED the record no longer exists —
// any close of the dialog then hands back to the host (`onDeleted`). An
// open request that names exactly this record is followed the same way.
// A read refused because the user's sign-in is not usable pauses the
// reading, as the monitoring feeds do (each refusal would reopen the
// Sign-in dialog the user may have closed), until the area signals that
// the same user's sign-in is usable again. The web app only records the
// request; it never deletes anything itself.

type PreviewState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; preview: DeletionPreview };

/** A refused request or cancellation: the server's message, and the
 * refusals of a deletion its plan refuses. */
interface Failure {
  message: string;
  refusals: DeletionRefusal[] | null;
}

const REQUEST_UNKNOWN =
  'The server did not answer, so the deletion may or may not have been requested. What would be deleted is read again above, with the request if there is one.';
const CANCEL_UNKNOWN =
  'The server did not answer, so the request may or may not have been cancelled. Its state is read again every few seconds; cancelling it again is safe.';

/** The words of the dialog for its one record. */
function copyFor(record: PermanentDeletionRecord) {
  if (record.kind === 'work-order') {
    const number = record.workOrderNumber;
    return {
      title: 'Delete Work Order permanently',
      name: number,
      typedLabel: 'Work Order Number',
      intro: `This removes Work Order ${number} and all its data from the database, as if it had never been entered. The server takes a pre-purge backup first.`,
      gone: `Work Order ${number} no longer exists.`,
      back: 'Back to Work Orders',
    };
  }
  const pn = record.partNumber;
  return {
    title: 'Delete Part Number permanently',
    name: pn,
    typedLabel: 'Part Number',
    intro: `This removes Part Number ${pn} with all its data, including its saved details and its production history, from the database, as if it had never been entered. The server takes a pre-purge backup first.`,
    gone: `Part Number ${pn} and all its data no longer exist.`,
    back: 'Back to Part Numbers',
  };
}

function apiTarget(record: PermanentDeletionRecord): DeletionTarget {
  return record.kind === 'work-order'
    ? { kind: 'work-orders', workOrderNumbers: [record.workOrderNumber] }
    : { kind: 'part-number', partNumber: record.partNumber };
}

/**
 * The zone at the end of Work Order Details and of the Part Number dialog
 * that opens Delete permanently…: the Danger Zone presentation, apart from
 * every other delete. `children` is its one sentence.
 */
export function DeletePermanentlyZone({
  children,
  disabled,
  onOpen,
}: {
  children: ReactNode;
  disabled: boolean;
  onOpen: () => void;
}) {
  return (
    <div className="pdel-zone">
      <div className="pdel-zonetitle">Delete permanently</div>
      <div className="pdel-zonebody">
        <p>{children}</p>
        <button
          type="button"
          className="pdel-zonebtn"
          disabled={disabled}
          onClick={onOpen}
        >
          Delete permanently…
        </button>
      </div>
    </div>
  );
}

export function DeletePermanentlyDialog({
  record: recordAtOpen,
  writeBlocked,
  onClose,
  onDeleted,
}: {
  /** The record to delete; fixed when the dialog opens. */
  record: PermanentDeletionRecord;
  /** Blocks the request and the cancellation while the server cannot be
   * reached; reading and closing stay available. */
  writeBlocked: boolean;
  /** Close request while the record still exists (nothing requested,
   * or the request is not finished, or it ended without deleting). */
  onClose: () => void;
  /** The dialog closes after its request deleted the record: it no
   * longer exists. */
  onDeleted: (request: DeletionRequest) => void;
}) {
  const headingId = useId();
  const fieldId = useId();
  const [record] = useState(recordAtOpen);
  const [target] = useState(() => apiTarget(recordAtOpen));
  const copy = copyFor(record);
  const [previewState, setPreviewState] = useState<PreviewState>({
    status: 'loading',
  });
  const [previewGeneration, setPreviewGeneration] = useState(0);
  // The request the dialog follows: the one it made, or the open request
  // that names exactly this record.
  const [tracked, setTracked] = useState<DeletionRequest | null>(null);
  const [reason, setReason] = useState('');
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    previewDeletion(target).then(
      (preview) => {
        if (cancelled) return;
        setPreviewState({ status: 'ready', preview });
        const open = preview.openRequest;
        if (open !== null && requestNamesOnly(open, record)) setTracked(open);
      },
      (error: unknown) => {
        if (!cancelled) {
          setPreviewState({ status: 'error', message: errorMessage(error) });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [target, record, previewGeneration]);

  const readPreviewAgain = () => {
    setPreviewState({ status: 'loading' });
    setPreviewGeneration((value) => value + 1);
  };

  // While the followed request is open, its state is read again every few
  // seconds (one read at a time, so an older answer never overwrites a
  // newer one); a final state ends the reading. A read refused for the
  // user's sign-in pauses it until the sign-in is usable again.
  const trackedId = tracked?.id ?? null;
  const trackedOpen = tracked !== null && deletionRequestIsOpen(tracked);
  const [pollPaused, setPollPaused] = useState(false);
  const retrySignal = useContext(RetryFailedLoadsContext);
  const seenRetrySignal = useRef(retrySignal);
  useEffect(() => {
    if (seenRetrySignal.current === retrySignal) return;
    seenRetrySignal.current = retrySignal;
    setPollPaused(false);
  }, [retrySignal]);
  useEffect(() => {
    if (trackedId === null || !trackedOpen || pollPaused) return undefined;
    let stopped = false;
    let timer = 0;
    const schedule = () => {
      timer = window.setTimeout(() => {
        listDeletionRequests(DELETION_POLL_LIMIT).then(
          (requests) => {
            if (stopped) return;
            setPollError(null);
            const fresh = requests.find((request) => request.id === trackedId);
            if (fresh !== undefined) setTracked(fresh);
            schedule();
          },
          (error: unknown) => {
            if (stopped) return;
            setPollError(errorMessage(error));
            if (
              refusalFlag(error, 'authentication_required') ||
              refusalFlag(error, 'password_change_required')
            ) {
              setPollPaused(true);
              return;
            }
            schedule();
          },
        );
      }, DELETION_POLL_MS);
    };
    schedule();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [trackedId, trackedOpen, pollPaused]);

  const preview = previewState.status === 'ready' ? previewState.preview : null;
  const refused = preview !== null && preview.refusals.length > 0;
  const otherOpen =
    tracked === null && preview !== null ? preview.openRequest : null;
  const formShown =
    tracked === null &&
    preview !== null &&
    !refused &&
    preview.openRequest === null;

  const guard = useCloseGuard({
    dirty: formShown && (reason.trim() !== '' || typed.trim() !== ''),
    busy,
    onClose,
  });
  // Once its request deleted the record, every close hands back to the
  // host: the record no longer exists.
  const requestClose = () => {
    if (tracked !== null && tracked.state === 'COMPLETED') onDeleted(tracked);
    else guard.requestClose();
  };

  // A step that removes the focused control (the request sent, the state
  // changed) keeps the focus inside the dialog.
  const phaseKey =
    tracked === null
      ? `${previewState.status}:${String(formShown)}`
      : `request:${tracked.state}`;
  useEffect(() => {
    const root = document
      .getElementById(headingId)
      ?.closest<HTMLElement>('[role="dialog"]');
    const active = document.activeElement;
    if (root && (active === null || active === document.body)) root.focus();
  }, [phaseKey, headingId]);

  const submit = async () => {
    if (busy || writeBlocked) return;
    setBusy(true);
    setFailure(null);
    setNote(null);
    try {
      setTracked(await requestDeletion(target, reason, [typed]));
      // An answered request proves the sign-in usable again.
      setPollError(null);
      setPollPaused(false);
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        // The request may have been recorded: the preview read again
        // shows it as this record's open request if it was.
        setNote(REQUEST_UNKNOWN);
        readPreviewAgain();
      } else {
        setFailure({
          message: errorMessage(error),
          refusals: deletionRefusals(error),
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const cancelRequest = async () => {
    if (busy || writeBlocked || tracked === null) return;
    setBusy(true);
    setFailure(null);
    setNote(null);
    try {
      setTracked(await cancelDeletionRequest(tracked.id));
      setPollError(null);
      setPollPaused(false);
    } catch (error) {
      if (writeOutcomeUnknown(error)) setNote(CANCEL_UNKNOWN);
      else setFailure({ message: errorMessage(error), refusals: null });
    } finally {
      setBusy(false);
    }
  };

  // After a request that ended without deleting: read the preview again
  // and offer a new request (the reason is kept, the confirmation typed
  // again).
  const previewAgain = () => {
    setTracked(null);
    setTyped('');
    setFailure(null);
    setNote(null);
    setPollError(null);
    setPollPaused(false);
    readPreviewAgain();
  };

  const closeButton = (
    <button className="bigbtn ghost" disabled={busy} onClick={requestClose}>
      Close (Esc)
    </button>
  );
  let buttons: ReactNode;
  if (tracked !== null && tracked.state === 'COMPLETED') {
    buttons = (
      <button className="bigbtn primary" onClick={() => onDeleted(tracked)}>
        {copy.back}
      </button>
    );
  } else if (tracked !== null && deletionRequestCancellable(tracked)) {
    buttons = (
      <>
        {closeButton}
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked}
          onClick={() => void cancelRequest()}
        >
          Cancel request
        </button>
      </>
    );
  } else if (tracked !== null && !deletionRequestIsOpen(tracked)) {
    buttons = (
      <>
        {closeButton}
        <button className="bigbtn primary" onClick={previewAgain}>
          Preview again
        </button>
      </>
    );
  } else if (formShown) {
    buttons = (
      <>
        <button
          className="bigbtn ghost"
          disabled={busy}
          onClick={guard.requestClose}
        >
          Cancel (Esc)
        </button>
        <button
          className="bigbtn danger"
          disabled={
            busy ||
            writeBlocked ||
            reason.trim() === '' ||
            typed.trim() === '' ||
            (failure !== null && failure.refusals !== null)
          }
          onClick={() => void submit()}
        >
          {busy ? 'Requesting…' : 'Request deletion'}
        </button>
      </>
    );
  } else if (otherOpen !== null) {
    buttons = (
      <>
        {closeButton}
        <button className="bigbtn primary" onClick={readPreviewAgain}>
          Check again
        </button>
      </>
    );
  } else {
    buttons = closeButton;
  }

  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={requestClose}
      size="wide"
      className="dangerdlg pdel"
    >
      <h3 id={headingId}>{copy.title}</h3>
      <div className="big mono pdel-name">{copy.name}</div>
      <p className="sub">{copy.intro}</p>
      {previewState.status === 'loading' ? (
        <LoadingState label="Loading what would be deleted" />
      ) : null}
      {previewState.status === 'error' ? (
        <ErrorState
          message="What would be deleted could not be loaded."
          detail={previewState.message}
          onRetry={readPreviewAgain}
        />
      ) : null}
      {preview !== null && refused ? (
        <RefusedDeletion refusals={preview.refusals} />
      ) : null}
      {preview !== null && !refused ? (
        <WhatIsDeleted preview={preview} />
      ) : null}
      {tracked !== null ? (
        <RequestState
          request={tracked}
          gone={copy.gone}
          pollError={pollError}
          pollPaused={pollPaused}
        />
      ) : null}
      {otherOpen !== null ? <OtherOpenRequest request={otherOpen} /> : null}
      {formShown ? (
        <div className="pdel-form">
          <label className="pdel-field">
            <span>
              Reason <span className="field-required">(required)</span>
            </span>
            <textarea
              className="field"
              rows={3}
              maxLength={500}
              placeholder="Why are these records deleted?"
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="typedconfirm">
            <label htmlFor={fieldId}>
              Type <b>{copy.name}</b>{' '}
              <span className="field-note">({copy.typedLabel})</span> to confirm
            </label>
            <input
              id={fieldId}
              type="text"
              value={typed}
              autoComplete="off"
              spellCheck={false}
              placeholder={copy.name}
              disabled={busy}
              onChange={(event) => setTyped(event.target.value)}
            />
          </div>
        </div>
      ) : null}
      {failure !== null ? (
        <div className="pdel-error" role="alert">
          <div>{failure.message}</div>
          {failure.refusals !== null && failure.refusals.length > 0 ? (
            <RefusalList refusals={failure.refusals} />
          ) : null}
        </div>
      ) : null}
      {note !== null ? (
        <p className="pdel-note" role="status">
          {note}
        </p>
      ) : null}
      <div className="row">{buttons}</div>
      {guard.asking ? (
        <DiscardInputDialog
          title="Discard this deletion request?"
          onKeepEditing={guard.keepEditing}
          onDiscard={onClose}
        />
      ) : null}
    </ModalDialog>
  );
}

function RefusalList({ refusals }: { refusals: readonly DeletionRefusal[] }) {
  return (
    <ul className="pdel-refusals">
      {refusals.map((refusal, index) => (
        <li key={`${refusal.code}-${index}`}>{refusal.message}</li>
      ))}
    </ul>
  );
}

/** A deletion the server refuses: every refusal as the server words it. */
function RefusedDeletion({
  refusals,
}: {
  refusals: readonly DeletionRefusal[];
}) {
  return (
    <section className="pdel-block refused" aria-label="Refusals">
      <h4>This deletion is refused</h4>
      <p>Nothing can be requested until every refusal below is resolved.</p>
      <RefusalList refusals={refusals} />
    </section>
  );
}

/** What the deletion deletes: its records, the rows per table and the
 * audit rows kept with the reason each is kept. */
function WhatIsDeleted({ preview }: { preview: DeletionPreview }) {
  const kept = preview.keptAuditRows;
  return (
    <section className="pdel-block" aria-label="What is deleted">
      <h4>What is deleted</h4>
      <div className="pdel-records">
        {preview.workOrders.map((workOrder) => (
          <div key={`wo-${workOrder.id}`} className="pdel-record">
            <span className="pdel-recordname">
              {`Work Order ${workOrder.workOrderNumber ?? '—'}`}
            </span>
            <span className="pdel-recordsub">
              {workOrderLinesText(workOrder)}
            </span>
          </div>
        ))}
        {preview.partNumbers.map((partNumber) => (
          <div key={`pn-${partNumber.partNumber}`} className="pdel-record">
            <span className="pdel-recordname">
              {`Part Number ${partNumber.partNumber}`}
            </span>
            <span className="pdel-recordsub">
              {partNumber.master
                ? 'With its saved details'
                : 'No saved details'}
            </span>
          </div>
        ))}
      </div>
      {preview.rows !== null ? (
        <dl className="pdel-rows" aria-label="Rows per table">
          {rowsPerTable(preview.rows).map((row) => (
            <div key={row.table}>
              <dt>{row.label}</dt>
              <dd>{row.count.toLocaleString('en-US')}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {kept.length === 0 ? (
        <p className="pdel-kept">Audit rows kept: none.</p>
      ) : (
        <details className="pdel-kept">
          <summary>{`Audit rows kept: ${kept.length}`}</summary>
          <ul>
            {kept.map((row) => (
              <li key={row.id}>
                {keptAuditRowText(row)}
                <span className="pdel-keptwhy">{`Kept: ${row.kept}`}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** The followed request: its number and state, and what the state means. */
function RequestState({
  request,
  gone,
  pollError,
  pollPaused,
}: {
  request: DeletionRequest;
  /** The sentence that the record no longer exists. */
  gone: string;
  /** The last read of the state failed with this message. */
  pollError: string | null;
  /** The reading waits for the user's sign-in. */
  pollPaused: boolean;
}) {
  let lines: ReactNode = null;
  switch (request.state) {
    case 'APPROVED':
      lines = (
        <p>{`Requested ${formatInstant(request.requestedAt)} by ${requesterName(request)}. ${DELETION_WAIT_NOTE}`}</p>
      );
      break;
    case 'RUNNING':
      lines = (
        <p>{`Started ${formatInstant(request.startedAt ?? request.requestedAt)}. The server takes the pre-purge backup, then deletes the records in one step.`}</p>
      );
      break;
    case 'COMPLETED':
      lines = (
        <>
          <p className="pdel-strong">{gone}</p>
          <p>
            {`Deleted ${formatInstant(request.finishedAt ?? request.requestedAt)}, after the pre-purge backup `}
            <span className="pdel-backup">
              {request.prePurgeBackup?.name ?? '—'}
            </span>
            .
          </p>
        </>
      );
      break;
    case 'FAILED':
      lines = (
        <>
          <p className="pdel-failure">{request.failureReason ?? 'Failed.'}</p>
          <p>You may request the deletion again.</p>
        </>
      );
      break;
    case 'CANCELLED':
      lines = (
        <p>
          {request.cancelledBy === null
            ? 'Cancelled. Nothing was deleted.'
            : `Cancelled by ${request.cancelledBy.displayName}. Nothing was deleted.`}
        </p>
      );
      break;
  }
  return (
    <section
      className="pdel-block"
      role="status"
      aria-label={`Deletion request ${request.id}`}
    >
      <div className="pdel-statushead">
        <span>{`Deletion request ${request.id}`}</span>
        <span className={`pdel-state ${deletionStateTone(request.state)}`}>
          {deletionStateLabel(request.state)}
        </span>
      </div>
      {lines}
      {pollError !== null && deletionRequestIsOpen(request) ? (
        <p>
          {pollPaused
            ? `The request's state could not be read: ${pollError} It is read again once you are signed in again.`
            : `The request's state could not be read just now: ${pollError} PartFlow tries again every few seconds.`}
        </p>
      ) : null}
    </section>
  );
}

/** Another open request: nothing can be requested until it ends. */
function OtherOpenRequest({ request }: { request: DeletionRequest }) {
  return (
    <section className="pdel-block" aria-label="Open deletion request">
      <div className="pdel-statushead">
        <span>{`Deletion request ${request.id} is still open`}</span>
        <span className={`pdel-state ${deletionStateTone(request.state)}`}>
          {deletionStateLabel(request.state)}
        </span>
      </div>
      <p>{`${scopeLines(request.scope).join(', ')} · requested ${formatInstant(request.requestedAt)} by ${requesterName(request)}.`}</p>
      <p>Nothing can be requested until it finishes or is cancelled.</p>
    </section>
  );
}
