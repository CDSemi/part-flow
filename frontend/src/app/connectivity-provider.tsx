import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { ConnectivityContext } from './connectivity-context';
import type { ConnectivityStatus } from './connectivity-context';

// Bounded timeout so the screen cannot stay in the loading state forever
// when the request hangs (accepted connection that never responds).
// Preserved from the Phase 1 connectivity screen.
const HEALTH_REQUEST_TIMEOUT_MS = 5000;

// Passive re-check so a lost backend eventually surfaces as OFFLINE even
// without a user action; the Retry control remains the explicit path.
const RECHECK_INTERVAL_MS = 30000;

export function ConnectivityProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectivityStatus>('connecting');
  // Generation counter invalidates in-flight checks after unmount/retry.
  const generation = useRef(0);

  const runCheck = useCallback(async (showConnecting: boolean) => {
    const gen = ++generation.current;
    if (showConnecting) setStatus('connecting');
    try {
      const response = await fetch('/api/health', {
        signal: AbortSignal.timeout(HEALTH_REQUEST_TIMEOUT_MS),
      });
      if (generation.current === gen) {
        setStatus(response.ok ? 'connected' : 'unavailable');
      }
    } catch {
      if (generation.current === gen) {
        setStatus('unavailable');
      }
    }
  }, []);

  useEffect(() => {
    void runCheck(true);
    const intervalId = setInterval(() => {
      void runCheck(false);
    }, RECHECK_INTERVAL_MS);
    return () => {
      generation.current += 1;
      clearInterval(intervalId);
    };
  }, [runCheck]);

  const retry = useCallback(() => {
    void runCheck(true);
  }, [runCheck]);

  return (
    <ConnectivityContext.Provider value={{ status, retry }}>
      {children}
    </ConnectivityContext.Provider>
  );
}
