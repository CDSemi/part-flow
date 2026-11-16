import { useState } from 'react';

import { ApiError } from '../../api/client';
import { getBackupPolicy, updateBackupPolicy } from '../../api/policies';
import type { BackupPolicy } from '../../api/policies';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useUnsavedWork } from '../../app/unsaved-work';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  BACKUP_INTERVAL_HOURS,
  KEEP_DAILY_RANGE,
  KEEP_HOURLY_RANGE,
  KEEP_WEEKLY_RANGE,
  formatBackupInterval,
  keptSummary,
  parseKept,
} from './backup-policy';
import {
  ReadOnlyValues,
  SectionHeader,
  ServerErrorNote,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Backups (Phase 16, owner decision of 2026-11-13;
// GUI_DESIGN §9 Policies): the real backup policy stored in the server's
// application policy (`/api/policies/backups`), audited and re-validated
// by the server — how often the server's hourly backup task takes a
// scheduled backup (every 1, 2, 3, 4, 6, 8, 12 or 24 hours) and how many
// hourly, daily and weekly scheduled backups its rotation keeps. Saving
// takes or removes no backup: the task reads the policy at its next run.
// Without the Configure system settings permission the values read as
// text.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'backups')?.subtitle ?? '';

const KEEP_HOURLY_ERROR = 'Enter a whole number from 0 to 720.';
const KEEP_DAILY_ERROR = 'Enter a whole number from 1 to 3650.';
const KEEP_WEEKLY_ERROR = 'Enter a whole number from 0 to 520.';
const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Check the backup settings before trying again; saving the same values again is safe.';

export function BackupsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const canWrite = useSession().can('CONFIGURE_SYSTEM_SETTINGS');
  const policyData = useApiData(getBackupPolicy);

  const header = (
    <>
      <SectionHeader title="Backups" subtitle={SUBTITLE} />
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
          message="Backup settings could not be loaded."
          detail={policyData.state.message}
          onRetry={policyData.reload}
        />
      </>
    );
  }

  const saved = policyData.state.data;
  return (
    <>
      {header}
      <div className="admin-config">
        <h2>Scheduled backups</h2>
        <p className="admin-confighelp">
          The backup task on the server runs every hour. It takes a backup when
          the interval below has passed since the newest scheduled backup, then
          keeps the newest scheduled backup of each of the last hours, days and
          weeks set below (counted in UTC) and removes the other scheduled
          backups. Backups taken by hand or before an update are never removed.
          A change applies from the task&apos;s next run.
        </p>
        {canWrite ? (
          <BackupPolicyForm
            saved={saved}
            writeBlocked={writeBlocked}
            onSaved={policyData.reload}
            onOutcomeUnknown={policyData.revalidate}
          />
        ) : (
          <ReadOnlyValues
            rows={[
              {
                label: 'Scheduled backup',
                value: formatBackupInterval(saved.intervalHours),
              },
              { label: 'Hourly backups kept', value: saved.keepHourly },
              { label: 'Daily backups kept', value: saved.keepDaily },
              { label: 'Weekly backups kept', value: saved.keepWeekly },
            ]}
          />
        )}
      </div>
    </>
  );
}

function BackupPolicyForm({
  saved,
  writeBlocked,
  onSaved,
  onOutcomeUnknown,
}: {
  saved: BackupPolicy;
  writeBlocked: boolean;
  onSaved: () => void;
  /** Background re-read of the stored policy after a save whose answer
   * was lost — never tearing the form down, so the notice stays. */
  onOutcomeUnknown: () => void;
}) {
  const [intervalHours, setIntervalHours] = useState(saved.intervalHours);
  const [hourlyText, setHourlyText] = useState(String(saved.keepHourly));
  const [dailyText, setDailyText] = useState(String(saved.keepDaily));
  const [weeklyText, setWeeklyText] = useState(String(saved.keepWeekly));
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const keepHourly = parseKept(hourlyText, KEEP_HOURLY_RANGE);
  const keepDaily = parseKept(dailyText, KEEP_DAILY_RANGE);
  const keepWeekly = parseKept(weeklyText, KEEP_WEEKLY_RANGE);
  const policy: BackupPolicy | null =
    keepHourly !== null && keepDaily !== null && keepWeekly !== null
      ? { intervalHours, keepHourly, keepDaily, keepWeekly }
      : null;
  const dirty =
    intervalHours !== saved.intervalHours ||
    keepHourly !== saved.keepHourly ||
    keepDaily !== saved.keepDaily ||
    keepWeekly !== saved.keepWeekly;
  useUnsavedWork(dirty);

  const edited = (set: (text: string) => void) => (text: string) => {
    set(text);
    setSavedNote(false);
  };

  const submit = async () => {
    if (policy === null) return;
    setBusy(true);
    setServerError(null);
    setSavedNote(false);
    try {
      await updateBackupPolicy(policy);
      setSavedNote(true);
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && !writeOutcomeUnknown(error)) {
        setServerError(error.message);
      } else {
        // No answer, a timeout or a 5xx: the PUT may have committed.
        // Re-read the stored policy so Save is judged against it.
        setServerError(UNKNOWN_OUTCOME_MESSAGE);
        onOutcomeUnknown();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="admin-configgrid">
        <div>
          <label>
            Scheduled backup
            <select
              className="field"
              value={intervalHours}
              onChange={(event) => {
                setIntervalHours(Number(event.target.value));
                setSavedNote(false);
              }}
            >
              {BACKUP_INTERVAL_HOURS.map((hours) => (
                <option key={hours} value={hours}>
                  {formatBackupInterval(hours)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <KeptField
          label="Hourly backups kept"
          range={KEEP_HOURLY_RANGE}
          text={hourlyText}
          onChange={edited(setHourlyText)}
          error={keepHourly === null ? KEEP_HOURLY_ERROR : null}
        />
        <KeptField
          label="Daily backups kept"
          range={KEEP_DAILY_RANGE}
          text={dailyText}
          onChange={edited(setDailyText)}
          error={keepDaily === null ? KEEP_DAILY_ERROR : null}
        />
        <KeptField
          label="Weekly backups kept"
          range={KEEP_WEEKLY_RANGE}
          text={weeklyText}
          onChange={edited(setWeeklyText)}
          error={keepWeekly === null ? KEEP_WEEKLY_ERROR : null}
        />
      </div>
      {policy !== null ? (
        <p className="admin-confighelp">{keptSummary(policy)}</p>
      ) : null}
      <ServerErrorNote message={serverError} />
      {savedNote ? (
        <div className="admin-savednote" role="status">
          ✓ Backup settings saved.
        </div>
      ) : null}
      <div className="row">
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || policy === null || !dirty}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </>
  );
}

/** One labelled whole-number input of the form, with its own inline
 * error under it. */
function KeptField({
  label,
  range,
  text,
  onChange,
  error,
}: {
  label: string;
  range: readonly [number, number];
  text: string;
  onChange: (text: string) => void;
  error: string | null;
}) {
  return (
    <div>
      <label>
        {label}
        <input
          className="field mono"
          type="number"
          min={range[0]}
          max={range[1]}
          step={1}
          value={text}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {error !== null ? (
        <div className="err" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}
