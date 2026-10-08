// The one-shot production-write protocol shared by the Scan Station
// dialogs (GUI_DESIGN §4.6; PROJECT_PROFILE §15): one idempotency key
// per confirmed intent, success ONLY after the server confirmed the
// write, an explicit application rejection (4xx) reported in place
// with nothing recorded, and a transport failure / timeout / 5xx
// treated as an UNKNOWN outcome — the intent is frozen and the only
// way forward is the exact same request under the same
// `device_event_id`, which replays the committed write or records it
// once. A `worker_session_required` refusal (Phase 13) is neither: the
// server recorded nothing because the station has no valid Worker
// Session, so the sign-in modal is raised and, after the badge, the
// operator confirms the IDENTICAL request again. A typed final-gate
// refusal of DONE / QUEUE / Undo (Phase 13 badge confirmation) is not a
// rejection either: it is judged after the idempotency fast path, so
// nothing was recorded under this `device_event_id` — it also ends an
// unknown outcome — and the owner switches or re-opens its final gate
// (`useFinalGate`) and the same intent is confirmed again. An `undo_reason_required` refusal
// (Phase 13 Undo reason policy) is not a rejection either: it is judged
// after the idempotency fast path, so nothing is recorded under this
// `device_event_id` — it also ends an unknown outcome — and the owner
// asks for the reason before the same key is confirmed again.
// An enrolled-device refusal (Phase 14 slice 4: 401
// `station_device_required`, 403 `station_device_mismatch`) is not a
// rejection either, but unlike the refusals above it is judged BEFORE
// the idempotency fast path: it proves nothing about an earlier
// attempt, so the unknown-outcome state is KEPT as it was, the same
// `device_event_id` stays, and the station raises its enrollment dialog;
// after enrolling, the operator confirms the identical request again
// (it replays a committed original or records it once). A refusal by
// the role applied at Scan Stations (403 `station_permission_denied`)
// is an ordinary rejection — and since it is judged after the fast path
// and the post-lock re-check, it also ends an unknown outcome.
// Production-safe: no mock data, no JSX.

import { useCallback, useRef, useState } from 'react';

import { ApiError, errorMessage } from '../../api/client';
import { newDeviceEventId } from '../../api/production-release';
import {
  badgeGateRefusal,
  finalGateFor,
  undoReasonRequired,
  workerSessionRequired,
  writeOutcomeUnknown,
} from '../../api/scan-station';
import {
  stationDeviceRefusal,
  stationPermissionDenied,
} from '../../api/station-devices';
import type {
  BadgeGateRefusal,
  FinalGate,
  SensitiveAction,
  StationContext,
} from '../../api/scan-station';
import {
  useRequireWorkerSession,
  useStationDeviceRefused,
  useTrackOutcomeUnknown,
} from './scan-station-session';

/**
 * Whether a failed station request is an answer whose outcome is
 * unknown: a 408 or 5xx — including the `web` tier's own 502/504,
 * whose detail asks the operator to check whether the change was
 * saved. A transport failure keeps the client's own "could not be
 * reached" sentence and is not covered here.
 */
export function answeredOutcomeUnknown(error: unknown): boolean {
  return error instanceof ApiError && writeOutcomeUnknown(error);
}

/**
 * The message of a failed station request followed by its "nothing
 * was recorded" sentence — except for an unknown outcome
 * (`answeredOutcomeUnknown`), which never claims that nothing was
 * recorded: the message then stands alone.
 */
export function failureDetail(error: unknown, nothingRecorded: string): string {
  const message = errorMessage(error);
  return answeredOutcomeUnknown(error)
    ? message
    : `${message} ${nothingRecorded}`;
}

