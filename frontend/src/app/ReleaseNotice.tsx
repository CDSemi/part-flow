import { useEffect, useRef, useState } from 'react';

import { useConnectivity } from './connectivity-context';
import {
  RELEASE_NOTICE_MESSAGE,
  RELOAD_PAGE_LABEL,
  reloadCountdownLabel,
} from './release-copy';
import {
  autoReloadAllowed,
  autoReloadDueAt,
  autoReloadRoute,
  markAutoReload,
  noDialogOpen,
  RELEASE_RELOAD_COUNTDOWN_MS,
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
 * The page also reloads itself after a 10 s countdown shown beside
 * `Reload page`. The countdown runs only while no dialog is open
 * (OD-16-07) and the per-tab limit allows a reload (at most once per 10
 * minutes), and starts over once nothing holds it back any more. At
 * zero the page reloads as soon as the server serves the shell of the
 * release its health answer reports (`Reloading…` until then). The Scan
 * Station and Production Board routes (unattended stations and wall
 * displays, whose writes all happen in dialogs) need nothing more.
 * Every other route also holds the countdown while a form on it reports
 * unsaved work (unsaved-work.ts), and reloads only once nobody has used
 * the page for 1 minute: the countdown shows the last 10 s of that
 * minute, and any input stops it at once.
 */
export function ReleaseNotice() {
  const { status, serverRelease } = useConnectivity();
  const { route } = useRouter();
  const unattended = autoReloadRoute(route);
  const unattendedRef = useRef(unattended);
  unattendedRef.current = unattended;
  const serverReleaseRef = useRef(serverRelease ?? null);
  serverReleaseRef.current = serverRelease ?? null;
  // When somebody last used the page (null until it has mounted).
  const lastInput = useRef<number | null>(null);
  // The countdown beside `Reload page`: the whole seconds left, 0 once
  // it has run out, null while it is not shown.
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  useEffect(() => {
    lastInput.current = Date.now();
    const used = () => {
      lastInput.current = Date.now();
      // Input stops the countdown of an attended page at once (while no
      // countdown shows, the unchanged state renders nothing).
      if (!unattendedRef.current) setSecondsLeft(null);
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
      setSecondsLeft(null);
      return undefined;
    }
    let cancelled = false;
    let attempting = false;
    // Set when the per-tab marker could not be written: without it the
    // limit cannot hold, so this outdated period never reloads itself.
    let markerRefused = false;
    // Since when nothing has held the reload back (null while something
    // does): the countdown runs from here.
    let clearSince: number | null = null;
    const heldBack = (now: number) =>
      markerRefused ||
      !noDialogOpen(document) ||
      (!unattendedRef.current && hasUnsavedWork()) ||
      !autoReloadAllowed(now);
    const dueAt = (from: number) =>
      autoReloadDueAt(from, lastInput.current, !unattendedRef.current);
    const tick = () => {
      const now = Date.now();
      const release = serverReleaseRef.current;
      if (release === null || heldBack(now)) {
        clearSince = null;
        setSecondsLeft(null);
        return;
      }
      clearSince ??= now;
      const left = dueAt(clearSince) - now;
      setSecondsLeft(
        left > RELEASE_RELOAD_COUNTDOWN_MS
          ? null
          : Math.max(0, Math.ceil(left / 1000)),
      );
      if (left > 0 || attempting) return;
      attempting = true;
      void servedShellIs(release).then((served) => {
        attempting = false;
        if (cancelled || !served) return;
        // Something may have held the reload back, or somebody may have
        // used the page, while the shell was checked.
        const at = Date.now();
        if (clearSince === null || heldBack(at) || dueAt(clearSince) > at) {
          return;
        }
        if (!markAutoReload(at)) {
          markerRefused = true;
          setSecondsLeft(null);
          return;
        }
        window.location.reload();
      });
    };
    tick();
    const intervalId = setInterval(tick, AUTO_RELOAD_TICK_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [status]);

  if (status !== 'outdated') return null;
  return (
    <div className="offbanner outdated" role="alert">
      <span className="msg">{RELEASE_NOTICE_MESSAGE}</span>
      {secondsLeft === null ? null : (
        // A timer is not read out as it ticks, so the alert around it is
        // not announced again every second.
        <span className="countdown" role="timer" aria-live="off">
          {reloadCountdownLabel(secondsLeft)}
        </span>
      )}
      <button
        className="retry zone-action"
        onClick={() => window.location.reload()}
      >
        {RELOAD_PAGE_LABEL}
      </button>
    </div>
  );
}
