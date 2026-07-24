import { useEffect, useState } from 'react';

type ConnectivityState = 'loading' | 'connected' | 'unavailable';

// Bounded timeout so the screen cannot stay in the loading state forever
// when the request hangs (accepted connection that never responds).
const HEALTH_REQUEST_TIMEOUT_MS = 5000;

// Phase 1 connectivity screen only: it proves the frontend can reach the
// backend health endpoint through the dev-server proxy. No application
// shell, routing, theming, or domain UI belongs here yet.
export function App() {
  const [connectivity, setConnectivity] =
    useState<ConnectivityState>('loading');

  useEffect(() => {
    let cancelled = false;

    async function checkBackend() {
      try {
        const response = await fetch('/api/health', {
          signal: AbortSignal.timeout(HEALTH_REQUEST_TIMEOUT_MS),
        });
        if (!cancelled) {
          setConnectivity(response.ok ? 'connected' : 'unavailable');
        }
      } catch {
        if (!cancelled) {
          setConnectivity('unavailable');
        }
      }
    }

    void checkBackend();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1>PartFlow</h1>
      {connectivity === 'loading' && (
        <p role="status">Checking backend connection…</p>
      )}
      {connectivity === 'connected' && <p role="status">Backend connected.</p>}
      {connectivity === 'unavailable' && (
        <p role="alert">
          Backend unavailable. Verify that the API and database are running.
        </p>
      )}
    </main>
  );
}
