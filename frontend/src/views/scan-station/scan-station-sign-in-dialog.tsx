import { Suspense, lazy, useEffect, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { scanBadge } from '../../api/scan-station';
import type { BadgeScanResult } from '../../api/scan-station';
import { ModalDialog } from '../../components/ModalDialog';
import { normalizeScanInput } from './barcode';
import { Guidance } from './scan-station-presentation';

// Development-only demo badges: the lazy import sits behind
// `import.meta.env.DEV`, so production builds drop the module from the
// graph (verified by src/production-boundary.test.ts).
const DevBadges = import.meta.env.DEV
  ? lazy(() =>
      import('./scan-station-dev-badges').then((module) => ({
        default: module.DevBadges,
      })),
    )
  : null;

const BADGE_NOT_RECOGNIZED =
  'Badge not recognized. Check the badge and scan again — nothing was recorded.';

/**
 * Inline copy of a failed badge check. The reason keeps its own
 * "nothing was recorded/changed" sentence when it already has one (the
 * server's sign-in conflict, the unreachable-server fallback); the
 * suffix is added only when it is missing, never twice.
 */
function badgeCheckFailure(error: unknown): string {
  const reason = errorMessage(error);
  const stated = /nothing was (recorded|changed)\.\s*$/i.test(reason);
  return `Badge could not be checked — ${reason}${stated ? '' : ' Nothing was recorded.'}`;
}

/**
 * Blocking badge-scan modal of a Scanned-session Area (GUI_DESIGN
 * §4.12; PROJECT_PROFILE §19): shown while the station has no valid
 * Worker Session — on open and after the session expired or the server
 * refused a command for want of one. Only the Scan Station is blocked,
 * and an open production dialog keeps its draft underneath. Escape and
 * backdrop clicks never dismiss it: a badge the SERVER accepts is the
 * only way through. A badge scan here is a write (sign-in), so it is
 * disabled while disconnected; nothing is queued.
 */
export function WorkerSignInDialog({
  stationId,
  expired,
  writeBlocked,
  ticket,
  onSignedIn,
  onModeChanged,
}: {
  stationId: string;
  /** true after a session existed in this page; false before any. */
  expired: boolean;
  writeBlocked: boolean;
  /** Send-order ticket of the session answers (taken before sending). */
  ticket: () => number;
  /** The server signed in, switched or refreshed the session. */
  onSignedIn: (result: BadgeScanResult, ticket: number) => void;
  /** The Area no longer takes badge scans: re-read the station. */
  onModeChanged: () => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [checking, setChecking] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  // The badge field owns focus whenever it can take a scan: on open,
  // after a refused or failed check, and on reconnection.
  useEffect(() => {
    if (!checking && !writeBlocked) fieldRef.current?.focus();
  }, [checking, writeBlocked]);

  async function submit() {
    const field = fieldRef.current;
    if (!field || checking || writeBlocked) return;
    const value = normalizeScanInput(field.value);
    field.value = '';
    if (!value) return;
    if (value.toUpperCase().startsWith('PF:')) {
      // A PartFlow barcode is never a Worker badge.
      setScanError(BADGE_NOT_RECOGNIZED);
      return;
    }
    setChecking(true);
    setScanError(null);
    const sent = ticket();
    try {
      const result = await scanBadge(stationId, value);
      if (
        result.outcome === 'SIGNED_IN' ||
        result.outcome === 'SWITCHED' ||
        result.outcome === 'REFRESHED'
      ) {
        onSignedIn(result, sent);
        return;
      }
      // Any answer under a mode other than Scanned session (whatever its
      // outcome) means the Area changed mode: re-read the station, whose
      // new mode lifts this modal.
      if (result.outcome === 'NOT_USED_IN_AREA' || result.mode !== 'SCANNED') {
        onModeChanged();
      } else {
        setScanError(BADGE_NOT_RECOGNIZED);
      }
    } catch (error) {
      setScanError(badgeCheckFailure(error));
    } finally {
      setChecking(false);
    }
  }

  // Development-only: a click on a demo badge is the exact equivalent
  // of a wedge badge scan ending in Enter — the value lands in this
  // field and goes through the SAME submit path.
  function simulate(value: string) {
    if (writeBlocked || checking || !fieldRef.current) return;
    fieldRef.current.value = value;
    void submit();
  }

  const title = expired ? 'Worker session expired' : 'Worker sign-in required';
  return (
    <ModalDialog
      label={title}
      // Deliberately not dismissable: the close request is ignored.
      onClose={() => undefined}
    >
      <h3>{title}</h3>
      <div className="sub">Scan your badge to continue.</div>
      <input
        aria-label="Scan Worker badge"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        disabled={writeBlocked || checking}
        placeholder={
          writeBlocked
            ? 'Disconnected — scanning disabled'
            : checking
              ? 'Checking badge…'
              : 'Scan Worker badge · Press Enter'
        }
        onChange={() => setScanError(null)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') void submit();
        }}
      />
      {scanError ? <Guidance tone="error">{scanError}</Guidance> : null}
      {DevBadges ? (
        <Suspense fallback={null}>
          <DevBadges onScan={simulate} />
        </Suspense>
      ) : null}
    </ModalDialog>
  );
}
