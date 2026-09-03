// Production Board feed: the polling read of `GET /api/production-board`
// (GUI_DESIGN §5 auto-refresh, stale feed).
//
// One request at a time: the next refresh is armed only after the
// previous answer arrived (`BOARD_REFRESH_MS` later), so a slow server
// never accumulates overlapping requests. A refresh that fails keeps
// the last COMPLETE board on screen and marks the feed stale — the
// title's `● Live` status turns the warning tone with the explicit
// `Feed stale — reconnecting` note — nothing partial is ever shown,
// and polling continues so the board recovers by itself. The first
// load has nothing to keep, so its failure is the error state with a
// Retry. Connectivity returning after a loss (the shared health probe:
// `unavailable` → `connected`) triggers an immediate refresh instead
// of waiting out the period. A generation counter discards the answer
// of a superseded request (an unmounted board, a Retry racing a
// pending refresh).

import { useCallback, useEffect, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import type { ProductionBoard } from '../../api/production-board';
import { loadProductionBoard } from '../../api/production-board';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import { BOARD_REFRESH_MS } from './board-logic';

export type BoardFeedState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready';
      board: ProductionBoard;
      /** The last refresh failed: `board` is the last complete answer. */
      stale: boolean;
    };

export interface BoardFeed {
  state: BoardFeedState;
  /** Load again now (the Retry of the error state). */
  reload: () => void;
}

/**
 * Read the board `departmentId` (null: the server resolves the single
 * active Department) and keep it fresh. `enabled: false` (development
 * state previews) performs no request at all.
 */
export function useBoardFeed(
  departmentId: number | null,
  connectivity: ConnectivityStatus,
  enabled = true,
): BoardFeed {
  const [state, setState] = useState<BoardFeedState>({ status: 'loading' });
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
      void loadProductionBoard(departmentId).then(
        (board) => {
          if (cancelled || liveGeneration.current !== requested) return;
          setState({ status: 'ready', board, stale: false });
          timer.current = window.setTimeout(run, BOARD_REFRESH_MS);
        },
        (error: unknown) => {
          if (cancelled || liveGeneration.current !== requested) return;
          setState((current) =>
            current.status === 'ready'
              ? { ...current, stale: true }
              : { status: 'error', message: errorMessage(error) },
          );
          timer.current = window.setTimeout(run, BOARD_REFRESH_MS);
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
  }, [departmentId, generation, enabled]);

  return { state, reload };
}
