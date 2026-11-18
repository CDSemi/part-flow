import { Fragment, useState } from 'react';

import { ApiError } from '../../api/client';
import {
  getTwoStepPolicy,
  TWO_STEP_SETTINGS,
  updateTwoStepPolicy,
} from '../../api/policies';
import type { TwoStepPolicy, TwoStepSetting } from '../../api/policies';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AdminField, ServerErrorNote, SettingsCard } from './section-widgets';

// Administration → Security → Two-step sign-in (Phase 17, owner decisions
// of 2026-11-17): every number of two-step sign-in — how long a second
// step stays open and how many wrong codes it takes, the wrong codes that
// lock an account, the emailed codes, the recovery codes and the clock
// drift of an authenticator app. Only the app standard stays fixed (6
// digits, a new code every 30 seconds), because authenticator apps ignore
// other values. Every signed-in user reads it; users whose role may
// configure system settings edit it. Save sends only the changed settings
// (the server merges them); the server validates and applies every value
// from the next sign-in step.

/** The groups of the settings, in the card and in the dialog. */
type SettingGroup =
  | 'During sign-in'
  | 'Emailed codes'
  | 'Recovery codes'
  | 'Authenticator apps and passkeys';

interface SettingSpec {
  group: SettingGroup;
  /** The preview row. */
  label: string;
  /** The form field, with its unit. */
  field: string;
  min: number;
  max: number;
  show: (value: number) => string;
  /** The refusal, worded as the server words it. */
  error: string;
}

const count = (value: number) => String(value);
const minutes = (value: number) =>
  `${value} ${value === 1 ? 'minute' : 'minutes'}`;
const seconds = (value: number) =>
  `${value} ${value === 1 ? 'second' : 'seconds'}`;

const SPECS: Record<TwoStepSetting, SettingSpec> = {
  windowMinutes: {
    group: 'During sign-in',
    label: 'Time to finish the second step',
    field: 'Time to finish the second step (minutes)',
    min: 1,
    max: 60,
    show: minutes,
    error:
      'The time to finish the second step must be a whole number of minutes from 1 to 60.',
  },
  maxCodes: {
    group: 'During sign-in',
    label: 'Wrong codes per sign-in',
    field: 'Wrong codes per sign-in',
    min: 1,
    max: 20,
    show: count,
    error:
      'The wrong codes one sign-in may take must be a whole number from 1 to 20.',
  },
  lockoutCodes: {
    group: 'During sign-in',
    label: 'Wrong codes before a lock',
    field: 'Wrong codes before a lock',
    min: 3,
    max: 100,
    show: count,
    error:
      'The wrong codes before a lock must be a whole number from 3 to 100.',
  },
  emailCodes: {
    group: 'Emailed codes',
    label: 'Emailed codes per sign-in',
    field: 'Emailed codes per sign-in',
    min: 1,
    max: 10,
    show: count,
    error:
      'The codes one sign-in may email must be a whole number from 1 to 10.',
  },
  emailIntervalSeconds: {
    group: 'Emailed codes',
    label: 'Wait between emailed codes',
    field: 'Wait between emailed codes (seconds)',
    min: 0,
    max: 3600,
    show: seconds,
    error:
      'The wait between two emailed codes must be a whole number of seconds from 0 to 3600.',
  },
  emailCodeDigits: {
    group: 'Emailed codes',
    label: 'Digits of an emailed code',
    field: 'Digits of an emailed code',
    min: 6,
    max: 10,
    show: count,
    error: 'The digits of an emailed code must be a whole number from 6 to 10.',
  },
  recoveryCodes: {
    group: 'Recovery codes',
    label: 'Recovery codes per set',
    field: 'Recovery codes per set',
    min: 5,
    max: 20,
    show: count,
    error: 'The number of recovery codes must be a whole number from 5 to 20.',
  },
  recoveryCodeLength: {
    group: 'Recovery codes',
    label: 'Characters of a recovery code',
    field: 'Characters of a recovery code',
    min: 8,
    max: 20,
    show: count,
    error:
      'The characters of a recovery code must be a whole number from 8 to 20.',
  },
  setupMinutes: {
    group: 'Authenticator apps and passkeys',
    label: 'Time to set up an app or a passkey',
    field: 'Time to set up an app or a passkey (minutes)',
    min: 1,
    max: 60,
    show: minutes,
    error:
      'The time to finish setting up an authenticator app or a passkey must be a whole number of minutes from 1 to 60.',
  },
  clockDriftSteps: {
    group: 'Authenticator apps and passkeys',
    label: 'Clock drift allowed',
    field: 'Clock drift allowed (30-second steps)',
    min: 0,
    max: 2,
    show: (value) => (value === 0 ? 'None' : `±${value * 30} seconds`),
    error:
      'The clock drift allowed must be a whole number of 30-second steps from 0 to 2.',
  },
};

/** The settings by group, in the order of TWO_STEP_SETTINGS. */
const GROUPS = TWO_STEP_SETTINGS.reduce<
  { group: SettingGroup; settings: TwoStepSetting[] }[]
