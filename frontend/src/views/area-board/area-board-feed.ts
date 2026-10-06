// Area Board feed: the polling read of `GET /api/area-board`
// (GUI_DESIGN §6 — the live Management monitoring view).
//
// The refresh, staleness and recovery behaviour is the shared
// monitoring feed (views/monitoring-feed), the same one the Production
// Board reads through: one request at a time, a failed refresh keeping
// the last COMPLETE board with the feed marked stale, a failed FIRST
// load as the error state with Retry, and an immediate refresh when
// connectivity returns. The Due Soon warning policy its due countdowns
// derive from (`GET /api/policies/due-soon`, GUI_DESIGN §3.12) is part
// of the same ready state: every refresh re-reads both, the loader
// settles only after both requests settled, and a failure of either is
// a failed read — never a fallback policy.

import { useCallback } from 'react';

import type { AreaBoard } from '../../api/area-board';
import { loadAreaBoard } from '../../api/area-board';
import { getDueSoonPolicy } from '../../api/policies';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import type { DueSoonPolicy } from '../dates';
import type { MonitoringFeed } from '../monitoring-feed';
import { useMonitoringFeed } from '../monitoring-feed';

/**
 * Refresh period of the Area Board (the Production Board's own period
 * — one monitoring cadence across the live views). A Management view
 * is read while work moves on the floor, so it follows the same feed
 * rather than waiting for a manual reload.
 */
export const AREA_BOARD_REFRESH_MS = 15_000;

/** One complete Area Board answer: the board and its Due Soon policy. */
export interface AreaBoardFeedData {
  board: AreaBoard;
  dueSoon: DueSoonPolicy;
}

export function useAreaBoardFeed(
  departmentId: number | null,
  connectivity: ConnectivityStatus,
  enabled = true,
): MonitoringFeed<AreaBoardFeedData> {
  // The board request starts first; a board failure is reported first.
  const load = useCallback(
    () =>
      Promise.allSettled([
        loadAreaBoard(departmentId),
        getDueSoonPolicy(),
      ]).then(([board, dueSoon]): AreaBoardFeedData => {
        if (board.status === 'rejected') throw board.reason;
        if (dueSoon.status === 'rejected') throw dueSoon.reason;
        return { board: board.value, dueSoon: dueSoon.value };
      }),
    [departmentId],
  );
  return useMonitoringFeed(load, AREA_BOARD_REFRESH_MS, connectivity, enabled);
}
