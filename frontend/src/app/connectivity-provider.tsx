import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { BUNDLE_RELEASE, setReleaseMismatchListener } from '../api/client';
import { ConnectivityContext } from './connectivity-context';
import type { ConnectivityStatus } from './connectivity-context';

// Fast connectivity-loss detection for the Scan Station (Phase 2 —
// browser events + frequent lightweight polling; no WebSocket/SSE):
//
// - browser `offline` marks the application unavailable immediately;
// - browser `online`, tab focus and visibility trigger an immediate
//   re-check;
// - while active, the lightweight health endpoint is probed every
//   second with a request timeout below the probe interval, so a lost
//   backend surfaces in roughly one second;
// - probes never overlap, and passive probes never flip the status to
//   `connecting` (no UI flicker).
//
// Connectivity status is an EARLY WARNING, never permission to record a
// Movement optimistically: a scan is successful only after the server
// confirms the write, and a write that races a connection loss fails as
// "nothing recorded". Nothing is queued while offline.
//
// Release check (Phase 16): the health answer names the server's
// release. When it differs from this bundle's release — or any request
// was refused with 409 `release_mismatch` — a reachable server reads
// `outdated` (writes blocked like disconnected, reads continue) until a
// later health answer carries this bundle's release again. An answer
// without `release` (or with no readable body) leaves that judgement
// unchanged.

const HEALTH_REQUEST_TIMEOUT_MS = 900;
const PROBE_INTERVAL_MS = 1000;

/** The `release` string of a health answer body, if it carries one. */
async function healthRelease(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object') {
      const release = (body as Record<string, unknown>).release;
      if (typeof release === 'string') return release;
    }
  } catch {
    // A missing or unreadable body is no evidence either way.
  }
  return null;
}

export function ConnectivityProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectivityStatus>('connecting');
  const [serverRelease, setServerRelease] = useState<string | null>(null);
  // Generation counter invalidates in-flight checks after unmount,
  // explicit retry, and browser offline events.
  const generation = useRef(0);
  // The AbortController of the probe currently in flight (if any) —
  // both the no-overlap guard and the unmount cleanup handle.
  const activeProbe = useRef<AbortController | null>(null);
  // Whether the server is known to run another release than this page.
  const releaseMismatch = useRef(false);

  const runCheck = useCallback(async (showConnecting: boolean) => {
    if (activeProbe.current) return; // never overlap health probes
    const controller = new AbortController();
    activeProbe.current = controller;
    const gen = ++generation.current;
    if (showConnecting) setStatus('connecting');
    // Bounded timeout below the probe interval keeps the one-second
    // detection target and prevents a hung request from blocking the
    // next probe.
    const timeoutId = setTimeout(
      () =>
        controller.abort(
          new DOMException('The operation timed out.', 'TimeoutError'),
        ),
      HEALTH_REQUEST_TIMEOUT_MS,
    );
    try {
      const response = await fetch('/api/health', {
        signal: controller.signal,
      });
      const release = await healthRelease(response);
      if (generation.current === gen) {
        if (release !== null) {
          releaseMismatch.current = release !== BUNDLE_RELEASE;
          setServerRelease(release);
        }
        setStatus(
          !response.ok
            ? 'unavailable'
            : releaseMismatch.current
              ? 'outdated'
              : 'connected',
        );
      }
    } catch {
      if (generation.current === gen) {
        setStatus('unavailable');
      }
    } finally {
      clearTimeout(timeoutId);
      if (activeProbe.current === controller) activeProbe.current = null;
    }
  }, []);

  useEffect(() => {
    void runCheck(true);
    const intervalId = setInterval(() => {
      void runCheck(false);
    }, PROBE_INTERVAL_MS);
    // `offline` is authoritative bad news: mark unavailable immediately
    // and invalidate any probe still in flight.
    const handleOffline = () => {
      generation.current += 1;
      setStatus('unavailable');
    };
    const handleOnline = () => {
      void runCheck(false);
    };
    const handleFocus = () => {
      void runCheck(false);
    };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') void runCheck(false);
    };
    // A request refused with 409 `release_mismatch`: the server answers
    // but runs another release — outdated at once (a lost connection
    // stays the stronger state), then confirmed by an immediate check.
    setReleaseMismatchListener(() => {
      releaseMismatch.current = true;
      setStatus((current) =>
        current === 'unavailable' ? current : 'outdated',
      );
      void runCheck(false);
    });
    window.addEventListener('offline', handleOffline);
    window.addEventListener('online', handleOnline);
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      generation.current += 1;
      clearInterval(intervalId);
      setReleaseMismatchListener(null);
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibility);
      activeProbe.current?.abort();
      activeProbe.current = null;
    };
  }, [runCheck]);

  const retry = useCallback(() => {
    // Explicit user retry replaces any probe in flight and is allowed
    // to show the connecting state.
    activeProbe.current?.abort();
    activeProbe.current = null;
    generation.current += 1;
    void runCheck(true);
  }, [runCheck]);

  return (
    <ConnectivityContext.Provider value={{ status, retry, serverRelease }}>
      {children}
    </ConnectivityContext.Provider>
  );
}
