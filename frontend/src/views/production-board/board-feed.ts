// Production Board feed: the polling read of `GET /api/production-board`
// (GUI_DESIGN §5 auto-refresh, stale feed).
//
// The refresh, staleness and recovery behaviour is the shared
// monitoring feed (views/monitoring-feed) — the Area Board reads its
// own board through the same one, so no monitoring view invents its
// own rules. This module only binds it to the board's loader and
// period.

import { useCallback } from 'react';

import type { ProductionBoard } from '../../api/production-board';
import { loadProductionBoard } from '../../api/production-board';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import type { MonitoringFeed, MonitoringFeedState } from '../monitoring-feed';
import { useMonitoringFeed } from '../monitoring-feed';
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

function boardState(
  state: MonitoringFeedState<ProductionBoard>,
): BoardFeedState {
  return state.status === 'ready'
    ? { status: 'ready', board: state.data, stale: state.stale }
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
  const load = useCallback(
    () => loadProductionBoard(departmentId),
    [departmentId],
  );
  const feed: MonitoringFeed<ProductionBoard> = useMonitoringFeed(
    load,
    BOARD_REFRESH_MS,
    connectivity,
    enabled,
  );
  return { state: boardState(feed.state), reload: feed.reload };
}
