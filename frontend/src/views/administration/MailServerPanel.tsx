import { useState } from 'react';

import { ApiError } from '../../api/client';
import {
  getMailServerPolicy,
  MAIL_SECURITIES,
  sendTestMail,
  updateMailServerPolicy,
} from '../../api/policies';
import type { MailSecurity, MailServerPolicy } from '../../api/policies';
import { signInWriteOutcomeUnknown } from '../../api/session';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { useCloseGuard } from '../../components/close-guard';
import { UnsavedChangesDialog } from '../../components/close-guard-dialogs';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { canonicalEmail, EMAIL_ADDRESS_ERROR } from './email-address';
import { AdminField, ServerErrorNote } from './section-widgets';

// Administration → Settings → Outgoing mail server (Phase 17, owner
// decision of 2026-11-13): the mail server PartFlow sends its messages
// through — a one-time sign-in code to a user who has lost their other
// ways to sign in. Every signed-in user reads it; users whose role may
// configure system settings edit it, remove it and send a test message.
// The password is never shown: the server answers only whether one is
// stored, keeps it when the field is left empty, and drops it with the
// user name. Saving sends nothing. The server validates every value.

const SECURITY_LABELS: Record<MailSecurity, string> = {
  STARTTLS: 'STARTTLS',
  TLS: 'SSL/TLS',
  NONE: 'None',
};

const DEFAULT_PORTS: Record<MailSecurity, number> = {
  STARTTLS: 587,
  TLS: 465,
  NONE: 25,
};

const HOST_PATTERN = /^[a-z0-9.:-]+$/;
const HOST_ERROR =
  'Enter the mail server as a host name or IP address, such as smtp.example.com, without spaces or a port.';
const PORT_ERROR = 'The port must be a whole number from 1 to 65535.';
const SENDER_MISSING =
  'Enter the sender address PartFlow sends its messages from.';
const CLEAR_SIGN_IN_ERROR =
  'Signing in to the mail server needs STARTTLS or SSL/TLS, so the password never crosses the network unencrypted.';
const UNKNOWN_OUTCOME =
  'The server did not answer — the mail server may or may not have been saved. Close this window to reload it before trying again.';
const UNKNOWN_SEND =
  'The server did not answer — the test message may or may not have been sent. Check the inbox before trying again.';

function parsePort(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= 1 && value <= 65535 ? value : null;
}

export function MailServerPanel() {
  const session = useSession();
  return (
    <>
      <h2>Outgoing mail server</h2>
      <p className="admin-confighelp">
        The mail server PartFlow sends email through: a one-time sign-in code to
        a user who has lost their other ways to sign in. Without one, PartFlow
        sends no email.
      </p>
      <MailServerView canEdit={session.can('CONFIGURE_SYSTEM_SETTINGS')} />
    </>
  );
}

