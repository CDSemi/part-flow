import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { ApiError } from '../../api/client';
import {
  activateStationDevice,
  storeStationDeviceToken,
} from '../../api/station-devices';
import { useRouter } from '../../app/router-context';
import { ModalDialog } from '../../components/ModalDialog';
import { Guidance } from './scan-station-presentation';

/** Why this browser cannot use the station right now. */
export type EnrollmentReason = 'not-enrolled' | 'revoked' | 'mismatch';

const ENROLLMENT_CODE_LENGTH = 10;

function leadOf(reason: EnrollmentReason, stationId: string): string {
  switch (reason) {
    case 'not-enrolled':
      return `This device is not enrolled for Scan Station ${stationId}. Production at this station needs an enrolled device. Ask an administrator for an enrollment code (Administration → Scan Stations → Devices).`;
    case 'revoked':
      return `This device's enrollment for Scan Station ${stationId} was revoked or replaced. Ask an administrator for a new enrollment code.`;
    case 'mismatch':
      return `This device is enrolled for a different Scan Station, not ${stationId}. Enroll it for this station to continue.`;
  }
}

/**
 * Device enrollment at a Scan Station (Phase 14 slice 4 — owner
 * decision OD-P6; GUI_DESIGN §4.13): the browser exchanges the one-time
 * enrollment code an administrator issued for THIS station for its
 * device token, which it keeps in its own storage and sends with every
 * station request — the token is never shown. Two forms:
 * - `panel`: the station never loaded for want of an enrolled device;
 *   the panel replaces the station content (the owner renders the
 *   header) and keeps a `Station Selector` way out.
 * - `dialog`: a later station call was refused; the blocking modal
 *   renders above the station and its open dialogs (drafts kept
 *   underneath) — Escape and backdrop clicks never dismiss it, a code
 *   the server accepts is the only way through. After enrolling, the
 *   operator confirms the same action again: it is recorded once.
 * Enrolling is a write: disabled while disconnected, nothing queued.
 */
export function StationEnrollment({
  stationId,
  variant,
  reason,
  pendingUnknownOutcome,
  writeNotSent = false,
  writeBlocked,
  onEnrolled,
}: {
  stationId: string;
  variant: 'panel' | 'dialog';
  reason: EnrollmentReason;
  /** An open action's outcome is unknown: it must be confirmed again
   * after enrolling (never "nothing was recorded"). */
  pendingUnknownOutcome: boolean;
  /** A first attempt of an action was refused for want of an enrolled
   * device: it was not sent on, nothing was recorded. */
  writeNotSent?: boolean;
  writeBlocked: boolean;
  /** The device is enrolled and its token stored; `persisted: false` =
   * the browser could not keep it beyond this page. */
  onEnrolled: (result: { persisted: boolean }) => void;
}) {
  const { navigate } = useRouter();
  const fieldRef = useRef<HTMLInputElement>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The code field owns focus on open (after ModalDialog's own focus).
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);

  async function submit() {
    if (busy || writeBlocked) return;
    const normalized = code.replace(/[\s-]/g, '');
    if (normalized.length !== ENROLLMENT_CODE_LENGTH) {
      setError('Enter the 10-character enrollment code.');
      fieldRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    let token: string;
    try {
      token = (await activateStationDevice(stationId, code.trim())).deviceToken;
    } catch (failure) {
      setBusy(false);
      if (failure instanceof ApiError && failure.status < 500) {
        setError(failure.message);
      } else if (failure instanceof ApiError) {
        setError(
          'The server did not answer — this device may or may not be enrolled. If the next attempt says the code is not valid, ask an administrator for a new code.',
        );
      } else {
        setError('The PartFlow server could not be reached. Try again.');
      }
      fieldRef.current?.focus();
      return;
    }
    const { persisted } = storeStationDeviceToken(stationId, token);
    setBusy(false);
    onEnrolled({ persisted });
  }

  const body: ReactNode = (
    <>
      <div className="sub">{leadOf(reason, stationId)}</div>
      {pendingUnknownOutcome ? (
        <Guidance tone="warn">
          The outcome of the last action is unknown. After enrolling, confirm it
          again — it is recorded only once.
        </Guidance>
      ) : writeNotSent ? (
        <Guidance tone="info">
          Not sent — this device must be enrolled first. Nothing was recorded.
        </Guidance>
      ) : null}
      <label className="ss-enroll-field">
        <span className="ss-enroll-label">Enrollment code</span>
        <input
          ref={fieldRef}
          className="field mono"
          value={code}
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="characters"
          placeholder="XXXXX-XXXXX"
          disabled={busy}
          onChange={(event) => {
            setCode(event.target.value);
            setError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void submit();
            }
          }}
        />
      </label>
      {error ? (
        <div role="alert">
          <Guidance tone="error">{error}</Guidance>
        </div>
      ) : null}
      {writeBlocked ? (
        <Guidance tone="warn">
          Enrolling needs the connection to the PartFlow server.
        </Guidance>
      ) : null}
      <div className="row">
        {variant === 'panel' ? (
          // The dialog has no way out: its drafts and an unknown outcome
          // stay until the same intent is confirmed again after enrolling.
          <button
            className="bigbtn ghost"
            onClick={() => navigate('/scan-station')}
          >
            Station Selector
          </button>
        ) : null}
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={() => void submit()}
        >
          {busy ? 'Enrolling…' : 'Enroll device'}
        </button>
      </div>
    </>
  );

  if (variant === 'dialog') {
    return (
      <ModalDialog
        label="Enroll this device"
        // Deliberately not dismissable: the close request is ignored.
        onClose={() => undefined}
      >
        <h3>Enroll this device</h3>
        {body}
      </ModalDialog>
    );
  }
  return (
    <div className="ss-enroll" role="region" aria-label="Enroll this device">
      <h2>Enroll this device</h2>
      {body}
    </div>
  );
}
