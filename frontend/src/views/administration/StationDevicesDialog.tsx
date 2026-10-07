import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api/client';
import type { ScanStation } from '../../api/environment';
import type { Permission } from '../../api/roles';
import {
  issueStationDeviceEnrollment,
  revokeStationDevice,
} from '../../api/station-devices';
import type { StationDevice } from '../../api/station-devices';
import { useSession } from '../../app/session-context';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ModalDialog } from '../../components/ModalDialog';
import { useUiClock } from '../../components/ui-clock';
import { formatElapsedSince, formatTimeOfDay } from '../dates';
import { AdminField, ServerErrorNote, ViewOnlyNote } from './section-widgets';

// Administration → Scan Stations → Devices (Phase 14 slice 4 — owner
// decisions OD-P6, OD-S4-9): the devices enrolled for one Scan Station
// and its pending enrollment codes, with their last contact. An
// enrollment code is shown ONCE, right after it was created; it works
// once, for this station, within 15 minutes, and the station device
// exchanges it for its own token (never shown anywhere). Re-enrolling
// replaces a device when the new code is used; revoking stops a device
// at once. Enrolling needs Manage Scan Stations and — while the role
// applied at Scan Stations holds a correction permission — Manage
// correction permissions (`enrollmentPermissions`, the server's answer);
// revoking needs Manage Scan Stations only. Controls the signed-in user
// may not use are hidden; offline they are shown disabled. The server
// decides every write.

const DEVICE_LABEL_MAX = 80;

const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — a code may or may not have been created. Create a new one; an unused code expires by itself.';

type Pending =
  | { kind: 'enroll' }
  | { kind: 'reenroll'; device: StationDevice }
  | { kind: 'revoke'; device: StationDevice };

