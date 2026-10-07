import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api/client';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { setUserPassword } from '../../api/users';
import type { User } from '../../api/users';
import { newPasswordError } from '../../app/password-rules';
import { ModalDialog } from '../../components/ModalDialog';
import { AdminField, ServerErrorNote } from './section-widgets';

const UNKNOWN_OUTCOME =
  'The server did not answer — the password may or may not have been set. Set it again to be sure.';

/**
 * Administration → Users → `Set password…`: a user administrator gives
 * another user a new password (the server refuses it for the signed-in
 * user's own account). The new password replaces the old one, ends every
 * sign-in of that user and clears a lock; it is stored as temporary, so
 * the user chooses their own at the next sign-in when Settings → User
 * sign-in requires it. Setting the same password again is safe.
 */
export function SetPasswordDialog({
  user,
  writeBlocked,
  onCancel,
  onSet,
}: {
  user: User;
  writeBlocked: boolean;
  onCancel: () => void;
  onSet: (user: User) => void;
}) {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const passwordField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    passwordField.current?.focus();
  }, []);

  const passwordInvalid = newPasswordError(password, repeat);

  // Cancel, Escape and the backdrop are ignored while the write is in
  // flight.
  const requestClose = () => {
    if (!busy) onCancel();
  };

  const submit = async () => {
    if (busy || writeBlocked) return;
    if (passwordInvalid) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      onSet(await setUserPassword(user.id, password));
    } catch (error) {
      setServerError(
        signInWriteOutcomeUnknown(error) || !(error instanceof ApiError)
          ? UNKNOWN_OUTCOME
          : error.message,
      );
      setBusy(false);
    }
  };

  const title = `Set password for ${user.displayName}`;
  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      <p className="sub">
        This replaces the password and signs {user.displayName} out everywhere.
        Give the new password to them in person. If Settings → User sign-in
        requires it, they choose their own password at their next sign-in. A
        lock on the account is cleared.
      </p>
      <form
        className="ad-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <AdminField label="New password">
          <input
            ref={passwordField}
            className="field"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
          />
        </AdminField>
        <AdminField label="Repeat password">
          <input
            className="field"
            type="password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            autoComplete="new-password"
          />
        </AdminField>
        {attempted && passwordInvalid ? (
          <div className="err" role="alert">
            {passwordInvalid}
          </div>
        ) : null}
        <ServerErrorNote message={serverError} />
        <div className="row">
          <button
            type="button"
            className="bigbtn ghost"
            disabled={busy}
            onClick={requestClose}
          >
            Cancel (Esc)
          </button>
          <button
            type="submit"
            className="bigbtn primary"
            disabled={busy || writeBlocked}
          >
            Set password
          </button>
        </div>
      </form>
    </ModalDialog>
  );
}
