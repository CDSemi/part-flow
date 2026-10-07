import { useState } from 'react';

import { ApiError } from '../../api/client';
import { getDueSoonPolicy, updateDueSoonPolicy } from '../../api/policies';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  DUE_SOON_DAYS_RANGE,
  DUE_SOON_PERCENT_RANGE,
  dueSoonWindowDays,
  isDueSoonDays,
  isDueSoonPercent,
} from '../dates';
import type { DueSoonPolicy } from '../dates';
import { SectionHeader, ServerErrorNote } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';
import { SignInSettingsPanel } from './SignInSettingsPanel';

// Administration → Settings (Phase 13; GUI_DESIGN §9 Policies and
// §3.12): the Due Soon warning panel — the one global policy behind
// every derived due countdown (Production Board, Area Board, Scan
// Station, Priority and Work Orders), stored in the server's
// application policy (`/api/policies/due-soon`), audited and
// re-validated by the server. The three fields share a cross-field rule
// (minimum ≤ maximum), so Save replaces them together. The User sign-in
// panel (SignInSettingsPanel) follows it and loads its own data. The
// rest of Settings is not available yet and says so — no control
// pretends otherwise.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'settings')?.subtitle ?? '';

const MIN_DAYS_ERROR =
  'Minimum warning days must be a whole number from 0 to 365.';
const MAX_DAYS_ERROR =
  'Maximum warning days must be a whole number from 0 to 365.';
const PERCENT_ERROR =
  'The lead-time warning percentage must be a whole number from 1 to 100.';
const ORDER_ERROR =
  'Minimum warning days cannot be greater than maximum warning days.';
const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Check the Due Soon warning before trying again; saving the same values again is safe.';

const EXAMPLE_LEADS = [10, 30, 90];

/** The whole number the text holds when `isValid` admits it, else null
 * (never rounded or clamped). */
function parseSetting(
  text: string,
  isValid: (value: unknown) => value is number,
): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return isValid(value) ? value : null;
}

export function SettingsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const policyData = useApiData(getDueSoonPolicy);

  const header = <SectionHeader title="Settings" subtitle={SUBTITLE} />;

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
          message="Due Soon warning settings could not be loaded."
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
        <h2>Due Soon warning</h2>
        <p className="ad-confighelp">
          A due date reads as due soon when its days left fall within this
          window: the lead-time percentage of the Work Order&apos;s received →
          due lead time, never fewer than the minimum and never more than the
          maximum warning days. Without a known lead time the minimum applies.
          Used by every due countdown — Production Board, Area Board, Scan
          Station, Priority and Work Orders.
        </p>
        <DueSoonForm
          saved={policyData.state.data}
          writeBlocked={writeBlocked}
          onSaved={policyData.reload}
          onOutcomeUnknown={policyData.revalidate}
        />
        <SignInSettingsPanel />
        <h2>Other settings</h2>
        <p className="ad-confighelp">
          Other application settings are not available yet.
        </p>
      </div>
    </>
  );
}

function DueSoonForm({
  saved,
  writeBlocked,
  onSaved,
  onOutcomeUnknown,
}: {
  saved: DueSoonPolicy;
  writeBlocked: boolean;
  onSaved: () => void;
  /** Background re-read of the stored policy after a save whose answer
   * was lost — never tearing the panel down, so the notice stays. */
  onOutcomeUnknown: () => void;
}) {
  const [minText, setMinText] = useState(String(saved.minDays));
  const [percentText, setPercentText] = useState(String(saved.leadTimePercent));
  const [maxText, setMaxText] = useState(String(saved.maxDays));
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const minDays = parseSetting(minText, isDueSoonDays);
  const leadTimePercent = parseSetting(percentText, isDueSoonPercent);
  const maxDays = parseSetting(maxText, isDueSoonDays);
  const misordered = minDays !== null && maxDays !== null && minDays > maxDays;
  const policy: DueSoonPolicy | null =
    minDays !== null && leadTimePercent !== null && maxDays !== null
      ? { minDays, leadTimePercent, maxDays }
      : null;
  const valid = policy !== null && !misordered;
  const dirty =
    minDays !== saved.minDays ||
    leadTimePercent !== saved.leadTimePercent ||
    maxDays !== saved.maxDays;

  const edited = (set: (text: string) => void) => (text: string) => {
    set(text);
    setSavedNote(false);
  };

  const submit = async () => {
    if (!valid || policy === null) return;
    setBusy(true);
    setServerError(null);
    setSavedNote(false);
    try {
      await updateDueSoonPolicy(policy);
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
      <div className="ad-configgrid">
        <DayField
          label="Minimum warning days"
          range={DUE_SOON_DAYS_RANGE}
          text={minText}
          onChange={edited(setMinText)}
          error={minDays === null ? MIN_DAYS_ERROR : null}
        />
        <DayField
          label="Lead-time warning percentage (%)"
          range={DUE_SOON_PERCENT_RANGE}
          text={percentText}
          onChange={edited(setPercentText)}
          error={leadTimePercent === null ? PERCENT_ERROR : null}
        />
        <DayField
          label="Maximum warning days"
          range={DUE_SOON_DAYS_RANGE}
          text={maxText}
          onChange={edited(setMaxText)}
          error={maxDays === null ? MAX_DAYS_ERROR : null}
        />
      </div>
      {misordered ? (
        <div className="err" role="alert">
          {ORDER_ERROR}
        </div>
      ) : null}
      {valid ? (
        <ul className="ad-confighelp">
          {EXAMPLE_LEADS.map((lead) => {
            const days = dueSoonWindowDays(lead, policy);
            return (
              <li key={lead}>
                {`${lead}-day lead → warns ${days} day${days === 1 ? '' : 's'} ahead`}
              </li>
            );
          })}
        </ul>
      ) : null}
      <ServerErrorNote message={serverError} />
      {savedNote ? (
        <div className="ad-savednote" role="status">
          ✓ Due Soon warning saved.
        </div>
      ) : null}
      <div className="row">
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || !valid || !dirty}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </>
  );
}

/** One labelled whole-number input of the panel, with its own inline
 * error under it. */
function DayField({
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