>((groups, setting) => {
  const last = groups.at(-1);
  if (last?.group === SPECS[setting].group) last.settings.push(setting);
  else groups.push({ group: SPECS[setting].group, settings: [setting] });
  return groups;
}, []);

const UNKNOWN_OUTCOME =
  'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.';

/** The whole number in `text` within [min, max], else null (never
 * rounded or clamped). */
function parseWhole(text: string, min: number, max: number): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= max ? value : null;
}

export function TwoStepSettingsPanel() {
  const session = useSession();
  const canEdit = session.can('CONFIGURE_SYSTEM_SETTINGS');
  const { status } = useConnectivity();
  const policyData = useApiData(getTwoStepPolicy);
  const [editing, setEditing] = useState(false);
  const { state } = policyData;
  const policy = state.status === 'ready' ? state.data : null;

  return (
    <SettingsCard
      title="Two-step sign-in"
      description="Limits for the extra step after the password: codes from an authenticator app, passkeys, emailed codes and recovery codes."
      edit={
        canEdit && policy
          ? {
              label: 'Edit two-step sign-in settings…',
              disabled: status !== 'connected',
              onClick: () => setEditing(true),
            }
          : undefined
      }
      note="Emailed codes are sent through the outgoing mail server, set up in Administration → Settings → Outgoing mail server."
    >
      {state.status === 'loading' ? (
        <LoadingState label="Loading two-step sign-in settings" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The two-step sign-in settings could not be loaded."
          detail={state.message}
          onRetry={policyData.reload}
        />
      ) : null}
      {policy ? (
        <div className="admin-configpreview">
          {GROUPS.map(({ group, settings }) => (
            <Fragment key={group}>
              <div className="pcap">{group}</div>
              {settings.map((setting) => (
                <div className="prow" key={setting}>
                  <span className="k">{SPECS[setting].label}</span>
                  <span className="v">
                    {SPECS[setting].show(policy[setting])}
                  </span>
                </div>
              ))}
            </Fragment>
          ))}
        </div>
      ) : null}
      {editing && policy ? (
        <TwoStepPolicyDialog
          saved={policy}
          writeBlocked={status !== 'connected'}
          onClose={(wrote) => {
            setEditing(false);
            if (wrote) policyData.reload();
          }}
        />
      ) : null}
    </SettingsCard>
  );
}

function TwoStepPolicyDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: TwoStepPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the settings). */
  onClose: (wrote: boolean) => void;
}) {
  const [texts, setTexts] = useState<Record<TwoStepSetting, string>>(
    () =>
      Object.fromEntries(
        TWO_STEP_SETTINGS.map((setting) => [setting, String(saved[setting])]),
      ) as Record<TwoStepSetting, string>,
  );
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const values = Object.fromEntries(
    TWO_STEP_SETTINGS.map((setting) => [
      setting,
      parseWhole(texts[setting], SPECS[setting].min, SPECS[setting].max),
    ]),
  ) as Record<TwoStepSetting, number | null>;
  const invalid = TWO_STEP_SETTINGS.some((setting) => values[setting] === null);
  // Only the valid settings whose value differs from the stored policy
  // (an invalid entry blocks the save on its own).
  const patch: Partial<Record<TwoStepSetting, number>> = {};
  for (const setting of TWO_STEP_SETTINGS) {
    const value = values[setting];
    if (value !== null && value !== saved[setting]) patch[setting] = value;
  }

  const guard = useCloseGuard({
    dirty: invalid || Object.keys(patch).length > 0,
    busy,
    onClose: () => onClose(wrote),
  });

  const submit = async () => {
    if (busy || writeBlocked || invalid) return;
    if (Object.keys(patch).length === 0) {
      onClose(wrote);
      return;
    }
    setBusy(true);
    setServerError(null);
    setWrote(true);
    try {
      await updateTwoStepPolicy(patch);
      onClose(true);
    } catch (error) {
      setServerError(
        signInWriteOutcomeUnknown(error) || !(error instanceof ApiError)
          ? UNKNOWN_OUTCOME
          : error.message,
      );
      setBusy(false);
    }
  };

  const title = 'Two-step sign-in settings';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="sub">
        Authenticator app codes always have 6 digits and change every 30 seconds
        — the standard every app uses. A lock lasts as long as the lock duration
        under User sign-in.
      </div>
      <div className="admin-form">
        {GROUPS.map(({ group, settings }) => (
          <fieldset key={group} className="admin-fieldgroup">
            <legend>{group}</legend>
            {settings.map((setting) => (
              <div key={setting}>
                <AdminField label={SPECS[setting].field}>
                  <input
                    className="field mono"
                    type="number"
                    min={SPECS[setting].min}
                    max={SPECS[setting].max}
                    step={1}
                    value={texts[setting]}
                    disabled={busy}
                    onChange={(event) => {
                      const text = event.target.value;
                      setTexts((current) => ({ ...current, [setting]: text }));
                    }}
                  />
                </AdminField>
                {values[setting] === null ? (
                  <div className="err" role="alert">
                    {SPECS[setting].error}
                  </div>
                ) : null}
              </div>
            ))}
          </fieldset>
        ))}
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
          You have unsaved changes to the two-step sign-in settings.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}
