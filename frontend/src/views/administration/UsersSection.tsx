import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api/client';
import { listRoles } from '../../api/roles';
import type { Role } from '../../api/roles';
import { useApiData } from '../../api/use-api-data';
import {
  createUser,
  listUsers,
  removeUserAvatar,
  updateUser,
  uploadUserAvatar,
  userAvatarUrl,
} from '../../api/users';
import type { SignInState, User } from '../../api/users';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { Avatar } from '../../components/Avatar';
import { ModalDialog } from '../../components/ModalDialog';
import {
  ImageUploadError,
  prepareImageUpload,
} from '../../components/image-upload';
import { useToastNotice } from '../../components/toast-notice';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { roleHoldsProtected } from './permissions';
import {
  ActiveField,
  AdminField,
  RowOpener,
  SectionHeader,
  ServerErrorNote,
  StatusPill,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';
import { SetPasswordDialog } from './SetPasswordDialog';
import { canonicalLoginName, loginNameError } from './user-login';

// Administration → Users: application accounts for Management,
// Administration and the other non-Scan-Station views — separate from
// Workers, who scan at the Scan Stations. The standard table + editor
// pattern: Users are created and edited here and deactivated, never
// deleted. Users sign in since Phase 14 slice 1, and the server checks
// every Administration permission since slice 2 and every Management
// permission since slice 3; the section says what is checked so far. Without the Manage users and roles permission the
// section is view-only (hidden, not disabled). A user administrator who
// may not manage correction permissions may still rename users whose
// role holds a correction permission or the permission to manage them,
// but neither changes their role or activity nor sets their password,
// and gives only roles without those permissions (the server's
// permission-management guard decides). The server owns every rule
// (login-name canonical form and uniqueness, image limits); the editor
// mirrors the login-name rule only to answer early.
//
// A save is up to two audited writes — the profile, then an avatar
// change on its own binary endpoint — so the editor handles a
// partially completed save explicitly. An edit sends only the fields
// the operator changed, so an editor opened before another
// administrator's change never reverts it. The list reloads only when
// the editor closes, so a failing refresh never unmounts an open
// editor.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'users')?.subtitle ?? '';

const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the user before trying again.';

const PROTECTED_ROLE_HINT =
  'Roles that hold correction permissions or the permission to manage them can be given only by a user who may manage correction permissions.';

const SIGN_IN_STATE_LABELS: Record<SignInState, string> = {
  NO_PASSWORD: 'No password',
  TEMPORARY_PASSWORD: 'Temporary password',
  PASSWORD_SET: 'Password set',
  LOCKED: 'Locked',
};

type PendingDialog =
  | { kind: 'new' }
  | { kind: 'edit'; user: User }
  | { kind: 'password'; user: User };

export function UsersSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const session = useSession();
  // User administrators see how each user can sign in and may give any
  // other user a password; the server checks the same permission.
  const administersUsers = session.can('MANAGE_USERS_AND_ROLES');
  // Changing who holds a correction permission or the permission to
  // manage them also needs this one (the server's guard).
  const managesCorrections = session.can('MANAGE_CORRECTION_PERMISSIONS');
  const signedInId = session.user?.id ?? null;
  const usersData = useApiData(listUsers);
  const rolesData = useApiData(listRoles);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  const { showNotice, noticeElement } = useToastNotice();

  // The list answer depends on who is signed in (the sign-in states are
  // sent to user administrators only): read it again when that changes
  // (never while signed out — the server refuses the read then).
  const reloadUsersList = usersData.reload;
  const listedFor = useRef(signedInId);
  useEffect(() => {
    if (signedInId === null || listedFor.current === signedInId) return;
    listedFor.current = signedInId;
    reloadUsersList();
  }, [signedInId, reloadUsersList]);
  const ready =
    usersData.state.status === 'ready' && rolesData.state.status === 'ready';

  const reloadUsers = usersData.reload;
  const reloadRoles = rolesData.reload;
  const reloadBoth = useCallback(() => {
    reloadUsers();
    reloadRoles();
  }, [reloadUsers, reloadRoles]);

  const closeDialog = (wroteAny: boolean) => {
    setDialog(null);
    if (wroteAny) usersData.reload();
  };

  let body;
  if (
    usersData.state.status === 'error' ||
    rolesData.state.status === 'error'
  ) {
    const message =
      usersData.state.status === 'error'
        ? usersData.state.message
        : rolesData.state.status === 'error'
          ? rolesData.state.message
          : undefined;
    body = (
      <ErrorState
        message="User data could not be loaded."
        detail={message}
        onRetry={reloadBoth}
      />
    );
  } else if (
    usersData.state.status === 'loading' ||
    rolesData.state.status === 'loading'
  ) {
    body = <LoadingState label="Loading users" />;
  } else {
    const users = usersData.state.data;
    const roles = rolesData.state.data;
    // A role this list does not know is treated as protected.
    const inProtectedRole = (user: User) => {
      const role = roles.find((candidate) => candidate.id === user.roleId);
      return role === undefined || roleHoldsProtected(role);
    };
    const guarded = administersUsers && !managesCorrections;
    body = (
      <>
        {users.length === 0 ? (
          <EmptyState message="No users configured yet." />
        ) : (
          <table className="ad-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Login name</th>
                <th>Role</th>
                {administersUsers ? <th>Sign-in</th> : null}
                <th>Status</th>
                {administersUsers ? <th aria-label="Actions" /> : null}
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr
                  key={user.id}
                  className={administersUsers ? 'selrow' : undefined}
                  onClick={
                    administersUsers
                      ? () => setDialog({ kind: 'edit', user })
                      : undefined
                  }
                >
                  <td>
                    <RowOpener
                      editable={administersUsers}
                      label={`Edit ${user.displayName}`}
                    >
                      <span className="ad-worker">
                        <Avatar
                          name={user.displayName}
                          size="sm"
                          src={userAvatarUrl(user)}
                        />
                        <b>{user.displayName}</b>
                      </span>
                    </RowOpener>
                  </td>
                  <td className="mono" data-label="Login name">
                    {user.loginName}
                  </td>
                  <td data-label="Role">{user.roleName}</td>
                  {administersUsers ? (
                    <td data-label="Sign-in">
                      {user.signInState
                        ? SIGN_IN_STATE_LABELS[user.signInState]
                        : '—'}
                    </td>
                  ) : null}
                  <td data-label="Status">
                    <StatusPill active={user.isActive} />
                  </td>
                  {administersUsers ? (
                    <td>
                      {user.id !== signedInId &&
                      !(guarded && inProtectedRole(user)) ? (
                        <button
                          className="btn"
                          aria-label={`Set password for ${user.displayName}`}
                          onClick={(event) => {
                            // The row itself opens the editor.
                            event.stopPropagation();
                            setDialog({ kind: 'password', user });
                          }}
                        >
                          Set password…
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="ad-notice">
          Users sign in with their login name and a password. Use Set password…
          to give a user a password. PartFlow checks permissions in
          Administration and Management; Scan Station screens stay open to
          anyone who can reach PartFlow until station devices are enrolled.
          Workers who scan at the Scan Stations are managed in Workers, not
          here. Users are deactivated, never deleted; deactivating a user signs
          them out.
        </div>
        {guarded && users.some(inProtectedRole) ? (
          <p className="ad-confighelp">
            Users whose role holds correction permissions or the permission to
            manage them can be renamed here; their role, whether they are active
            and their password can be changed only by a user who may manage
            correction permissions.
          </p>
        ) : null}
      </>
    );
  }

  return (
    <>
      <SectionHeader
        title="Users"
        subtitle={SUBTITLE}
        action={
          administersUsers ? (
            <button
              className="btn primary"
              disabled={!ready || writeBlocked}
              onClick={() => setDialog({ kind: 'new' })}
            >
              + New user
            </button>
          ) : undefined
        }
      />
      {administersUsers ? null : (
        <ViewOnlyNote permission="MANAGE_USERS_AND_ROLES" />
      )}
      {body}
      {dialog?.kind === 'password' ? (
        <SetPasswordDialog
          user={dialog.user}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSet={(user) => {
            setDialog(null);
            showNotice(`Password set for ${user.displayName}.`);
            usersData.reload();
          }}
        />
      ) : null}
      {dialog &&
      dialog.kind !== 'password' &&
      rolesData.state.status === 'ready' ? (
        <UserDialog
          user={dialog.kind === 'edit' ? dialog.user : undefined}
          roles={rolesData.state.data}
          managesCorrections={managesCorrections}
          writeBlocked={writeBlocked}
          onClose={closeDialog}
        />
      ) : null}
      {noticeElement}
    </>
  );
}

/** The avatar change staged in the editor, applied on Save. */
type StagedAvatar =
  | { kind: 'upload'; image: Blob; previewUrl: string }
  | { kind: 'remove' }
  | null;

function UserDialog({
  user,
  roles,
  managesCorrections,
  writeBlocked,
  onClose,
}: {
  user?: User;
  roles: Role[];
  /** The user may manage correction permissions; otherwise only roles
   * without them are offered, and a user in such a role keeps their
   * role and activity (shown as text). */
  managesCorrections: boolean;
  writeBlocked: boolean;
  /** Close request; `wroteAny` = at least one write was sent. */
  onClose: (wroteAny: boolean) => void;
}) {
  // The saved record the editor works against: undefined until a new
  // User exists, then the server's latest answer. An edit's changed
  // fields are computed against it.
  const [saved, setSaved] = useState<User | undefined>(user);
  const [name, setName] = useState(user?.displayName ?? '');
  const [login, setLogin] = useState(user?.loginName ?? '');
  const [roleId, setRoleId] = useState<number | null>(user?.roleId ?? null);
  const [isActive, setIsActive] = useState(user?.isActive ?? true);
  const [staged, setStaged] = useState<StagedAvatar>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const wroteAny = useRef(false);
  const closed = useRef(false);

  // The Name field takes focus once the dialog opened. Focused from an
  // effect (after ModalDialog's own), never `autoFocus`: ModalDialog
  // records the opener only while focus is still outside the dialog,
  // and closing must return focus to the opener.
  const nameField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameField.current?.focus();
  }, []);

  // Release the staged preview's object URL when it is replaced or the
  // editor unmounts.
  useEffect(() => {
    if (staged?.kind !== 'upload') return undefined;
    const url = staged.previewUrl;
    return () => URL.revokeObjectURL(url);
  }, [staged]);

  const close = () => {
    if (closed.current) return;
    closed.current = true;
    onClose(wroteAny.current);
  };

  // Cancel, Escape and the backdrop are ignored while a save is in
  // flight: its writes cannot be recalled, so the editor stays open
  // until they settle.
  const requestClose = () => {
    if (busy) return;
    close();
  };

  const trimmedName = name.trim();
  const canonical = canonicalLoginName(login);
  const nameInvalid = !trimmedName;
  const loginInvalid = loginNameError(login);
  const roleInvalid = roleId === null;
  const title = saved ? 'Edit user' : 'New user';
  // Without Manage correction permissions: a saved user in a protected
  // role keeps it and their activity; otherwise only unprotected roles
  // are offered.
  const savedRole = saved
    ? roles.find((role) => role.id === saved.roleId)
    : undefined;
  const roleFixed =
    !managesCorrections &&
    saved !== undefined &&
    (savedRole === undefined || roleHoldsProtected(savedRole));
  const choosableRoles = managesCorrections
    ? roles
    : roles.filter((role) => !roleHoldsProtected(role));
  const noRoleToGive = !saved && choosableRoles.length === 0;
  const hasStoredAvatar = saved?.avatarUpdatedAt != null;
  let avatarSrc: string | null = null;
  if (staged?.kind === 'upload') avatarSrc = staged.previewUrl;
  else if (staged === null && saved) avatarSrc = userAvatarUrl(saved);
  const showRemove =
    staged?.kind === 'upload' || (hasStoredAvatar && staged === null);
  const controlsBlocked = writeBlocked || busy || preparing;

  const chooseImage = async (file: File | undefined) => {
    if (!file) return;
    setImageError(null);
    setPreparing(true);
    try {
      const image = await prepareImageUpload(file);
      setStaged({
        kind: 'upload',
        image,
        previewUrl: URL.createObjectURL(image),
      });
    } catch (error) {
      setImageError(
        error instanceof ImageUploadError
          ? error.message
          : 'Choose a PNG, JPEG or WebP image.',
      );
    } finally {
      setPreparing(false);
    }
  };

  const removeAvatar = () => {
    setImageError(null);
    setStaged(hasStoredAvatar ? { kind: 'remove' } : null);
  };

  const submit = async () => {
    if (nameInvalid || loginInvalid || roleId === null) {
      setAttempted(true);
      return;
    }
    // Only the fields whose value differs from the saved record.
    const delta = saved
      ? {
          ...(trimmedName !== saved.displayName
            ? { displayName: trimmedName }
            : {}),
          ...(canonical !== saved.loginName ? { loginName: canonical } : {}),
          ...(roleId !== saved.roleId ? { roleId } : {}),
          ...(isActive !== saved.isActive ? { isActive } : {}),
        }
      : null;
    const sendProfile = delta === null || Object.keys(delta).length > 0;
    if (!sendProfile && staged === null) {
      close();
      return;
    }
    setBusy(true);
    setServerError(null);
    let step: 'profile' | 'avatar' = 'profile';
    let profileChanged = false;
    try {
      wroteAny.current = true;
      let record = saved;
      // Step 1 — the profile: create, or only the changed fields.
      if (!saved) {
        record = await createUser({
          displayName: trimmedName,
          loginName: canonical,
          roleId,
        });
        profileChanged = true;
        setSaved(record);
      } else if (delta && sendProfile) {
        record = await updateUser(saved.id, delta);
        profileChanged = true;
        setSaved(record);
      }

      // Step 2 — the staged avatar change, its own audited write.
      step = 'avatar';
      if (record && staged?.kind === 'upload') {
        record = await uploadUserAvatar(record.id, staged.image);
      } else if (record && staged?.kind === 'remove') {
        record = await removeUserAvatar(record.id);
      }
      setSaved(record);
      setStaged(null);
      close();
    } catch (error) {
      if (!(error instanceof ApiError)) {
        // No answer: the write may or may not have committed.
        setServerError(UNKNOWN_OUTCOME_MESSAGE);
      } else if (step === 'profile') {
        setServerError(error.message);
      } else if (profileChanged) {
        setServerError(
          `The user was saved, but the avatar could not be updated: ${error.message}`,
        );
      } else {
        setServerError(`The avatar could not be updated: ${error.message}`);
      }
      setBusy(false);
    }
  };

  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      <div className="ad-form">
        <AdminField label="Name">
          <input
            ref={nameField}
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Jane Doe"
          />
        </AdminField>
        {nameInvalid && attempted ? (
          <div className="err" role="alert">
            A name is required.
          </div>
        ) : null}
        <AdminField label="Login name">
          <input
            className="field mono"
            value={login}
            onChange={(event) => setLogin(event.target.value)}
            placeholder="e.g. jdoe"
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        <div className="ad-fieldnotes">
          <div aria-live="polite">
            {canonical && canonical !== login ? (
              <p className="ad-fieldnote">
                Saved as: <span className="mono">{canonical}</span>
              </p>
            ) : null}
          </div>
          <p className="ad-fieldhelp">
            Saved in small letters — letter case does not matter.
          </p>
        </div>
        {loginInvalid && attempted ? (
          <div className="err" role="alert">
            {loginInvalid}
          </div>
        ) : null}
        {roleFixed ? (
          <div className="ad-identity">
            <div className="idrow">
              <span className="k">Role</span>
              <span className="v">{savedRole?.name ?? saved?.roleName}</span>
            </div>
            <div className="idrow">
              <span className="k">Status</span>
              <span className="v">
                {saved?.isActive ? 'Active' : 'Inactive'}
              </span>
            </div>
          </div>
        ) : (
          <>
            <AdminField label="Role">
              <select
                className="field"
                value={roleId === null ? '' : String(roleId)}
                onChange={(event) => setRoleId(Number(event.target.value))}
              >
                {roleId === null ? (
                  <option value="" disabled>
                    Choose a role…
                  </option>
                ) : null}
                {choosableRoles.map((role) => (
                  <option key={role.id} value={String(role.id)}>
                    {role.name}
                  </option>
                ))}
              </select>
            </AdminField>
            {managesCorrections ? null : (
              <div className="ad-fieldnotes">
                <p className="ad-fieldhelp">{PROTECTED_ROLE_HINT}</p>
              </div>
            )}
          </>
        )}
        {roleInvalid && attempted ? (
          <div className="err" role="alert">
            Choose a role.
          </div>
        ) : null}
        <div className="ad-avatarblock">
          <span className="ad-avatarlabel">
            Avatar <span className="field-optional">(optional)</span>
          </span>
          <div className="ad-avatarrow">
            <Avatar name={trimmedName} size="md" src={avatarSrc} />
            <div className="ad-avataractions">
              <button
                type="button"
                className="btn ghost"
                disabled={controlsBlocked}
                onClick={() => fileInput.current?.click()}
              >
                Choose image…
              </button>
              <input
                ref={fileInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="Avatar image file"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  void chooseImage(file);
                }}
              />
              {showRemove ? (
                <button
                  type="button"
                  className="btn ghost"
                  disabled={controlsBlocked}
                  onClick={removeAvatar}
                >
                  Remove avatar
                </button>
              ) : null}
            </div>
          </div>
          <p className="ad-fieldhelp">
            PNG, JPEG or WebP. Large images are resized before upload.
          </p>
          {imageError ? (
            <div className="err" role="alert">
              {imageError}
            </div>
          ) : null}
        </div>
        {saved && !roleFixed ? (
          <ActiveField
            label="Active"
            checked={isActive}
            onChange={setIsActive}
          />
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={controlsBlocked || noRoleToGive}
          onClick={() => void submit()}
        >
          {saved ? 'Save changes' : 'Add user'}
        </button>
      </div>
    </ModalDialog>
  );
}