export interface OneShotWrite<T> {
  /** A request is in flight. */
  busy: boolean;
  /** The server rejected the request before writing — nothing recorded. */
  serverError: string | null;
  /** The server never answered: the write may or may not be recorded. */
  outcomeUnknown: boolean;
  /**
   * The server refused this intent at least once (nothing recorded).
   * From then on the wizard offers only Retry or Cancel: Back would
   * return to a selection snapshot the server has already refused.
   */
  rejected: boolean;
  /** The idempotency key of this intent, reused verbatim on retries. */
  deviceEventId: string;
  /** Send (or resend) the frozen intent. */
  submit: () => Promise<void>;
  /** Forget a previous rejection (before changing the intent). */
  clearError: () => void;
  /**
   * Start a NEW intent after an explicit refusal: a fresh
   * `device_event_id`, no standing rejection. A refused request wrote
   * nothing, so what follows is a different intent and must never
   * replay the refused one. Never allowed while the outcome is
   * unknown — that intent stays frozen behind its own id.
   */
  resetIntent: () => void;
  /** The confirmed result, once the server answered. */
  result: T | null;
}

export function useOneShotWrite<T>({
  send,
  writeBlocked,
  onDone,
  onRejected,
  onGateRefusal,
  onReasonRequired,
}: {
  /** The request for THIS intent; called with the frozen key. */
  send: (deviceEventId: string) => Promise<T>;
  writeBlocked: boolean;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: T) => void;
  /**
   * Called after an explicit application rejection (4xx — nothing
   * recorded). The owner uses it to re-read the Area from the server so
   * that Back/Cancel never leave the operator with the stale state the
   * server just refused (a flow moved meanwhile, a Machine retired…).
   */
  onRejected?: () => void;
  /**
   * A typed refusal of the final gate (409 `badge_confirmation_required`
   * / `badge_confirmation_not_expected`, 422 `badge_not_recognized`):
   * nothing recorded and the intent is still valid — no error, no
   * rejection, `onRejected` not called and the same `device_event_id`
   * kept. The unknown-outcome state is CLEARED: the gate is judged
   * after the idempotency fast path and the post-lock re-check, so the
   * server proved no commit exists for this key (else it would have
   * replayed) — an earlier unknown outcome is now known (not recorded).
   * Without this handler such a refusal is an ordinary rejection.
   */
  onGateRefusal?: (refusal: BadgeGateRefusal, message: string) => void;
  /**
   * The Undo reason policy refused a reason-less Undo (409
   * `undo_reason_required`): no error, no rejection (`rejected` is left
   * as it was), `onRejected` not called and the same `device_event_id`
   * kept. The unknown-outcome state is CLEARED: the refusal was judged
   * after the idempotency fast path and the post-lock re-check, so the
   * outcome of an earlier unknown attempt is now known — nothing is
   * recorded under this key. Without this handler such a refusal is an
   * ordinary rejection.
   */
  onReasonRequired?: (message: string) => void;
}): OneShotWrite<T> {
  const requireSession = useRequireWorkerSession();
  const deviceRefused = useStationDeviceRefused();
  const deviceEventId = useRef(newDeviceEventId());
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  useTrackOutcomeUnknown(outcomeUnknown);

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
      if (workerSessionRequired(error)) {
        // Nothing recorded and nothing wrong with the intent: no error,
        // no rejection, the same `device_event_id` — the sign-in modal
        // opens above this dialog and Confirm resends the same request.
        requireSession();
        setBusy(false);
        return;
      }
      if (stationDeviceRefusal(error) !== null) {
        // Refused before the idempotency fast path: no error, no
        // rejection, the same `device_event_id` and the unknown-outcome
        // state exactly as it was — the enrollment dialog opens above
        // this dialog and Confirm resends the same request afterwards.
        deviceRefused({ outcomeUnknown });
        setBusy(false);
        return;
      }
      const refusal = badgeGateRefusal(error);
      if (refusal && onGateRefusal) {
        // Judged after the idempotency fast path and the post-lock
        // re-check, like the reason refusal below: nothing is recorded
        // under this device_event_id, so an unknown outcome is resolved.
        setOutcomeUnknown(false);
        setBusy(false);
        onGateRefusal(refusal, errorMessage(error));
        return;
      }
      if (undoReasonRequired(error) && onReasonRequired) {
        // Judged after the idempotency fast path and the post-lock
        // re-check: nothing is recorded under this device_event_id, so
        // an earlier unknown outcome is now known (not recorded).
        setOutcomeUnknown(false);
        setBusy(false);
        onReasonRequired(errorMessage(error));
        return;
      }
      if (writeOutcomeUnknown(error)) {
        setOutcomeUnknown(true);
        setServerError(null);
      } else {
        // A station-permission refusal is judged after the fast path
        // and the post-lock re-check: nothing is recorded under this
        // key, so an earlier unknown outcome is now known.
        if (stationPermissionDenied(error)) setOutcomeUnknown(false);
        setServerError(errorMessage(error));
        setRejected(true);
        onRejected?.();
      }
      setBusy(false);
      return;
    }
    setBusy(false);
    setResult(confirmed);
    onDone(confirmed);
  }, [
    busy,
    writeBlocked,
    send,
    onDone,
    onRejected,
    onGateRefusal,
    onReasonRequired,
    requireSession,
    deviceRefused,
    outcomeUnknown,
  ]);

  const clearError = useCallback(() => setServerError(null), []);

  const resetIntent = useCallback(() => {
    if (outcomeUnknown) return;
    deviceEventId.current = newDeviceEventId();
    setServerError(null);
    setRejected(false);
  }, [outcomeUnknown]);

  return {
    busy,
    serverError,
    outcomeUnknown,
    rejected,
    deviceEventId: deviceEventId.current,
    submit,
    clearError,
    resetIntent,
    result,
  };
}

