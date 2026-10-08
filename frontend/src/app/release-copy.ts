// Exact copy of the release-mismatch (outdated) state — GUI_DESIGN §3
// rule 13. The page is `outdated` when the server answers but runs
// another release than this bundle: writes are blocked exactly as while
// disconnected, and every write-blocked reason that names the
// connection reads the outdated alternative instead.

import type { ConnectivityStatus } from './connectivity-context';

/** The persistent update notice (OFFLINE banner's place). */
export const RELEASE_NOTICE_MESSAGE =
  '⚠ UPDATED — PartFlow was updated on the server. Reload this page to continue. Production actions are disabled';

/** The manual reload control of the notice and the non-dismissable modals. */
export const RELOAD_PAGE_LABEL = 'Reload page';

/** Scan inputs (main input, badge gate, Worker sign-in) while outdated. */
export const OUTDATED_SCAN_PLACEHOLDER =
  'PartFlow was updated — reload to continue scanning';

/** Scan Station blocked-scan notice while outdated. */
export const OUTDATED_BLOCKED_TITLE =
  'PartFlow was updated — scanning is paused';
export const OUTDATED_BLOCKED_DETAIL =
  'Reload this page before continuing. No scans or production updates are recorded until then.';

/** A Scan Station write that was not sent because the page is outdated. */
export function outdatedNotSent(what: string): string {
  return `PartFlow was updated — the ${what} was not sent and nothing was recorded. Reload the page to continue.`;
}

/** A Scan Station dialog's blocked line while outdated. */
export function outdatedCannotRecord(what: string): string {
  return `PartFlow was updated — the ${what} cannot be recorded until this page is reloaded.`;
}

/** Station enrollment reason while outdated. */
export const OUTDATED_ENROLL_REASON =
  'PartFlow was updated — reload this page to enroll the device.';

/** Disabled reasons outside the Scan Station that name the connection. */
export const OUTDATED_REASON =
  'PartFlow was updated — reload the page to continue.';

/**
 * The disabled reason for `status`: `OUTDATED_REASON` while outdated,
 * the site's existing connection reason otherwise.
 */
export function connectionReason(
  status: ConnectivityStatus,
  existing: string,
): string {
  return status === 'outdated' ? OUTDATED_REASON : existing;
}
