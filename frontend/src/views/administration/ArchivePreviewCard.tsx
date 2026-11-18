import { useEffect, useRef, useState } from 'react';

import { ApiError, errorMessage } from '../../api/client';
import { previewArchive, requestArchiveRun } from '../../api/history-archive';
import type { ArchivePreview, ArchiveRun } from '../../api/history-archive';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useCloseGuard } from '../../components/close-guard';
import { DiscardInputDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { formatIsoDate } from '../dates';
import {
  countsLine,
  eligibleRange,
  formatArchiveSize,
  formatPreviewDuration,
} from './archive-runs';
import { AdminField, ServerErrorNote, SettingsCard } from './section-widgets';

// Administration → History archival & purge → Preview and the request of
// a run (archival design B steps 1 and 2). The preview asks the server
// what a run would archive at a cutoff date — by default the newest date
// the retention period allows, which the server answers — and can take up
// to the preview time limit, so it shows its progress while the rest of
// the page stays usable. The card and the request say what a run does: it
// exports the history and, after the off-host copy and a pre-purge
// backup, purges it from the database. `Request a run…` opens the request
// with the reason and the previewed numbers; the server recomputes the
// preview, stores it with the run and refuses what it must (another open
// run, nothing eligible, the cutoff), and its messages are shown as
// given. A request whose answer was lost may have created the run: the
// runs are reloaded and the user is told to check them before requesting
// again.

const REQUEST_UNKNOWN =
  'The server did not answer, so the run may or may not have been requested. Check the runs table before requesting again.';

/** What follows a purge, said on the card and in the request. */
const AFTER_PURGE =
  'Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.';

export function ArchivePreviewCard({
  previewSeconds,
  writeBlocked,
  onRunsChanged,
}: {
  /** The stored preview time limit (null until the settings loaded). */
  previewSeconds: number | null;
  writeBlocked: boolean;
  /** Reload the runs table (a run was, or may have been, requested). */
  onRunsChanged: () => void;
}) {
  const [cutoffDate, setCutoffDate] = useState('');
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [preview, setPreview] = useState<ArchivePreview | null>(null);
  const [latestCutoffDate, setLatestCutoffDate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [notice, setNotice] = useState<
    { kind: 'requested'; run: ArchiveRun } | { kind: 'unknown' } | null
  >(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Seconds since the running preview started, once a second.
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [running]);

  const runPreview = async () => {
    if (running) return;
    setRunning(true);
    setError(null);
    setNotice(null);
    setPreview(null);
    try {
      const result = await previewArchive(
        cutoffDate === '' ? null : cutoffDate,
      );
      if (!mounted.current) return;
      setPreview(result);
      setLatestCutoffDate(result.latestCutoffDate);
      setCutoffDate(result.cutoffDate);
    } catch (failure) {
      if (!mounted.current) return;
      setError(
        failure instanceof ApiError
          ? failure.message
          : 'The PartFlow server could not be reached.',
      );
    } finally {
      if (mounted.current) setRunning(false);
    }
  };

  const nothingEligible = preview !== null && preview.components === 0;

  return (
    <SettingsCard
      title="Preview"
      description="What a run would archive and purge at a cutoff date, before you request it."
      note={`After the off-host copy and a pre-purge backup, a run deletes the archived Movement history from the database. ${AFTER_PURGE}`}
    >
      <div className="admin-cardform">
        <div className="admin-configgrid">
          <div>
            <label>
              Cutoff date
              <input
                className="field mono"
                type="date"
                value={cutoffDate}
                max={latestCutoffDate ?? undefined}
                disabled={running}
                onChange={(event) => {
                  setCutoffDate(event.target.value);
                  // A preview stands only for the date it was run for.
                  setPreview(null);
                  setNotice(null);
                }}
              />
            </label>
          </div>
        </div>
        <p className="admin-confighelp">
          {latestCutoffDate === null
            ? 'History before the start of this day is archived. Leave it empty for the latest date the retention period allows.'
            : `History before the start of this day is archived. The latest date the retention period allows is ${formatIsoDate(latestCutoffDate)}.`}
        </p>
        {running ? (
          <p className="admin-confighelp" role="status">
            {previewSeconds === null
              ? `Running the preview… ${elapsed} s`
              : `Running the preview… ${elapsed} s (time limit ${previewSeconds} s)`}
          </p>
        ) : null}
        <ServerErrorNote message={error} />
        {preview !== null ? <PreviewValues preview={preview} /> : null}
        {nothingEligible ? (
          <p className="admin-confighelp">
            Nothing is eligible at this cutoff, so there is nothing to request.
          </p>
        ) : null}
        {notice?.kind === 'requested' ? (
          <div className="admin-savednote" role="status">
            {`✓ Run ${notice.run.id} is requested. The archival job on the server starts it at its next Start minute.`}
          </div>
        ) : null}
        <ServerErrorNote
          message={notice?.kind === 'unknown' ? REQUEST_UNKNOWN : null}
        />
        <div className="admin-cardactionrow">
          <button
            type="button"
            className="admin-btn"
            disabled={running || writeBlocked}
            onClick={() => void runPreview()}
          >
            {running ? 'Running preview…' : 'Run preview'}
          </button>
          <button
            type="button"
            className="admin-btn primary"
            disabled={
              running || writeBlocked || preview === null || nothingEligible
            }
            onClick={() => setRequesting(true)}
          >
            Request a run…
          </button>
        </div>
      </div>
      {requesting && preview !== null ? (
        <RequestRunDialog
          preview={preview}
          writeBlocked={writeBlocked}
          onCancel={() => setRequesting(false)}
          onRequested={(run) => {
            setRequesting(false);
            setPreview(null);
            setNotice({ kind: 'requested', run });
            onRunsChanged();
          }}
          onOutcomeUnknown={() => {
            setRequesting(false);
            setNotice({ kind: 'unknown' });
            onRunsChanged();
          }}
        />
      ) : null}
    </SettingsCard>
  );
}

/** The numbers of one preview, as label and value rows. */
function PreviewValues({ preview }: { preview: ArchivePreview }) {
  const rows: [string, string][] = [
    ['Cutoff date', formatIsoDate(preview.cutoffDate)],
    ['Components', preview.components.toLocaleString('en-US')],
    ['Flows', preview.flows.toLocaleString('en-US')],
    ['Movements', preview.movements.toLocaleString('en-US')],
    ['First and last Movement', eligibleRange(preview)],
    [
      'Estimated archive size',
      formatArchiveSize(preview.estimatedArchiveBytes),
    ],
    ['Preview took', formatPreviewDuration(preview.durationMs)],
  ];
  return (
    <div className="admin-configpreview">
      {rows.map(([label, value]) => (
        <div key={label} className="prow">
          <span className="k">{label}</span>
          <span className="v">{value}</span>
        </div>
      ))}
    </div>
  );
}

function RequestRunDialog({
  preview,
  writeBlocked,
  onCancel,
  onRequested,
  onOutcomeUnknown,
}: {
  preview: ArchivePreview;
  writeBlocked: boolean;
  onCancel: () => void;
  onRequested: (run: ArchiveRun) => void;
  onOutcomeUnknown: () => void;
}) {
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
      onRequested(await requestArchiveRun(reason, preview.cutoffDate));
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        // No answer, a timeout or a gateway error (502, 504, 524): the
        // run may exist. Never retried here.
        onOutcomeUnknown();
        return;
      }
      setServerError(errorMessage(error));
      setBusy(false);
    }
  };

  const title = 'Request an archival run';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <p className="admin-idnote">
        {`The run exports the Movement history before ${formatIsoDate(preview.cutoffDate)} to archive files on the server and, after the off-host copy and a pre-purge backup, deletes it from the database. ${AFTER_PURGE}`}
      </p>
      <div className="admin-identity">
        <div className="idrow">
          <span className="k">Cutoff date</span>
          <span className="v">{formatIsoDate(preview.cutoffDate)}</span>
        </div>
        <div className="idrow">
          <span className="k">Preview</span>
          <span className="v">{countsLine(preview)}</span>
        </div>
        <div className="idrow">
          <span className="k">Movements from</span>
          <span className="v">{eligibleRange(preview)}</span>
        </div>
        <div className="idrow">
          <span className="k">Estimated size</span>
          <span className="v">
            {formatArchiveSize(preview.estimatedArchiveBytes)}
          </span>
        </div>
      </div>
      <div className="admin-form">
        <AdminField
          label={
            <>
              Reason <span className="field-required">(required)</span>
            </>
          }
        >
          <textarea
            className="field"
            rows={3}
            maxLength={500}
            placeholder="Why is this history archived?"
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </AdminField>
        {busy ? (
          <p className="admin-fieldhelp" role="status">
            {`Requesting the run… The server checks the numbers again, which can take up to ${preview.timeLimitSeconds} seconds.`}
          </p>
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
          disabled={busy || writeBlocked}
          onClick={() => void submit()}
        >
          Request run
        </button>
      </div>
      {guard.asking ? (
        <DiscardInputDialog
          title="Discard this request?"
          onKeepEditing={guard.keepEditing}
          onDiscard={onCancel}
        />
      ) : null}
    </ModalDialog>
  );
}