/** The devices dialog of one Scan Station. */
export function StationDevicesDialog({
  station,
  devices,
  enrollmentPermissions,
  writeBlocked,
  onChanged,
  onNotice,
  onClose,
}: {
  station: ScanStation;
  /** The station's open devices: enrolled ones and unexpired codes. */
  devices: StationDevice[];
  /** What issuing an enrollment code needs now (server's answer). */
  enrollmentPermissions: Permission[];
  writeBlocked: boolean;
  /** A write was answered: read the device list again. */
  onChanged: () => void;
  /** A confirmed write's toast. */
  onNotice: (message: string) => void;
  onClose: () => void;
}) {
  const session = useSession();
  const manages = session.can('MANAGE_SCAN_STATIONS');
  const canEnroll =
    manages && enrollmentPermissions.every((key) => session.can(key));
  const now = useUiClock('minute');
  const [pending, setPending] = useState<Pending | null>(null);

  if (pending?.kind === 'enroll' || pending?.kind === 'reenroll') {
    return (
      <EnrollDeviceDialog
        station={station}
        replaces={pending.kind === 'reenroll' ? pending.device : null}
        writeBlocked={writeBlocked}
        onCreated={onChanged}
        onClose={() => setPending(null)}
      />
    );
  }
  if (pending?.kind === 'revoke') {
    return (
      <RevokeDeviceDialog
        device={pending.device}
        stationId={station.stationId}
        writeBlocked={writeBlocked}
        onRevoked={(message) => {
          setPending(null);
          onNotice(message);
          onChanged();
        }}
        onCancel={() => setPending(null)}
      />
    );
  }

  const title = `Devices of Scan Station ${station.stationId}`;
  return (
    <ModalDialog label={title} onClose={onClose} size="wide">
      <h3>{title}</h3>
      {!manages ? (
        <ViewOnlyNote permission="MANAGE_SCAN_STATIONS" />
      ) : !canEnroll ? (
        <p className="ad-confighelp">
          Enrolling a device needs the Manage correction permissions permission
          while the role applied at Scan Stations holds correction permissions.
        </p>
      ) : null}
      {devices.length === 0 ? (
        <p className="ad-fieldhelp">No device is enrolled for this station.</p>
      ) : (
        <table className="ad-table" aria-label="Devices">
          <thead>
            <tr>
              <th>Device</th>
              <th>State</th>
              <th>Last seen</th>
              {manages ? <th aria-label="Actions" /> : null}
            </tr>
          </thead>
          <tbody>
            {devices.map((device) => (
              <tr key={device.id}>
                <td>
                  <b>{device.label}</b>
                </td>
                <td data-label="State">{deviceStateLabel(device)}</td>
                <td data-label="Last seen">
                  {device.lastSeenAt
                    ? `${formatElapsedSince(device.lastSeenAt, now)} ago`
                    : 'Never'}
                </td>
                {manages ? (
                  <td>
                    {device.state === 'ACTIVE' && canEnroll ? (
                      <button
                        className="btn"
                        disabled={writeBlocked || !station.isActive}
                        title={
                          station.isActive
                            ? undefined
                            : 'Reactivate the Scan Station first'
                        }
                        onClick={() => setPending({ kind: 'reenroll', device })}
                      >
                        Re-enroll…
                      </button>
                    ) : null}{' '}
                    <button
                      className="btn"
                      disabled={writeBlocked}
                      onClick={() => setPending({ kind: 'revoke', device })}
                    >
                      {device.state === 'PENDING' ? 'Cancel code' : 'Revoke…'}
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="ad-fieldhelp">
        An enrolled device can use this Scan Station until it is revoked. Each
        device has its own enrollment; a device enrolled for another station
        cannot act here. Last seen is the device's most recent request to
        PartFlow, including refused ones.
      </p>
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Close
        </button>
        {canEnroll ? (
          <button
            className="bigbtn primary"
            disabled={writeBlocked || !station.isActive}
            title={
              station.isActive ? undefined : 'Reactivate the Scan Station first'
            }
            onClick={() => setPending({ kind: 'enroll' })}
          >
            Enroll device…
          </button>
        ) : null}
      </div>
    </ModalDialog>
  );
}

function deviceStateLabel(device: StationDevice): string {
  switch (device.state) {
    case 'ACTIVE':
      return 'Enrolled';
    case 'PENDING':
      return `Code issued — expires ${formatTimeOfDay(device.enrollmentExpiresAt)}`;
    case 'EXPIRED':
      return 'Code expired';
    case 'REVOKED':
      return 'Revoked';
  }
}

/** The code read out character by character (screen readers). */
function spokenCode(code: string): string {
  return code.split('').join(' ');
}

/**
 * Create an enrollment code for a new device, or for the device that
 * replaces `replaces` once the code is used. The code is shown once in
 * this same dialog; closing it requests nothing.
 */
function EnrollDeviceDialog({
  station,
  replaces,
  writeBlocked,
  onCreated,
  onClose,
}: {
  station: ScanStation;
  replaces: StationDevice | null;
  writeBlocked: boolean;
  /** A code was created (the device list shows it as issued). */
  onCreated: () => void;
  onClose: () => void;
}) {
  const [label, setLabel] = useState(replaces?.label ?? '');
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [issued, setIssued] = useState<{
    code: string;
    expiresAt: string;
  } | null>(null);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);

  const trimmed = label.trim();
  const labelInvalid = trimmed.length < 1 || trimmed.length > DEVICE_LABEL_MAX;
  const title = replaces ? `Re-enroll ${replaces.label}` : 'Enroll a device';

  const submit = async () => {
    if (busy || writeBlocked) return;
    if (labelInvalid) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const result = await issueStationDeviceEnrollment(station.stationId, {
        label: trimmed,
        replacesDeviceId: replaces?.id ?? null,
      });
      setIssued({
        code: result.enrollmentCode,
        expiresAt: result.device.enrollmentExpiresAt,
      });
      onCreated();
    } catch (error) {
      setServerError(
        error instanceof ApiError && error.status < 500
          ? error.message
          : UNKNOWN_OUTCOME_MESSAGE,
      );
    } finally {
      setBusy(false);
    }
  };

  if (issued) {
    return (
      <ModalDialog label={title} onClose={onClose}>
        <h3>{title}</h3>
        <div
          className="big mono ad-enrollcode"
          aria-label={`Enrollment code ${spokenCode(issued.code)}`}
        >
          {issued.code}
        </div>
        <p className="sub">
          Enter this code on the Scan Station {station.stationId} device before{' '}
          {formatTimeOfDay(issued.expiresAt)}. It is shown only once and works
          only once.
        </p>
        <div className="row">
          <button className="bigbtn primary" onClick={onClose}>
            Done
          </button>
        </div>
      </ModalDialog>
    );
  }

  return (
    <ModalDialog label={title} onClose={busy ? () => undefined : onClose}>
      <h3>{title}</h3>
      <div className="ad-form">
        <AdminField label="Device name">
          <input
            ref={field}
            className="field"
            value={label}
            maxLength={DEVICE_LABEL_MAX * 2}
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit();
            }}
          />
        </AdminField>
        <p className="ad-fieldhelp">
          For example "Lathe cell PC" — shown in this list only.
        </p>
        {labelInvalid && attempted ? (
          <div className="err" role="alert">
            Enter a device name of 1 to 80 characters.
          </div>
        ) : null}
        {replaces ? (
          <p className="ad-fieldhelp">
            When the new code is used, {replaces.label} stops working at once.
          </p>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={onClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={() => void submit()}
        >
          Create enrollment code
        </button>
      </div>
    </ModalDialog>
  );
}

/** Revoke an enrolled device, or cancel a pending code (danger). */
function RevokeDeviceDialog({
  device,
  stationId,
  writeBlocked,
  onRevoked,
  onCancel,
}: {
  device: StationDevice;
  stationId: string;
  writeBlocked: boolean;
  /** The server revoked it; the toast to show. */
  onRevoked: (message: string) => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const isCode = device.state === 'PENDING';

  const confirm = async () => {
    if (busy || writeBlocked) return;
    setBusy(true);
    setServerError(null);
    try {
      await revokeStationDevice(device.id);
      onRevoked(
        isCode
          ? 'The enrollment code was cancelled.'
          : `${device.label} was revoked.`,
      );
    } catch (error) {
      setServerError(
        error instanceof ApiError
          ? error.message
          : 'The PartFlow server could not be reached. Nothing was changed.',
      );
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={
        isCode ? 'Cancel this enrollment code?' : `Revoke ${device.label}?`
      }
      tone="danger"
      cancelLabel="Cancel (Esc)"
      confirmLabel={isCode ? 'Cancel code' : 'Revoke device'}
      confirmDisabled={writeBlocked || busy}
      onConfirm={() => void confirm()}
      onCancel={busy ? () => undefined : onCancel}
    >
      {isCode
        ? 'It can no longer be used.'
        : `This device can no longer use Scan Station ${stationId} until it is enrolled again. A scan in progress there fails and must be confirmed again after re-enrollment.`}
      <ServerErrorNote message={serverError} />
    </ConfirmDialog>
  );
}