/** The approved in-place copy of an unrecognized gate badge (equal to
 * the server's refusal text). */
export const BADGE_NOT_RECOGNIZED =
  'Badge not recognized. Check the badge and scan again — nothing was recorded.';

/** What the final gate needs of the dialog's one-shot write. */
type GateWrite = Pick<
  OneShotWrite<unknown>,
  'outcomeUnknown' | 'serverError' | 'submit'
>;

export interface FinalGateControl {
  /** The open gate's form; null while the gate is closed. */
  form: FinalGate | null;
  /** In the BADGE gate: the in-place error of an unrecognized badge. */
  error: string | null;
  /** The server's reason for a gate switch — shown inside the BADGE
   * gate when `noticeInGate`, else above the summary buttons. */
  notice: string | null;
  noticeInGate: boolean;
  /** The badge scanned in THIS gate opening, for the request; null in
   * the question form. Read by the write's `send` closure. */
  badge: () => string | null;
  /** The handler to pass to `useOneShotWrite`. */
  onGateRefusal: (refusal: BadgeGateRefusal, message: string) => void;
  /** The summary's primary: resend the frozen request, or open the gate. */
  request: (write: GateWrite) => void;
  /** The question's `Yes`. */
  answerQuestion: (write: GateWrite) => void;
  /** A badge scanned (or a demo badge clicked) in the BADGE gate. */
  scanBadge: (badge: string, write: GateWrite) => void;
  /** Cancel / Escape in either gate form: back to the summary. */
  cancel: () => void;
  /**
   * A refusal outside the gate changed the intent (the Undo reason):
   * close any open gate, forget the last badge and drop a stale gate
   * notice, so the next request opens the gate again — the changed
   * intent is confirmed deliberately, a badge only by a new scan.
   */
  confirmAgain: () => void;
}

