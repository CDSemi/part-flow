import { useState } from 'react';

import { ApiError } from '../../api/client';
import {
  getCorrectionPermissionsPolicy,
  updateUndoReasonRequired,
} from '../../api/policies';
import { listRoles, updateRole } from '../../api/roles';
import type { Permission, Role } from '../../api/roles';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  CORRECTION_PERMISSIONS,
  PERMISSION_LABELS,
  permissionChoiceLabel,
} from './permissions';
import {
  PolicySwitch,
  ReadOnlyValues,
  SectionHeader,
  ServerErrorNote,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Correction permissions (Phase 13; GUI_DESIGN §9
// Policies, PROJECT_PROFILE §16 "require a reason when configured"):
// the real Undo reason policy — one global On/Off switch (default Off)
// stored in the server's application policy
// (`/api/policies/correction-permissions`), audited and enforced by the
// server's Undo command — and below it the real role × correction-
// permission table (who may undo or correct), the only editor of the
// four correction permissions of each role (`/api/roles`). Both save on
// click and re-read the stored value. The two panels load
// independently, so either keeps working when the other cannot load.
// Changing either needs the Manage correction permissions permission
// (the server checks it; a change of only correction permissions needs
// no other permission); without it both read as text. The correction
// permissions themselves are not checked yet, and the section says so.

// No answer, a timeout or a 5xx: the write may or may not have
// committed, so the copy never claims that nothing was changed.
const SWITCH_OUTCOME_UNKNOWN =
  'The server did not answer — this change may or may not have been saved. The switch shows the stored setting once it can be read again; check it before trying again.';
const TABLE_OUTCOME_UNKNOWN =
  'The server did not answer — this change may or may not have been saved. The table shows the stored permissions once they can be read again; check them before trying again.';

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'correction-permissions')
    ?.subtitle ?? '';

export function CorrectionPermissionsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const canWrite = useSession().can('MANAGE_CORRECTION_PERMISSIONS');

  return (
    <>
      <SectionHeader title="Correction permissions" subtitle={SUBTITLE} />
      {canWrite ? null : (
        <ViewOnlyNote permission="MANAGE_CORRECTION_PERMISSIONS" />
      )}
      <div className="ad-config">
        <h2>Undo reason</h2>
        <p className="ad-confighelp">
          When On, every Undo at a Scan Station asks for a reason before the
          reversal can be confirmed, and a reversal without a reason is refused.
          The reason is recorded with the reversal and shown in Tracking. When
          Off, Undo asks for no reason.
        </p>
        <UndoReasonPanel canWrite={canWrite} writeBlocked={writeBlocked} />
        <h2>Who may undo or correct</h2>
        <p className="ad-confighelp">
          Choose which roles hold each correction permission. The correction
          permissions are not checked yet; Perform quantity corrections and
          Perform authorized historical corrections grant nothing yet because
          PartFlow has no such correction.
        </p>
        <CorrectionRoleTable canWrite={canWrite} writeBlocked={writeBlocked} />
        <p className="ad-confighelp">
          Undo recent eligible scans covers exactly the actions the Scan
          Station&apos;s Undo offers — there is no extra time limit.
        </p>
      </div>
    </>
  );
}

/** The Undo reason switch, toggled from the last read (as text for a
 * user who may not change it). */
function UndoReasonPanel({
  canWrite,
  writeBlocked,
}: {
  canWrite: boolean;
  writeBlocked: boolean;
}) {
  const policyData = useApiData(getCorrectionPermissionsPolicy);
  const [busy, setBusy] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  if (policyData.state.status === 'loading') {
    return <LoadingState label="Loading section" />;
  }
  if (policyData.state.status === 'error') {
    return (
      <ErrorState
        message="Correction permission settings could not be loaded."
        detail={policyData.state.message}
        onRetry={policyData.reload}
      />
    );
  }

  const required = policyData.state.data.undoReasonRequired;

  if (!canWrite) {
    return (
      <ReadOnlyValues
        rows={[
          {
            label: 'Require a reason for every Undo',
            value: required ? 'On' : 'Off',
          },
        ]}
      />
    );
  }

  // Toggled from the last read; the server's answer is re-read so the
  // switch shows the stored value.
  const toggle = async () => {
    if (writeBlocked || busy) return;
    setBusy(true);
    setSwitchError(null);
    try {
      await updateUndoReasonRequired(!required);
      policyData.reload();
    } catch (error) {
      if (error instanceof ApiError && !writeOutcomeUnknown(error)) {
        setSwitchError(error.message);
      } else {
        // Re-read in the background: the stored value replaces the
        // switch when it can be read, and a failed re-read keeps the
        // switch and this note on screen.
        setSwitchError(SWITCH_OUTCOME_UNKNOWN);
        policyData.revalidate();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="ad-switchlist">
        <PolicySwitch
          label="Require a reason for every Undo"
          description="Applies to every Area and every Scan Station."
          ariaLabel="Require a reason for every Undo"
          on={required}
          disabled={writeBlocked || busy}
          onToggle={() => void toggle()}
        />
      </div>
      <ServerErrorNote message={switchError} />
    </>
  );
}

/**
 * The role × correction-permission table. A click immediately grants or
 * revokes that one permission of that one role (a delta, so a
 * concurrent change to another permission is never reverted), toggled
 * from the last read; the roles are then re-read so every checkbox
 * shows the stored value. A user who may not change it reads each cell
 * as text.
 */
function CorrectionRoleTable({
  canWrite,
  writeBlocked,
}: {
  canWrite: boolean;
  writeBlocked: boolean;
}) {
  const rolesData = useApiData(listRoles);
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);

  if (rolesData.state.status === 'loading') {
    return <LoadingState label="Loading roles" />;
  }
  if (rolesData.state.status === 'error') {
    return (
      <ErrorState
        message="Role permissions could not be loaded."
        detail={rolesData.state.message}
        onRetry={rolesData.reload}
      />
    );
  }

  const roles = rolesData.state.data;

  const toggle = async (role: Role, key: Permission) => {
    if (writeBlocked || busy) return;
    const held = role.permissions.includes(key);
    setBusy(true);
    setWriteError(null);
    try {
      await updateRole(
        role.id,
        held ? { revokePermissions: [key] } : { grantPermissions: [key] },
      );
      rolesData.reload();
    } catch (error) {
      setWriteError(
        error instanceof ApiError && !writeOutcomeUnknown(error)
          ? error.message
          : TABLE_OUTCOME_UNKNOWN,
      );
      // Re-read in the background so each checkbox shows the stored
      // value; a failed re-read keeps the table and this note on screen.
      rolesData.revalidate();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <table className="ad-table">
        <thead>
          <tr>
            <th>Role</th>
            {CORRECTION_PERMISSIONS.map((key) => (
              <th key={key}>{permissionChoiceLabel(key)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.id}>
              <td>
                <b>{role.name}</b>
              </td>
              {CORRECTION_PERMISSIONS.map((key) => (
                <td
                  key={key}
                  className="ad-matrixcell"
                  data-label={PERMISSION_LABELS[key]}
                >
                  {canWrite ? (
                    <input
                      type="checkbox"
                      aria-label={`${PERMISSION_LABELS[key]} — ${role.name}`}
                      checked={role.permissions.includes(key)}
                      disabled={writeBlocked || busy}
                      onChange={() => void toggle(role, key)}
                    />
                  ) : role.permissions.includes(key) ? (
                    'Yes'
                  ) : (
                    '—'
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <ServerErrorNote message={writeError} />
    </>
  );
}
