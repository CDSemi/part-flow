import { useEffect, useRef } from 'react';

import { useConnectivity } from './connectivity-context';
import { RELEASE_NOTICE_MESSAGE, RELOAD_PAGE_LABEL } from './release-copy';
import {
  autoReloadAllowed,
  autoReloadRoute,
  markAutoReload,
  noDialogOpen,
  RELEASE_AUTO_RELOAD_DELAY_MS,
  servedShellIs,
} from './release-reload';
import { useRouter } from './router-context';

const AUTO_RELOAD_TICK_MS = 1000;

/**
 * The persistent update notice (GUI_DESIGN §3 rule 13): PartFlow was
 * updated on the server while this page was open. It shows in the
 * OFFLINE banner's place with the same two regions and action rail
 * (the OFFLINE banner shows instead while disconnected), and never
 * takes focus. `Reload page` reloads at once — Work Orders' leave guard
 * still asks first.
 *
 * On the Scan Station and Production Board routes (unattended stations
 * and wall displays, whose writes all happen in dialogs) the page also
 * reloads itself once it has been outdated for 60 s, only while no
 * dialog is open (OD-16-07), at most once per 10 minutes per tab, and
 * only when the server already serves the shell of the release its
 * health answer reports.
 */
export function ReleaseNotice() {
  const { status, serverRelease } = useConnectivity();
  const { route } = useRouter();
  const autoRoute = autoReloadRoute(route);
  // When the current outdated period began (null while not outdated).
  const outdatedSince = useRef<number | null>(null);
  const serverReleaseRef = useRef(serverRelease ?? null);
  serverReleaseRef.current = serverRelease ?? null;

  useEffect(() => {
    if (status !== 'outdated') {
      outdatedSince.current = null;
      return undefined;
    }
    if (outdatedSince.current === null) outdatedSince.current = Date.now();
    if (!autoRoute) return undefined;
    let cancelled = false;
    let attempting = false;
    const intervalId = setInterval(() => {
      const since = outdatedSince.current;
      const release = serverReleaseRef.current;
      const now = Date.now();
      if (
        attempting ||
        since === null ||
        release === null ||
        now - since < RELEASE_AUTO_RELOAD_DELAY_MS ||
        !noDialogOpen(document) ||
        !autoReloadAllowed(now)
      ) {
        return;
      }
      attempting = true;
      void servedShellIs(release).then((served) => {
        attempting = false;
        // A dialog may have opened while the shell was checked.
        if (cancelled || !served || !noDialogOpen(document)) return;
        const at = Date.now();
        if (!autoReloadAllowed(at) || !markAutoReload(at)) return;
        window.location.reload();
      });
    }, AUTO_RELOAD_TICK_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [status, autoRoute]);

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
