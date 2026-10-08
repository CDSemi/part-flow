// Automatic reload of an outdated page (GUI_DESIGN §3 rule 13,
// OD-16-07): only on the unattended Scan Station and Production Board
// routes, once the page has been outdated for 60 s, NEVER while any
// dialog is open, at most once per 10 minutes per browser tab, and only
// when the server already serves the shell of the release it reports.

import type { Route } from './router-core';

export const RELEASE_AUTO_RELOAD_DELAY_MS = 60_000;
export const RELEASE_AUTO_RELOAD_MIN_INTERVAL_MS = 600_000;
export const RELEASE_AUTO_RELOAD_STORAGE_KEY =
  'partflow.release-auto-reload-at';

const OPEN_DIALOG_SELECTOR =
  '[role="dialog"], [role="alertdialog"], dialog[open]';

/** Whether no dialog of any kind is open in `doc`. */
export function noDialogOpen(doc: Document): boolean {
  return doc.querySelector(OPEN_DIALOG_SELECTOR) === null;
}

/** The route families that may reload themselves (kiosk-style use). */
export function autoReloadRoute(route: Route): boolean {
  return route.view === 'scan-station' || route.view === 'production-board';
}

/**
 * Whether the per-tab marker allows an automatic reload at `now`. A
 * storage that cannot be read never allows one.
 */
export function autoReloadAllowed(now: number): boolean {
  try {
    const stored = window.sessionStorage.getItem(
      RELEASE_AUTO_RELOAD_STORAGE_KEY,
    );
    if (stored === null) return true;
    const at = Number(stored);
    return (
      !Number.isFinite(at) || now - at >= RELEASE_AUTO_RELOAD_MIN_INTERVAL_MS
    );
  } catch {
    return false;
  }
}

/** Write the per-tab marker; false when the storage refused it. */
export function markAutoReload(now: number): boolean {
  try {
    window.sessionStorage.setItem(RELEASE_AUTO_RELOAD_STORAGE_KEY, String(now));
    return true;
  } catch {
    return false;
  }
}

/** The `partflow-release` meta content of a served shell, if any. */
export function shellRelease(html: string): string | null {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return (
    doc
      .querySelector('meta[name="partflow-release"]')
      ?.getAttribute('content') ?? null
  );
}

/**
 * Whether the server serves the shell of `release` right now: `GET /`
 * (never from the cache) answered 200 with that `partflow-release` meta.
 * Any failure reads false.
 */
export async function servedShellIs(release: string): Promise<boolean> {
  try {
    const response = await fetch('/', { cache: 'no-store' });
    if (response.status !== 200) return false;
    return shellRelease(await response.text()) === release;
  } catch {
    return false;
  }
}
