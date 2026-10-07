import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api/client';
import {
  PERMISSIONS,
  createRole,
  listRoles,
  updateRole,
} from '../../api/roles';
import type { Permission, Role } from '../../api/roles';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  CORRECTION_PERMISSIONS,
  PERMISSION_LABELS,
  ROLE_PERMISSION_GROUPS,
} from './permissions';
import { AdminField, SectionHeader, ServerErrorNote } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Roles & permissions: named, editable roles and the
// permissions each one grants (initially Administrator, Manager and
// Operator with exactly the PROJECT_PROFILE §20 capabilities). The
// standard table + editor pattern: roles are created and renamed here,
// never deleted. Permissions are checked only where a route requires
// them (so far setting passwords and the user sign-in settings), and
// the section says so.
//
// The editor edits the four permission groups only and sends grant /
// revoke deltas computed over those groups: the correction permissions
// are shown read-only and edited only in Policies → Correction
// permissions, so this editor never grants or revokes one and never
// reverts a concurrent change made there.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'roles')?.subtitle ?? '';

const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the role before trying again.';

const EDITABLE_PERMISSIONS: readonly Permission[] =
  ROLE_PERMISSION_GROUPS.flatMap((group) => group.permissions);

type PendingDialog = { kind: 'new' } | { kind: 'edit'; role: Role };

export function RolesSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const rolesData = useApiData(listRoles);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  const ready = rolesData.state.status === 'ready';

  const closeDialog = (wroteAny: boolean) => {
    setDialog(null);
    if (wroteAny) rolesData.reload();
  };

  let body;
  if (rolesData.state.status === 'loading') {
    body = <LoadingState label="Loading roles" />;
  } else if (rolesData.state.status === 'error') {
    body = (
      <ErrorState
        message="Role data could not be loaded."
        detail={rolesData.state.message}
        onRetry={rolesData.reload}
      />
    );
  } else {
    const roles = rolesData.state.data;
    body = (
      <>
        {roles.length === 0 ? (
          <EmptyState message="No roles configured yet." />
        ) : (
          <table className="ad-table">
            <thead>
              <tr>
                <th>Role</th>
                <th>Permissions</th>
                <th>Users</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr
                  key={role.id}
                  className="selrow"
                  onClick={() => setDialog({ kind: 'edit', role })}
                >
                  <td>
                    <button className="rowbtn" aria-label={`Edit ${role.name}`}>
                      <b>{role.name}</b>
                    </button>
                  </td>
                  <td data-label="Permissions">
                    {role.permissions.length} of {PERMISSIONS.length}
                  </td>
                  <td data-label="Users">{role.userCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="ad-notice">
          Each user holds one role. PartFlow checks permissions only for setting
          passwords and changing user sign-in settings so far; the other
          permissions are recorded here and are not checked yet. Correction
          permissions are set in Policies → Correction permissions. Roles are
          renamed, never deleted.
        </div>
      </>
    );
  }

  return (
    <>
      <SectionHeader
        title="Roles & permissions"
        subtitle={SUBTITLE}
        action={
          <button
            className="btn primary"
            disabled={!ready || writeBlocked}
            onClick={() => setDialog({ kind: 'new' })}
          >
            + New role
          </button>
        }
      />
      {body}
      {dialog ? (
        <RoleDialog
          role={dialog.kind === 'edit' ? dialog.role : undefined}
          writeBlocked={writeBlocked}
          onClose={closeDialog}
        />
      ) : null}
    </>
  );
}

function RoleDialog({
  role,
  writeBlocked,
  onClose,
}: {
  role?: Role;
  writeBlocked: boolean;
  /** Close request; `wroteAny` = a write was sent. */
  onClose: (wroteAny: boolean) => void;
}) {
  const [name, setName] = useState(role?.name ?? '');
  // Checked permissions of the four editable groups.
  const [checked, setChecked] = useState<ReadonlySet<Permission>>(
    () =>
      new Set(
        role?.permissions.filter((key) => EDITABLE_PERMISSIONS.includes(key)),
      ),
  );
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const wroteAny = useRef(false);

  // The Name field takes focus once the dialog opened (from an effect,
  // after ModalDialog's own — see UsersSection).
  const nameField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameField.current?.focus();
  }, []);

  const requestClose = () => {
    if (busy) return;
    onClose(wroteAny.current);
  };

  const trimmedName = name.trim();
  const nameInvalid = !trimmedName;
  const title = role ? 'Edit role' : 'New role';
  const heldCorrections = role
    ? CORRECTION_PERMISSIONS.filter((key) => role.permissions.includes(key))
    : [];

  const toggle = (key: Permission, on: boolean) => {
    setChecked((current) => {
      const next = new Set(current);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const submit = async () => {
    if (nameInvalid) {
      setAttempted(true);
      return;
    }
    const checkedList = EDITABLE_PERMISSIONS.filter((key) => checked.has(key));
    let patch: Parameters<typeof updateRole>[1] | null = null;
    if (role) {
      // Deltas over the four editable groups only.
      const held = new Set(role.permissions);
      const grant = checkedList.filter((key) => !held.has(key));
      const revoke = EDITABLE_PERMISSIONS.filter(
        (key) => held.has(key) && !checked.has(key),
      );
      patch = {
        ...(trimmedName !== role.name ? { name: trimmedName } : {}),
        ...(grant.length ? { grantPermissions: grant } : {}),
        ...(revoke.length ? { revokePermissions: revoke } : {}),
      };
      if (Object.keys(patch).length === 0) {
        onClose(false);
        return;
      }
    }
    setBusy(true);
    setServerError(null);
    try {
      wroteAny.current = true;
      if (role && patch) {
        await updateRole(role.id, patch);
      } else {
        await createRole({ name: trimmedName, permissions: checkedList });
      }
      onClose(true);
    } catch (error) {
      setServerError(
        error instanceof ApiError ? error.message : UNKNOWN_OUTCOME_MESSAGE,
      );
      setBusy(false);
    }
  };

  return (
    <ModalDialog label={title} onClose={requestClose} size="wide">
      <h3>{title}</h3>
      <div className="ad-form">
        <AdminField label="Name">
          <input
            ref={nameField}
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Process Engineer"
          />
        </AdminField>
        {nameInvalid && attempted ? (
          <div className="err" role="alert">
            A role name is required.
          </div>
        ) : null}
        {ROLE_PERMISSION_GROUPS.map((group) => (
          <fieldset key={group.label} className="ad-permgroup">
            <legend>{group.label}</legend>
            {group.permissions.map((key) => (
              <label key={key} className="ad-check">
                <input
                  type="checkbox"
                  checked={checked.has(key)}
                  disabled={busy}
                  onChange={(event) => toggle(key, event.target.checked)}
                />
                <span>{PERMISSION_LABELS[key]}</span>
              </label>
            ))}
          </fieldset>
        ))}
        {role ? (
          <p className="ad-fieldhelp">
            Correction permissions:{' '}
            {heldCorrections.length
              ? heldCorrections.map((key) => PERMISSION_LABELS[key]).join(', ')
              : 'none'}{' '}
            — set in Policies → Correction permissions.
          </p>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={() => void submit()}
        >
          {role ? 'Save changes' : 'Add role'}
        </button>
      </div>
    </ModalDialog>
  );
}
