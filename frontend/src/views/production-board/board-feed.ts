// Production Board feed: the polling read of `GET /api/production-board`
// together with the Due Soon warning policy (`GET /api/policies/due-soon`)
// its due countdowns derive from (GUI_DESIGN §5 auto-refresh, stale
// feed; §3.12). Both are one ready state: every refresh re-reads both,
// the loader settles only after BOTH requests settled (one request at a
// time), and a failure of either is a failed read — the last complete
// pair stays (stale), or the first load is the error state. There is
// never a fallback policy.
//
// The refresh, staleness and recovery behaviour is the shared
// monitoring feed (views/monitoring-feed) — the Area Board reads its
// own board through the same one, so no monitoring view invents its
// own rules. This module only binds it to the board's loader and
// period.

import { useCallback } from 'react';

import { getDueSoonPolicy } from '../../api/policies';
import type { ProductionBoard } from '../../api/production-board';
import { loadProductionBoard } from '../../api/production-board';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import type { DueSoonPolicy } from '../dates';
import type { MonitoringFeed, MonitoringFeedState } from '../monitoring-feed';
import { useMonitoringFeed } from '../monitoring-feed';
import { BOARD_REFRESH_MS } from './board-logic';

export type BoardFeedState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready';
      board: ProductionBoard;
      /** The Due Soon warning policy read with `board`. */
      dueSoon: DueSoonPolicy;
      /** The last refresh failed: `board` and `dueSoon` are the last
       * complete answer. */
      stale: boolean;
    };

interface BoardFeedData {
  board: ProductionBoard;
  dueSoon: DueSoonPolicy;
}

export interface BoardFeed {
  state: BoardFeedState;
  /** Load again now (the Retry of the error state). */
  reload: () => void;
}

function boardState(state: MonitoringFeedState<BoardFeedData>): BoardFeedState {
  return state.status === 'ready'
    ? {
        status: 'ready',
        board: state.data.board,
        dueSoon: state.data.dueSoon,
        stale: state.stale,
      }
    : state;
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
  // The board request starts first; the loader settles only after both
  // requests settled, so the next refresh is never armed while one is
  // still in flight (the feed's one-request-at-a-time rule).
  const load = useCallback(
    () =>
      Promise.allSettled([
        loadProductionBoard(departmentId),
        getDueSoonPolicy(),
      ]).then(([board, dueSoon]): BoardFeedData => {
        if (board.status === 'rejected') throw board.reason;
        if (dueSoon.status === 'rejected') throw dueSoon.reason;
        return { board: board.value, dueSoon: dueSoon.value };
      }),
    [departmentId],
  );
  const feed: MonitoringFeed<BoardFeedData> = useMonitoringFeed(
    load,
    BOARD_REFRESH_MS,
    connectivity,
    enabled,
  );
  return { state: boardState(feed.state), reload: feed.reload };
}