function MailServerView({ canEdit }: { canEdit: boolean }) {
  const { status } = useConnectivity();
  const serverData = useApiData(getMailServerPolicy);
  const [dialog, setDialog] = useState<'edit' | 'test' | null>(null);

  if (serverData.state.status === 'loading') {
    return <LoadingState label="Loading the outgoing mail server" />;
  }
  if (serverData.state.status === 'error') {
    return (
      <ErrorState
        message="The outgoing mail server could not be loaded."
        detail={serverData.state.message}
        onRetry={serverData.reload}
      />
    );
  }
  const server = serverData.state.data;
  const writeBlocked = status !== 'connected';
  return (
    <>
      <div className="admin-configpreview">
        <div className="prow">
          <span className="k">Mail server</span>
          <span className="v">
            {server.host === null
              ? 'Not set up'
              : `${server.host}, port ${server.port}`}
          </span>
        </div>
        {server.host === null ? null : (
          <>
            <div className="prow">
              <span className="k">Connection security</span>
              <span className="v">{SECURITY_LABELS[server.security]}</span>
            </div>
            <div className="prow">
              <span className="k">Sign-in</span>
              <span className="v">
                {server.username === null
                  ? 'None'
                  : `${server.username}${server.passwordSet ? ', password stored' : ', no password'}`}
              </span>
            </div>
            <div className="prow">
              <span className="k">Sender address</span>
              <span className="v">{server.sender}</span>
            </div>
          </>
        )}
      </div>
      {canEdit ? (
        <div className="row">
          <button
            className="bigbtn primary"
            disabled={writeBlocked}
            onClick={() => setDialog('edit')}
          >
            {server.host === null
              ? 'Set up the outgoing mail server…'
              : 'Edit outgoing mail server…'}
          </button>
          {server.host === null ? null : (
            <button
              className="bigbtn"
              disabled={writeBlocked}
              onClick={() => setDialog('test')}
            >
              Send test email…
            </button>
          )}
        </div>
      ) : null}
      {dialog === 'edit' ? (
        <MailServerDialog
          saved={server}
          writeBlocked={writeBlocked}
          onClose={(wrote) => {
            setDialog(null);
            if (wrote) serverData.reload();
          }}
        />
      ) : null}
      {dialog === 'test' ? (
        <TestMailDialog
          writeBlocked={writeBlocked}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  );
}

function MailServerDialog({
  saved,
  writeBlocked,
  onClose,
}: {
  saved: MailServerPolicy;
  writeBlocked: boolean;
  /** Close request; `wrote` = a save was sent (re-read the server). */
  onClose: (wrote: boolean) => void;
}) {
  const [hostText, setHostText] = useState(saved.host ?? '');
  const [portText, setPortText] = useState(String(saved.port));
  const [security, setSecurity] = useState<MailSecurity>(saved.security);
  const [username, setUsername] = useState(saved.username ?? '');
  const [password, setPassword] = useState('');
  const [removePassword, setRemovePassword] = useState(false);
  const [senderText, setSenderText] = useState(saved.sender ?? '');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [wrote, setWrote] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const host = hostText.trim().toLowerCase();
  const port = parsePort(portText);
  const user = username.trim();
  const sender = canonicalEmail(senderText);
  const hostInvalid =
    host !== '' && (host.length > 253 || !HOST_PATTERN.test(host));
  const senderMissing = host !== '' && sender === '';
  const clearSignIn = user !== '' && security === 'NONE';
  const invalid =
    hostInvalid ||
    port === null ||
    sender === null ||
    senderMissing ||
    clearSignIn;
  // A password is sent only when typed (replace) or removed (clear); left
  // empty, the stored one is kept.
  const passwordChange =
    password !== '' ? password : removePassword ? null : undefined;
  const changed =
    host !== (saved.host ?? '') ||
    port !== saved.port ||
    security !== saved.security ||
    user !== (saved.username ?? '') ||
    sender !== (saved.sender ?? '') ||
    passwordChange !== undefined;

  const guard = useCloseGuard({
    dirty: changed,
    busy,
    onClose: () => onClose(wrote),
  });

  const chooseSecurity = (next: MailSecurity) => {
    // A port still at the old security's usual value follows the new one.
    if (port === DEFAULT_PORTS[security]) {
      setPortText(String(DEFAULT_PORTS[next]));
    }
    setSecurity(next);
  };

  const save = async (server: Parameters<typeof updateMailServerPolicy>[0]) => {
    setBusy(true);
    setServerError(null);
    setWrote(true);
    try {
      await updateMailServerPolicy(server);
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

  const submit = async () => {
    if (busy || writeBlocked) return;
    if (invalid || port === null || sender === null) {
      setAttempted(true);
      return;
    }
    if (!changed) {
      onClose(wrote);
      return;
    }
    await save({
      host: host === '' ? null : host,
      port,
      security,
      username: host === '' || user === '' ? null : user,
      sender: host === '' || sender === '' ? null : sender,
      ...(passwordChange !== undefined ? { password: passwordChange } : {}),
    });
  };

  const remove = async () => {
    if (busy || writeBlocked) return;
    await save({
      host: null,
      port: saved.port,
      security: saved.security,
      username: null,
      sender: null,
      password: null,
    });
  };

  const title = 'Outgoing mail server';
  return (
    <ModalDialog label={title} onClose={guard.requestClose}>
      <h3>{title}</h3>
      <div className="admin-form">
        <AdminField label="Mail server">
          <input
            className="field mono"
            value={hostText}
            disabled={busy}
            onChange={(event) => setHostText(event.target.value)}
            placeholder="e.g. smtp.example.com"
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        {hostInvalid && attempted ? (
          <div className="err" role="alert">
            {HOST_ERROR}
          </div>
        ) : null}
        <AdminField label="Connection security">
          <select
            className="field"
            value={security}
            disabled={busy}
            onChange={(event) =>
              chooseSecurity(event.target.value as MailSecurity)
            }
          >
            {MAIL_SECURITIES.map((value) => (
              <option key={value} value={value}>
                {`${SECURITY_LABELS[value]} (usually port ${DEFAULT_PORTS[value]})`}
              </option>
            ))}
          </select>
        </AdminField>
        <AdminField label="Port">
          <input
            className="field mono"
            type="number"
            min={1}
            max={65535}
            step={1}
            value={portText}
            disabled={busy}
            onChange={(event) => setPortText(event.target.value)}
          />
        </AdminField>
        {port === null && attempted ? (
          <div className="err" role="alert">
            {PORT_ERROR}
          </div>
        ) : null}
        <AdminField
          label={
            <>
              User name <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={username}
            disabled={busy}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        <AdminField
          label={
            <>
              Password <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            type="password"
            value={password}
            disabled={busy || removePassword}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={
              saved.passwordSet ? 'Stored — type a new one to replace it' : ''
            }
            autoComplete="new-password"
          />
        </AdminField>
        {saved.passwordSet ? (
          <label className="admin-check">
            <input
              type="checkbox"
              checked={removePassword}
              disabled={busy || password !== ''}
              onChange={(event) => setRemovePassword(event.target.checked)}
            />
            <span>Remove the stored password</span>
          </label>
        ) : null}
        {clearSignIn && attempted ? (
          <div className="err" role="alert">
            {CLEAR_SIGN_IN_ERROR}
          </div>
        ) : null}
        <AdminField label="Sender address">
          <input
            className="field"
            type="email"
            value={senderText}
            disabled={busy}
            onChange={(event) => setSenderText(event.target.value)}
            placeholder="e.g. partflow@example.com"
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        {sender === null && attempted ? (
          <div className="err" role="alert">
            {EMAIL_ADDRESS_ERROR}
          </div>
        ) : null}
        {senderMissing && attempted ? (
          <div className="err" role="alert">
            {SENDER_MISSING}
          </div>
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
        {saved.host === null ? null : (
          <button
            className="bigbtn danger"
            disabled={busy || writeBlocked}
            onClick={() => void remove()}
          >
            Remove mail server
          </button>
        )}
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked}
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
          You have unsaved changes to the outgoing mail server.
        </UnsavedChangesDialog>
      ) : null}
    </ModalDialog>
  );
}

function TestMailDialog({
  writeBlocked,
  onClose,
}: {
  writeBlocked: boolean;
  onClose: () => void;
}) {
  const [toText, setToText] = useState('');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const to = canonicalEmail(toText);
  const invalid = to === null || to === '';

  const send = async () => {
    if (busy || writeBlocked) return;
    if (invalid) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    setSentTo(null);
    try {
      setSentTo(await sendTestMail(to));
    } catch (error) {
      setServerError(
        signInWriteOutcomeUnknown(error) || !(error instanceof ApiError)
          ? UNKNOWN_SEND
          : error.message,
      );
    } finally {
      setBusy(false);
    }
  };

  const title = 'Send a test email';
  return (
    <ModalDialog label={title} onClose={busy ? () => undefined : onClose}>
      <h3>{title}</h3>
      <div className="admin-form">
        <p className="admin-confighelp">
          PartFlow sends one short message through the saved mail server.
        </p>
        <AdminField label="Send to">
          <input
            className="field"
            type="email"
            value={toText}
            disabled={busy}
            onChange={(event) => {
              setToText(event.target.value);
              setSentTo(null);
            }}
            placeholder="e.g. you@example.com"
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        {invalid && attempted ? (
          <div className="err" role="alert">
            {EMAIL_ADDRESS_ERROR}
          </div>
        ) : null}
        {sentTo !== null ? (
          <p className="admin-fieldnote" role="status">
            {`Sent to ${sentTo}. If it does not arrive within a few minutes, check the spam folder.`}
          </p>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={onClose}>
          Close (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={busy || writeBlocked}
          onClick={() => void send()}
        >
          Send test email
        </button>
      </div>
    </ModalDialog>
  );
}
