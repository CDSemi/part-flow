// The shared monitoring feed: the polling read behind a live view
// (GUI_DESIGN §5 and §6 — auto-refresh, stale feed).
//
// One request at a time: the next refresh is armed only after the
// previous answer arrived (`refreshMs` later), so a slow server never
// accumulates overlapping requests. A refresh that fails keeps the last
// COMPLETE answer on screen and marks the feed stale — the view shows
// its `Feed stale — reconnecting` status — nothing partial is ever
// shown, and polling continues so the view recovers by itself. The
// first load has nothing to keep, so its failure is the error state
// with a Retry. Connectivity returning after a loss (the shared health
// probe: `unavailable` → `connected`) triggers an immediate refresh
// instead of waiting out the period. A generation counter discards the
// answer of a superseded request (an unmounted view, a Retry racing a
// pending refresh).
//
// The Production Board and the Area Board share this ONE feed
// behaviour: a monitoring view must never invent its own refresh,
// staleness or recovery rules.

import { useCallback, useEffect, useRef, useState } from 'react';

import { errorMessage } from '../api/client';
import type { ConnectivityStatus } from '../app/connectivity-context';

export type MonitoringFeedState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready';
      data: T;
      /** The last refresh failed: `data` is the last complete answer. */
      stale: boolean;
    };

export interface MonitoringFeed<T> {
  state: MonitoringFeedState<T>;
  /** Load again now (the Retry of the error state). */
  reload: () => void;
}

/**
 * Read `load()` and keep it fresh. `load` must be referentially stable
 * across renders that should NOT re-read (wrap it in `useCallback`
 * over its real inputs); `enabled: false` (development state previews)
 * performs no request at all.
 */
export function useMonitoringFeed<T>(
  load: () => Promise<T>,
  refreshMs: number,
  connectivity: ConnectivityStatus,
  enabled = true,
): MonitoringFeed<T> {
  const [state, setState] = useState<MonitoringFeedState<T>>({
    status: 'loading',
  });
  const [generation, setGeneration] = useState(0);
  const liveGeneration = useRef(0);
  const timer = useRef<number | null>(null);

  const reload = useCallback(() => setGeneration((value) => value + 1), []);

  // A regained connection (lost → healthy) refreshes immediately: the
  // generation bump cancels the pending period and issues a fresh
  // request. The initial `connecting` → `connected` of the shared
  // probe is not a return — the first load is already in flight.
  const previousConnectivity = useRef(connectivity);
  useEffect(() => {
    if (
      connectivity === 'connected' &&
      previousConnectivity.current === 'unavailable'
    ) {
      reload();
    }
    previousConnectivity.current = connectivity;
  }, [connectivity, reload]);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const run = () => {
      const requested = ++liveGeneration.current;
      void load().then(
        (data) => {
          if (cancelled || liveGeneration.current !== requested) return;
          setState({ status: 'ready', data, stale: false });
          timer.current = window.setTimeout(run, refreshMs);
        },
        (error: unknown) => {
          if (cancelled || liveGeneration.current !== requested) return;
          setState((current) =>
            current.status === 'ready'
              ? { ...current, stale: true }
              : { status: 'error', message: errorMessage(error) },
          );
          timer.current = window.setTimeout(run, refreshMs);
        },
      );
    };
    run();
    return () => {
      cancelled = true;
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [load, refreshMs, generation, enabled]);

  return { state, reload };
}
