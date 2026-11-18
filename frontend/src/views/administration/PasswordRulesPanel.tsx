import { useState } from 'react';

import { ApiError } from '../../api/client';
import {
  getPasswordRulesPolicy,
  updatePasswordRulesPolicy,
} from '../../api/policies';
import type { PasswordRulesPolicy } from '../../api/policies';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  AdminField,
  OnOffValue,
  PolicySwitch,
  ServerErrorNote,
  SettingsCard,
} from './section-widgets';

// Administration → Security → Password rules (owner request of
// 2026-11-17): the rules every new password must satisfy — set at
// first-run setup, with Change password, by an administrator's Set
// password… or by the recovery command — a minimum and a maximum length
// (4 to 256 characters each, the minimum never above the maximum) and
// whether a digit, a special character, an uppercase letter and a
// lowercase letter are required. A stored password is never checked
// again, so tightening the rules signs nobody out. Every signed-in user
// reads it; users whose role may configure system settings edit it. Save
// sends only the changed rules (the server merges them and validates
// every value).

const LENGTH_RANGE = [4, 256] as const;
const MIN_ERROR =
  'The minimum password length must be a whole number of characters from 4 to 256.';
const MAX_ERROR =
  'The maximum password length must be a whole number of characters from 4 to 256.';
const ORDER_ERROR =
  'The minimum password length cannot be greater than the maximum password length.';
const UNKNOWN_OUTCOME =
  'The server did not answer — the rules may or may not have been saved. Close this window to reload them before trying again.';

type Requirement =
  'requireDigit' | 'requireSpecial' | 'requireUppercase' | 'requireLowercase';

const REQUIREMENTS: { key: Requirement; label: string; help: string }[] = [
  {
    key: 'requireDigit',
    label: 'Require a digit',
    help: 'Such as 0 to 9.',
  },
  {
    key: 'requireSpecial',
    label: 'Require a special character',
    help: 'A punctuation mark or a symbol, such as ! # % or *. A space does not count.',
  },
  {
    key: 'requireUppercase',
    label: 'Require an uppercase letter',
    help: 'Such as A to Z, or an uppercase letter of another alphabet.',
  },
  {
    key: 'requireLowercase',
    label: 'Require a lowercase letter',
    help: 'Such as a to z, or a lowercase letter of another alphabet.',
  },
];

/** The whole number in `text` within the length range, else null (never
 * rounded or clamped). */
function parseLength(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) &&
    value >= LENGTH_RANGE[0] &&
    value <= LENGTH_RANGE[1]
    ? value
    : null;
}

export function PasswordRulesPanel() {
  const session = useSession();
  const canEdit = session.can('CONFIGURE_SYSTEM_SETTINGS');
  const { status } = useConnectivity();
  const policyData = useApiData(getPasswordRulesPolicy);
  const [editing, setEditing] = useState(false);
  const { state } = policyData;
  const policy = state.status === 'ready' ? state.data : null;

  return (
    <SettingsCard
      title="Password rules"
      description="What a new password must contain. Passwords already in use keep working until they are changed."
      edit={
        canEdit && policy
          ? {
              label: 'Edit password rules…',
              disabled: status !== 'connected',
              onClick: () => setEditing(true),
            }
          : undefined
      }
    >
      {state.status === 'loading' ? (
        <LoadingState label="Loading password rules" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The password rules could not be loaded."
          detail={state.message}
          onRetry={policyData.reload}
        />
      ) : null}
      {policy ? (
        <div className="admin-configpreview">
          <div className="prow">
            <span className="k">Password length</span>
            <span className="v">{`${policy.minLength} to ${policy.maxLength} characters`}</span>
          </div>
          {REQUIREMENTS.map((requirement) => (
            <div className="prow" key={requirement.key}>
              <span className="k">{requirement.label}</span>
              <span className="v">
                <OnOffValue on={policy[requirement.key]} />
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {editing && policy ? (
        <PasswordRulesDialog
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

function PasswordRulesDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: PasswordRulesPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the rules). */
  onClose: (wrote: boolean) => void;
}) {
  const [minText, setMinText] = useState(String(saved.minLength));
  const [maxText, setMaxText] = useState(String(saved.maxLength));
  const [required, setRequired] = useState<Record<Requirement, boolean>>({
    requireDigit: saved.requireDigit,
    requireSpecial: saved.requireSpecial,
    requireUppercase: saved.requireUppercase,
    requireLowercase: saved.requireLowercase,
  });
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const minLength = parseLength(minText);
  const maxLength = parseLength(maxText);
  const misordered =
    minLength !== null && maxLength !== null && minLength > maxLength;
  const invalid = minLength === null || maxLength === null || misordered;
  // Only the valid rules whose value differs from the stored policy (an
  // invalid entry blocks the save on its own).
  const patch: Partial<Omit<PasswordRulesPolicy, 'updatedAt'>> = {
    ...(minLength !== null && minLength !== saved.minLength
      ? { minLength }
      : {}),
    ...(maxLength !== null && maxLength !== saved.maxLength
      ? { maxLength }
      : {}),
  };
  for (const { key } of REQUIREMENTS) {
    if (required[key] !== saved[key]) patch[key] = required[key];
  }

  // Cancel, Escape and the backdrop are ignored while a save is in
  // flight; otherwise a change — or an entry that is not a valid value —
  // asks first (useCloseGuard).
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
      await updatePasswordRulesPolicy(patch);
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

  const title = 'Password rules';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="sub">
        The rules apply each time a password is set: at first-run setup, with
        Change password, when an administrator sets one, and with the recovery
        command. Passwords already in use keep working.
      </div>
      <div className="admin-form">
        <AdminField label="Minimum length (characters)">
          <input
            className="field mono"
            type="number"
            min={LENGTH_RANGE[0]}
            max={LENGTH_RANGE[1]}
            step={1}
            value={minText}
            disabled={busy}
            onChange={(event) => setMinText(event.target.value)}
          />
        </AdminField>
        {minLength === null ? (
          <div className="err" role="alert">
            {MIN_ERROR}
          </div>
        ) : null}
        <AdminField label="Maximum length (characters)">
          <input
            className="field mono"
            type="number"
            min={LENGTH_RANGE[0]}
            max={LENGTH_RANGE[1]}
            step={1}
            value={maxText}
            disabled={busy}
            onChange={(event) => setMaxText(event.target.value)}
          />
        </AdminField>
        {maxLength === null ? (
          <div className="err" role="alert">
            {MAX_ERROR}
          </div>
        ) : null}
        {misordered ? (
          <div className="err" role="alert">
            {ORDER_ERROR}
          </div>
        ) : null}
        <div className="admin-switchlist">
          {REQUIREMENTS.map((requirement) => (
            <PolicySwitch
              key={requirement.key}
              label={requirement.label}
              description={requirement.help}
              ariaLabel={requirement.label}
              on={required[requirement.key]}
              disabled={busy}
              onToggle={() =>
                setRequired((current) => ({
                  ...current,
                  [requirement.key]: !current[requirement.key],
                }))
              }
            />
          ))}
        </div>
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
          You have unsaved changes to the password rules.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}