/**
 * The final-confirmation gate of a sensitive action (GUI_DESIGN §4.6;
 * PROJECT_PROFILE §16, §19): a toned question, or a Worker badge scan
 * where the server reports the BADGE form for `action`. The form the
 * gate opens in is the server's — the context's `finalGates`, or the
 * form a typed refusal just named (fresher than any context read, so a
 * switch never waits on the background re-read). Retry rule:
 * - an unknown outcome resends the frozen request, badge included,
 *   without asking again (the same physical intent; a committed
 *   original replays whatever the badge);
 * - an explicit refusal of a question-form request resends without
 *   asking again (the intent was already confirmed);
 * - after an explicit refusal of a badge-form request, or any typed
 *   gate refusal, the gate opens again: a badge is sent only by the
 *   scan that just happened in this gate, so a later Retry — possibly
 *   by another operator — never records an earlier badge's Worker.
 * The `device_event_id` is kept throughout (a refusal recorded nothing).
 */
export function useFinalGate({
  station,
  action,
  onGateChanged,
}: {
  station: StationContext;
  action: SensitiveAction;
  /** The server named another gate form: re-read the station context. */
  onGateChanged?: () => void;
}): FinalGateControl {
  // The badge of the LAST request sent: set by the gate's scan, cleared
  // by the question's Yes — so it is never a badge from an earlier gate
  // opening, and it tells the retry rule which form was refused.
  const badgeRef = useRef<string | null>(null);
  // Set by a typed gate refusal while a badge request is in flight: the
  // badge gate then stays as the refusal left it, else it closes.
  const refusedRef = useRef(false);
  const [form, setForm] = useState<FinalGate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeInGate, setNoticeInGate] = useState(false);
  const [formHint, setFormHint] = useState<FinalGate | null>(null);
  const [gateRedo, setGateRedo] = useState(false);

  function open(next: FinalGate) {
    setError(null);
    setForm(next);
  }

  /** Bookkeeping of a request about to be sent from the gate. */
  function sending() {
    setFormHint(null);
    setError(null);
    setNotice(null);
    setGateRedo(false);
  }

  function onGateRefusal(refusal: BadgeGateRefusal, message: string) {
    refusedRef.current = true;
    setGateRedo(true);
    if (refusal === 'NOT_RECOGNIZED') {
      setNotice(null);
      setError(BADGE_NOT_RECOGNIZED);
      setForm('BADGE');
      return;
    }
    if (refusal === 'REQUIRED') {
      // A question was sent and the server now wants a badge: the gate
      // re-opens directly in BADGE form — the scan is the confirmation.
      setFormHint('BADGE');
      open('BADGE');
      setNoticeInGate(true);
    } else {
      // A badge was sent and the server now wants the question: the
      // operator answers it deliberately after the next Confirm.
      setFormHint('QUESTION');
      setForm(null);
      setError(null);
      setNoticeInGate(false);
    }
    setNotice(message);
    onGateChanged?.();
  }

  return {
    form,
    error,
    notice,
    noticeInGate,
    badge: () => badgeRef.current,
    onGateRefusal,
    request: (write) => {
      if (write.outcomeUnknown && !gateRedo) {
        void write.submit();
        return;
      }
      if (write.serverError && badgeRef.current === null && !gateRedo) {
        void write.submit();
        return;
      }
      open(formHint ?? finalGateFor(station, action));
    },
    answerQuestion: (write) => {
      badgeRef.current = null;
      sending();
      setForm(null);
      void write.submit();
    },
    scanBadge: (badge, write) => {
      badgeRef.current = badge;
      refusedRef.current = false;
      sending();
      void write.submit().then(() => {
        // Success completes the dialog; any other outcome than a typed
        // gate refusal (unknown outcome, explicit refusal) closes the
        // gate so the summary shows it.
        if (!refusedRef.current) setForm(null);
      });
    },
    cancel: () => {
      setForm(null);
      setError(null);
      if (noticeInGate) setNotice(null);
    },
    confirmAgain: () => {
      badgeRef.current = null;
      setGateRedo(true);
      setForm(null);
      setError(null);
      setNotice(null);
      setNoticeInGate(false);
    },
  };
}
