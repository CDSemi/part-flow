// The one-shot production-write protocol shared by the Scan Station
// dialogs (GUI_DESIGN §4.6; PROJECT_PROFILE §15): one idempotency key
// per confirmed intent, success ONLY after the server confirmed the
// write, an explicit application rejection (4xx) reported in place
// with nothing recorded, and a transport failure / timeout / 5xx
// treated as an UNKNOWN outcome — the intent is frozen and the only
// way forward is the exact same request under the same
// `device_event_id`, which replays the committed write or records it
// once. Production-safe: no mock data, no JSX.

import { useCallback, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { newDeviceEventId } from '../../api/production-release';
import { writeOutcomeUnknown } from '../../api/scan-station';

export interface OneShotWrite<T> {
  /** A request is in flight. */
  busy: boolean;
  /** The server rejected the request before writing — nothing recorded. */
  serverError: string | null;
  /** The server never answered: the write may or may not be recorded. */
  outcomeUnknown: boolean;
  /** The idempotency key of this intent, reused verbatim on retries. */
  deviceEventId: string;
  /** Send (or resend) the frozen intent. */
  submit: () => Promise<void>;
  /** Forget a previous rejection (before changing the intent). */
  clearError: () => void;
  /** The confirmed result, once the server answered. */
  result: T | null;
}

export function useOneShotWrite<T>({
  send,
  writeBlocked,
  onDone,
}: {
  /** The request for THIS intent; called with the frozen key. */
  send: (deviceEventId: string) => Promise<T>;
  writeBlocked: boolean;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: T) => void;
}): OneShotWrite<T> {
  const deviceEventId = useRef(newDeviceEventId());
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [result, setResult] = useState<T | null>(null);

  const submit = useCallback(async () => {
    if (busy) return;
    if (writeBlocked) {
      setServerError(
        'Connection lost — the action was not sent. Reconnect and confirm again; nothing was recorded.',
      );
      return;
    }
    setBusy(true);
    setServerError(null);
    // Only the request itself is guarded: a server-confirmed result
    // must never be re-classified as an unknown outcome because the
    // completion handler failed afterwards.
    let confirmed: T;
    try {
      confirmed = await send(deviceEventId.current);
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        setOutcomeUnknown(true);
        setServerError(null);
      } else {
        setServerError(errorMessage(error));
      }
      setBusy(false);
      return;
    }
    setBusy(false);
    setResult(confirmed);
    onDone(confirmed);
  }, [busy, writeBlocked, send, onDone]);

  const clearError = useCallback(() => setServerError(null), []);

  return {
    busy,
    serverError,
    outcomeUnknown,
    deviceEventId: deviceEventId.current,
    submit,
    clearError,
    result,
  };
}
