// The Scan Station's view of a scanned Worker Session (PROJECT_PROFILE
// §19, GUI_DESIGN §4.12). The SERVER owns the session: it signs in,
// switches, refreshes and expires it, and judges it again at every
// production command. The station only keeps the latest server answer
// to render the pill countdown and to raise the blocking sign-in modal
// when the session is missing or its deadline passed. The same station
// owner also hears its dialogs' enrolled-device refusals (Phase 14
// slice 4): a write refused because this device is not (or no longer)
// enrolled for the station raises the enrollment dialog over the open
// dialog, which keeps its draft and its request. Production-safe: no
// mock data, no JSX.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import type { WorkerSession } from '../../api/scan-station';

/** One applied server answer, with its deadline on the CLIENT clock. */
interface AppliedSession {
  session: WorkerSession;
  /** `expiresAt` corrected by the server/client clock offset measured
   * when the answer arrived: expiresAt − (serverNow − receivedAt). */
  expiresAtClientMs: number;
}

export interface WorkerSessionClock {
  /** The latest applied session while its deadline has not passed. */
  live: WorkerSession | null;
  /** The live session's deadline on the client clock (pill countdown). */
  liveExpiresAtClientMs: number | null;
  /** A session was valid at some point in this page (the modal then
   * says the session expired rather than that sign-in is required). */
  hadSession: boolean;
  /** The next send-order ticket; take it right before sending a
   * session-bearing request. */
  ticket: () => number;
  /** Apply a server answer (null = no valid session) unless an answer
   * sent later was already applied. */
  apply: (session: WorkerSession | null, ticket: number) => void;
  /** The server refused a command for want of a session: drop the
   * session so the modal blocks, and ignore every answer sent before. */
  markRequired: () => void;
}

/**
 * The station's session state from the latest applied server answer.
 * Answers are applied in SEND order (R9): every session-bearing request
 * takes a ticket immediately before it is sent, and an answer whose
 * ticket is older than the last applied one is ignored — a slow context
 * read or resolve can never restore a session a later sign-in, switch
 * or refusal replaced. Expiry flips through one timer aimed at the
 * corrected deadline; the displayed countdown derives from the shared
 * UI clock inside the pill.
 */
export function useWorkerSessionClock(): WorkerSessionClock {
  const [applied, setApplied] = useState<AppliedSession | null>(null);
  const [hadSession, setHadSession] = useState(false);
  // The deadline whose timer fired; equal to the applied deadline
  // exactly while that session is expired.
  const [expiredDeadline, setExpiredDeadline] = useState<number | null>(null);
  const lastTicket = useRef(0);
  const lastApplied = useRef(0);

  const ticket = useCallback(() => {
    lastTicket.current += 1;
    return lastTicket.current;
  }, []);

  const apply = useCallback((session: WorkerSession | null, sent: number) => {
    if (sent < lastApplied.current) return;
    lastApplied.current = sent;
    if (session === null) {
      setApplied(null);
      return;
    }
    const receivedAt = Date.now();
    const offset = Date.parse(session.serverNow) - receivedAt;
    setApplied({
      session,
      expiresAtClientMs: Date.parse(session.expiresAt) - offset,
    });
    setHadSession(true);
  }, []);

  const markRequired = useCallback(() => {
    lastTicket.current += 1;
    lastApplied.current = lastTicket.current;
    setApplied(null);
  }, []);

  useEffect(() => {
    if (applied === null) return;
    const deadline = applied.expiresAtClientMs;
    const timer = window.setTimeout(
      () => setExpiredDeadline(deadline),
      Math.max(0, deadline - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [applied]);

  const live =
    applied !== null && expiredDeadline !== applied.expiresAtClientMs
      ? applied
      : null;
  return {
    live: live?.session ?? null,
    liveExpiresAtClientMs: live?.expiresAtClientMs ?? null,
    hadSession,
    ticket,
    apply,
    markRequired,
  };
}

/** What the station's dialogs need from the session owner. */
export interface StationSession {
  /** A command was refused with `worker_session_required`: raise the
   * modal (the open dialog keeps its draft and its request). */
  requireSession: () => void;
  ticket: () => number;
  applyWorkerSession: (session: WorkerSession | null, ticket: number) => void;
  /**
   * A write was refused because this device is not enrolled for the
   * station (any more) or is enrolled for another one — judged before
   * the idempotency fast path, so it proves nothing about an earlier
   * attempt. `outcomeUnknown`: the refused intent was already in the
   * unknown-outcome state. The open dialog keeps its draft, its request
   * and its `device_event_id`.
   */
  deviceRefused: (write: { outcomeUnknown: boolean }) => void;
  /** One open intent entered the unknown-outcome state; the returned
   * function says it left it (answered, abandoned or closed). */
  trackOutcomeUnknown: () => () => void;
}

export const StationSessionContext = createContext<StationSession>({
  requireSession: () => undefined,
  ticket: () => 0,
  applyWorkerSession: () => undefined,
  deviceRefused: () => undefined,
  trackOutcomeUnknown: () => () => undefined,
});

/** The handler of a `worker_session_required` refusal. */
export function useRequireWorkerSession(): () => void {
  return useContext(StationSessionContext).requireSession;
}

/** The handler of an enrolled-device refusal of a write. */
export function useStationDeviceRefused(): StationSession['deviceRefused'] {
  return useContext(StationSessionContext).deviceRefused;
}

/** Report this dialog's unknown-outcome state to the station, which
 * says so in the enrollment dialog (confirm again after enrolling). */
export function useTrackOutcomeUnknown(outcomeUnknown: boolean): void {
  const { trackOutcomeUnknown } = useContext(StationSessionContext);
  useEffect(
    () => (outcomeUnknown ? trackOutcomeUnknown() : undefined),
    [outcomeUnknown, trackOutcomeUnknown],
  );
}

/** Ticket + apply, for a dialog sending its own session-bearing read. */
export function useWorkerSessionSource(): Pick<
  StationSession,
  'ticket' | 'applyWorkerSession'
> {
  const { ticket, applyWorkerSession } = useContext(StationSessionContext);
  return useMemo(
    () => ({ ticket, applyWorkerSession }),
    [ticket, applyWorkerSession],
  );
}
