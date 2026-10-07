// What an enrolled Scan Station may do (Phase 14 slice 4 — owner
// decision OD-S4-1): every station action needs the matching key of
// the role applied at Scan Stations, which the station context reports
// as `stationPermissions`. The station HIDES the actions that role does
// not grant (never shows them disabled); the server stays the authority
// and refuses a command the role does not grant (403
// `station_permission_denied`, nothing recorded) — a station showing a
// stale context gets that refusal as an ordinary rejection. Scan inputs
// (PN, Machine, Worker badge) are never hidden: their refusal is the
// server's.
//
// Pure presentation table, mirrored from the server's command → key
// table. Production-safe: no mock data, no framework imports.

import type { StationPermission } from '../../api/scan-station';

/** One action a Scan Station offers through a choice, a row or card
 * action, a follow-up dialog or a control. */
export type StationAction =
  | 'RECEIVE'
  | 'ASSIGN'
  | 'QUEUE'
  | 'DONE'
  | 'TRANSFER'
  | 'REPAIR'
  | 'COMBINE'
  | 'SCRAP'
  | 'ADD_QUANTITY'
  | 'STOCK'
  | 'ALLOCATE'
  | 'ADJUST_ALLOCATION'
  | 'UNDO';

/** The station key each action needs (frozen mirror of the server). */
export const STATION_ACTION_KEY: Readonly<
  Record<StationAction, StationPermission>
> = Object.freeze({
  RECEIVE: 'RECEIVE_QUANTITY',
  ASSIGN: 'ASSIGN_QUANTITY_TO_MACHINE',
  QUEUE: 'ASSIGN_QUANTITY_TO_MACHINE',
  DONE: 'CONFIRM_QUANTITY',
  TRANSFER: 'CONFIRM_QUANTITY',
  REPAIR: 'CONFIRM_QUANTITY',
  COMBINE: 'CONFIRM_QUANTITY',
  SCRAP: 'CONFIRM_QUANTITY',
  ADD_QUANTITY: 'CONFIRM_QUANTITY',
  STOCK: 'COMPLETE_INTO_STOCKROOM',
  ALLOCATE: 'CONFIRM_SUGGESTED_ALLOCATION',
  ADJUST_ALLOCATION: 'ADJUST_SUGGESTED_ALLOCATION',
  UNDO: 'UNDO_RECENT_SCANS',
});

/**
 * The actions a scan can lead to on its own (a resolution that would
 * open only that action): the station refuses them itself, with the
 * server's text. Allocation, its adjustment and Undo are never entered
 * from a scan — their controls are simply not rendered, so their
 * refusal is only ever the server's.
 */
export type ScanLedStationAction = Exclude<
  StationAction,
  'ALLOCATE' | 'ADJUST_ALLOCATION' | 'UNDO'
>;

/** The action wording of the server's refusal (its per-command text). */
const ACTION_TEXT: Readonly<Record<ScanLedStationAction, string>> = {
  RECEIVE: 'receive quantity',
  ASSIGN: 'assign quantity to Machines',
  QUEUE: 'assign quantity to Machines',
  DONE: 'confirm quantity',
  TRANSFER: 'confirm quantity',
  REPAIR: 'confirm quantity',
  COMBINE: 'confirm quantity',
  SCRAP: 'confirm quantity',
  ADD_QUANTITY: 'confirm quantity',
  STOCK: 'complete production into the Stockroom',
};

/** Whether the role applied at Scan Stations grants `action`. */
export function stationCan(
  action: StationAction,
  stationPermissions: readonly StationPermission[],
): boolean {
  return stationPermissions.includes(STATION_ACTION_KEY[action]);
}

/** The refusal of `action` — the exact text the server answers. */
export function stationActionRefusal(action: ScanLedStationAction): string {
  return `Scan Stations are not allowed to ${ACTION_TEXT[action]}. An administrator can grant it to the role applied at Scan Stations. Nothing was recorded.`;
}
