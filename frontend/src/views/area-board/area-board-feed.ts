// Area Board feed: the polling read of `GET /api/area-board`
// (GUI_DESIGN §6 — the live Management monitoring view).
//
// The refresh, staleness and recovery behaviour is the shared
// monitoring feed (views/monitoring-feed), the same one the Production
// Board reads through: one request at a time, a failed refresh keeping
// the last COMPLETE board with the feed marked stale, a failed FIRST
// load as the error state with Retry, and an immediate refresh when
// connectivity returns.

import { useCallback } from 'react';

import type { AreaBoard } from '../../api/area-board';
import { loadAreaBoard } from '../../api/area-board';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import type { MonitoringFeed } from '../monitoring-feed';
import { useMonitoringFeed } from '../monitoring-feed';

/**
 * Refresh period of the Area Board (the Production Board's own period
 * — one monitoring cadence across the live views). A Management view
 * is read while work moves on the floor, so it follows the same feed
 * rather than waiting for a manual reload.
 */
export const AREA_BOARD_REFRESH_MS = 15_000;

export function useAreaBoardFeed(
  departmentId: number | null,
  connectivity: ConnectivityStatus,
  enabled = true,
): MonitoringFeed<AreaBoard> {
  const load = useCallback(() => loadAreaBoard(departmentId), [departmentId]);
  return useMonitoringFeed(load, AREA_BOARD_REFRESH_MS, connectivity, enabled);
}
