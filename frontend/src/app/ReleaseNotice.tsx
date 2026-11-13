import { useEffect, useRef } from 'react';

import { useConnectivity } from './connectivity-context';
import { RELEASE_NOTICE_MESSAGE, RELOAD_PAGE_LABEL } from './release-copy';
import {
  autoReloadAllowed,
  autoReloadRoute,
  markAutoReload,
  noDialogOpen,
  RELEASE_AUTO_RELOAD_DELAY_MS,
  RELEASE_IDLE_RELOAD_AFTER_MS,
  servedShellIs,
} from './release-reload';
import { useRouter } from './router-context';
import { hasUnsavedWork } from './unsaved-work';

const AUTO_RELOAD_TICK_MS = 1000;

/**
 * The input that counts as somebody using the page. A scroll event is
 * not one: the page also scrolls by itself (scroll anchoring under a
 * refreshed list, a view bringing its selection into view), and a
 * person scrolls with a wheel, a touch, a key or the pointer, which
 * count already.
 */
const INPUT_EVENTS = [
  'keydown',
  'pointerdown',
  'pointermove',
  'wheel',
  'touchstart',
] as const;

/**
 * The persistent update notice (GUI_DESIGN §3 rule 13): PartFlow was
 * updated on the server while this page was open. It shows in the
 * OFFLINE banner's place with the same two regions and action rail
 * (the OFFLINE banner shows instead while disconnected), and never
 * takes focus. `Reload page` reloads at once — Work Orders' leave guard
 * still asks first.
 *
 * The page also reloads itself once it has been outdated for 60 s, only
 * while no dialog is open (OD-16-07), at most once per 10 minutes per
 * tab, and only when the server already serves the shell of the release
 * its health answer reports. The Scan Station and Production Board
 * routes (unattended stations and wall displays, whose writes all
 * happen in dialogs) reload on those conditions alone. Every other
 * route also waits until nobody has used the page for 2 minutes and no
 * form on it reports unsaved work (unsaved-work.ts).
 */
export function ReleaseNotice() {
  const { status, serverRelease } = useConnectivity();
  const { route } = useRouter();
  const unattended = autoReloadRoute(route);
  const unattendedRef = useRef(unattended);
  unattendedRef.current = unattended;
  // When the current outdated period began (null while not outdated).
  const outdatedSince = useRef<number | null>(null);
  const serverReleaseRef = useRef(serverRelease ?? null);
  serverReleaseRef.current = serverRelease ?? null;
  // When somebody last used the page (null until it has mounted).
  const lastInput = useRef<number | null>(null);

  useEffect(() => {
    lastInput.current = Date.now();
    const used = () => {
      lastInput.current = Date.now();
    };
    for (const name of INPUT_EVENTS) {
      window.addEventListener(name, used, { capture: true, passive: true });
    }
    return () => {
      for (const name of INPUT_EVENTS) {
        window.removeEventListener(name, used, { capture: true });
      }
    };
  }, []);

  useEffect(() => {
    if (status !== 'outdated') {
      outdatedSince.current = null;
      return undefined;
    }
    if (outdatedSince.current === null) outdatedSince.current = Date.now();
    let cancelled = false;
    let attempting = false;
    // No dialog is open, and an attended page is idle with nothing unsaved.
    const mayReload = (now: number) =>
      noDialogOpen(document) &&
      (unattendedRef.current ||
        (lastInput.current !== null &&
          now - lastInput.current >= RELEASE_IDLE_RELOAD_AFTER_MS &&
          !hasUnsavedWork()));
    const intervalId = setInterval(() => {
      const since = outdatedSince.current;
      const release = serverReleaseRef.current;
      const now = Date.now();
      if (
        attempting ||
        since === null ||
        release === null ||
        now - since < RELEASE_AUTO_RELOAD_DELAY_MS ||
        !mayReload(now) ||
        !autoReloadAllowed(now)
      ) {
        return;
      }
      attempting = true;
      void servedShellIs(release).then((served) => {
        attempting = false;
        // A dialog may have opened, or somebody may have used the page,
        // while the shell was checked.
        if (cancelled || !served || !mayReload(Date.now())) return;
        const at = Date.now();
        if (!autoReloadAllowed(at) || !markAutoReload(at)) return;
        window.location.reload();
      });
    }, AUTO_RELOAD_TICK_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [status]);

  if (status !== 'outdated') return null;
  return (
    <div className="offbanner outdated" role="alert">
      <span className="msg">{RELEASE_NOTICE_MESSAGE}</span>
      <button
        className="retry zone-action"
        onClick={() => window.location.reload()}
      >
        {RELOAD_PAGE_LABEL}
      </button>
    </div>
  );
}
