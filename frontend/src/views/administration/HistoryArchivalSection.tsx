import { useState } from 'react';

import { ApiError } from '../../api/client';
import { getRetentionPolicy, updateRetentionPolicy } from '../../api/policies';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  RETENTION_MONTHS_MAX,
  RETENTION_MONTHS_MIN,
  formatRetentionPeriod,
  parseRetentionMonths,
} from './retention-period';
import {
  ReadOnlyValues,
  SectionHeader,
  ServerErrorNote,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → History archival & purge (Phase 13; GUI_DESIGN §9
// Policies, PROJECT_PROFILE §28): the real Movement-history retention
// period — no retention period, or whole months from 12 to 1200 —
// stored in the server's application policy
// (`/api/policies/data-retention`), audited and re-validated by the
// server. It is configuration only: saving it archives, deletes or
// schedules nothing, and no production workflow reads it. Archival and
// purge runs are not available yet and the section says so — no
// control pretends otherwise. Without the Configure system settings
// permission the period reads as text.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'data-retention')?.subtitle ??
  '';

const PERIOD_ERROR = 'Enter a whole number of months from 12 to 1200.';
const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Check the retention period before trying again; saving the same value again is safe.';

export function HistoryArchivalSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const canWrite = useSession().can('CONFIGURE_SYSTEM_SETTINGS');
  const policyData = useApiData(getRetentionPolicy);

  const header = (
    <>
      <SectionHeader title="History archival & purge" subtitle={SUBTITLE} />
      {canWrite ? null : (
        <ViewOnlyNote permission="CONFIGURE_SYSTEM_SETTINGS" />
      )}
    </>
  );

  if (policyData.state.status === 'loading') {
    return (
      <>
        {header}
        <LoadingState label="Loading section" />
      </>
    );
  }
  if (policyData.state.status === 'error') {
    return (
      <>
        {header}
        <ErrorState
          message="History retention settings could not be loaded."
          detail={policyData.state.message}
          onRetry={policyData.reload}
        />
      </>
    );
  }

  return (
    <>
      {header}
      <div className="ad-config">
        <h2>Retention period</h2>
        <p className="ad-confighelp">
          How long Movement history stays in the PartFlow database before
          archival maintenance may move it to archive files. Saving the period
          archives or deletes nothing, and it never affects production scanning.
        </p>
        {canWrite ? (
          <RetentionPeriodForm
            savedMonths={policyData.state.data.retentionPeriodMonths}
            writeBlocked={writeBlocked}
            onSaved={policyData.reload}
            onOutcomeUnknown={policyData.revalidate}
          />
        ) : (
          <ReadOnlyValues
            rows={[
              {
                label: 'Retention period',
                value:
                  policyData.state.data.retentionPeriodMonths === null
                    ? 'No retention period'
                    : formatRetentionPeriod(
                        policyData.state.data.retentionPeriodMonths,
                      ),
              },
            ]}
          />
        )}
        <h2>Archival and purge runs</h2>
        <p className="ad-confighelp">
          Archival and purge runs — by retention period, data-size threshold or
          manual request — are not available yet. Nothing is archived or purged:
          all Movement history stays in the database.
        </p>
      </div>
    </>
  );
}

function RetentionPeriodForm({
  savedMonths,
  writeBlocked,
  onSaved,
  onOutcomeUnknown,
}: {
  /** The stored period; null = no retention period. */
  savedMonths: number | null;
  writeBlocked: boolean;
  onSaved: () => void;
  /** Background re-read of the stored period after a save whose answer
   * was lost — never tearing the form down, so the notice stays. */
  onOutcomeUnknown: () => void;
}) {
  const [keepPeriod, setKeepPeriod] = useState(savedMonths !== null);
  const [text, setText] = useState(
    savedMonths === null ? '' : String(savedMonths),
  );
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const months = keepPeriod ? parseRetentionMonths(text) : null;
  const invalid = keepPeriod && months === null;
  const dirty = months !== savedMonths;

  const choose = (keep: boolean) => {
    setKeepPeriod(keep);
    setSavedNote(false);
  };

  const submit = async () => {
    if (invalid) return;
    setBusy(true);
    setServerError(null);
    setSavedNote(false);
    try {
      await updateRetentionPolicy(months);
      setSavedNote(true);
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && !writeOutcomeUnknown(error)) {
        setServerError(error.message);
      } else {
        // No answer, a timeout or a 5xx: the PUT may have committed.
        // Re-read the stored period so Save is judged against it.
        setServerError(UNKNOWN_OUTCOME_MESSAGE);
        onOutcomeUnknown();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="ad-form">
        <label className="ad-check">
          <input
            type="radio"
            name="retention-period-choice"
            checked={!keepPeriod}
            onChange={() => choose(false)}
          />
          <span>No retention period</span>
        </label>
        <label className="ad-check">
          <input
            type="radio"
            name="retention-period-choice"
            checked={keepPeriod}
            onChange={() => choose(true)}
          />
          <span>Keep a set period of history</span>
        </label>
      </div>
      {keepPeriod ? (
        <>
          <div className="ad-configgrid">
            <label>
              Retention period (months)
              <input
                className="field mono"
                type="number"
                min={RETENTION_MONTHS_MIN}
                max={RETENTION_MONTHS_MAX}
                step={1}
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                  setSavedNote(false);
                }}
              />
            </label>
          </div>
          {months === null ? (
            <div className="err" role="alert">
              {PERIOD_ERROR}
            </div>
          ) : (
            <p className="ad-confighelp">
              {`Retention period: ${formatRetentionPeriod(months)}.`}
            </p>
          )}
        </>
      ) : null}
      <ServerErrorNote message={serverError} />
      {savedNote ? (
        <div className="ad-savednote" role="status">
          ✓ Retention period saved.
        </div>
      ) : null}
      <div className="row">
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || invalid || !dirty}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </>
  );
}
