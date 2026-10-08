import './styles/tokens.css';
import './styles/global.css';
import './app/shell.css';

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { useConnectivity } from './app/connectivity-context';
import { ConnectivityProvider } from './app/connectivity-provider';
import { AccountChip } from './components/AccountChip';
import { ConnectivityChip } from './components/ConnectivityChip';
import { REAL_VIEWS } from './app/real-views';
import type { AppViewKey } from './app/view-keys';
import { Link } from './app/link';
import { NotFoundView } from './app/NotFoundView';
import { useRouter } from './app/router-context';
import {
  canReadManagementView,
  MANAGEMENT_READ_ACCESS,
  readableManagementSubviews,
} from './app/management-access';
import { isChromeHidden, MANAGEMENT_NAV_ORDER } from './app/router-core';
import type { ManagementSubview, Route } from './app/router-core';
import { ReleaseNotice } from './app/ReleaseNotice';
import { RouterProvider } from './app/router-provider';
import { useSession } from './app/session-context';
import { SessionProvider } from './app/session-provider';
import { SignInGate } from './app/SignInGate';
import { ViewErrorBoundary } from './app/ViewErrorBoundary';
import { ThemeProvider } from './app/theme-provider';
import { UserThemeBinding } from './app/user-theme-binding';
import { ThemeToggle } from './components/ThemeToggle';
import { LoadingState } from './components/view-states';
import { PERMISSION_LABELS } from './views/administration/permissions';
import { useHotHistoryOwnerReset } from './views/priority/hot-history';
import { usePriorityFocusReset } from './views/priority/priority-focus';

const TOP_NAV: {
  to: string;
  label: string;
  matches: (route: Route) => boolean;
}[] = [
  {
    to: '/scan-station',
    label: 'Scan Station',
    matches: (r) => r.view === 'scan-station',
  },
  {
    to: '/production-board',
    label: 'Production Board',
    matches: (r) => r.view === 'production-board',
  },
  {
    to: '/management',
    label: 'Management',
    matches: (r) => r.view === 'management',
  },
  {
    to: '/administration',
    label: 'Administration',
    matches: (r) => r.view === 'administration',
  },
];

const MANAGEMENT_LABELS: Record<ManagementSubview, string> = {
  'area-board': 'Area Board',
  'work-orders': 'Work Orders',
  tracking: 'PN Tracking',
  priority: 'Priority',
  'planned-routes': 'Planned Routes',
  'part-numbers': 'Part Numbers',
  machines: 'Machines',
};

// Sub-view order: Part Numbers sits next to last, Machines last —
// directly after it (GUI_DESIGN §1.1; router-core owns the order).
const MANAGEMENT_NAV: { subview: ManagementSubview; label: string }[] =
  MANAGEMENT_NAV_ORDER.map((subview) => ({
    subview,
    label: MANAGEMENT_LABELS[subview],
  }));

function OfflineBanner() {
  const { status, retry } = useConnectivity();
  if (status !== 'unavailable') return null;
  // Two explicit regions — the same division as the Scan Station Undo
  // block: the message region fills the remaining space; the Retry
  // ACTION RAIL is the banner's complete right edge (the button
  // itself), divided by its own inset vertical rule — no separator
  // element. No extra explanatory sentences (the write-blocked
  // behavior itself is the explanation on every surface).
  return (
    <div className="offbanner" role="alert">
      <span className="msg">
        ⚠ OFFLINE — Connection to the PartFlow server has been lost. Production
        actions are disabled
      </span>
      <button className="retry zone-action" onClick={retry}>
        Retry connection
      </button>
    </div>
  );
}

/** The view key the view boundary logs for `route`. */
function viewKeyOf(route: Route): string {
  if (route.view === 'not-found') return 'not-found';
  return route.view === 'management' ? route.subview : route.view;
}

function ViewForRoute({ route }: { route: Route }) {
  if (route.view === 'not-found') {
    return <NotFoundView path={route.path} />;
  }
  const key: AppViewKey =
    route.view === 'management' ? route.subview : route.view;
  // Every approved view is a real view (real-views.ts) that ships in
  // every build.
  const RealView = REAL_VIEWS[key];
  return <RealView />;
}

/**
 * What a signed-in user who may not open a Management sub view sees in
 * its place (Phase 14 slice 3): the permissions that open it. No request
 * of the sub view is sent.
 */
function ManagementAccessPanel({ subview }: { subview: ManagementSubview }) {
  const labels = MANAGEMENT_READ_ACCESS[subview].map(
    (key) => PERMISSION_LABELS[key],
  );
  const title = MANAGEMENT_LABELS[subview];
  return (
    <section className="mgmt-access" aria-label={title}>
      <h1 tabIndex={-1}>{title}</h1>
      <p>
        {labels.length === 1
          ? `Your account cannot open ${title}. Opening it needs the ${labels[0]} permission.`
          : `Your account cannot open ${title}. Opening it needs one of these permissions: ${labels.join(', ')}.`}
      </p>
      <p>Ask an administrator if you need access.</p>
    </section>
  );
}

/** A Management sub view, or its access panel (inside the sign-in gate,
 * so `can()` is the gated area's owner's). */
function ManagementSubviewContent({
  route,
}: {
  route: Extract<Route, { view: 'management' }>;
}) {
  const { can } = useSession();
  if (!canReadManagementView(can, route.subview)) {
    return <ManagementAccessPanel subview={route.subview} />;
  }
  return <ViewForRoute route={route} />;
}

function AppShell() {
  const { route, path, setManagementReadable } = useRouter();
  const session = useSession();
  useHotHistoryOwnerReset();
  usePriorityFocusReset();
  // The Management sub views the signed-in user may open; null while
  // nobody is known to be signed in (or a new password is still to be
  // chosen) — navigation is never authorization, so then all are listed.
  const signedIn =
    session.status === 'signed-in' && session.user?.mustChangePassword !== true;
  const { can } = session;
  const readable = useMemo(
    () => (signedIn ? readableManagementSubviews(can) : null),
    [signedIn, can],
  );
  useEffect(() => {
    setManagementReadable(readable);
  }, [readable, setManagementReadable]);
  // Phone-width top navigation (GUI_DESIGN §2.5): the nav links live
  // behind an explicit menu button and open as a vertical panel. The
  // state exists at every width — CSS decides whether the button is
  // visible and whether the links render inline (desktop) or as the
  // panel (phone), so the DOM and accessibility tree stay identical.
  const [menuOpen, setMenuOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const mgmtNavRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  // After a sign-in from the Management panel, focus moves to the active
  // sub-view link — or the access panel heading — once the entry
  // redirect that sign-in may cause has settled.
  const [focusRequest, setFocusRequest] = useState(0);
  const focusHandled = useRef(0);
  const requestManagementFocus = useCallback(
    () => setFocusRequest((count) => count + 1),
    [],
  );
  useEffect(() => {
    if (focusRequest === focusHandled.current) return;
    // The router is still moving to the entry's new landing.
    if (window.location.pathname !== path) return;
    focusHandled.current = focusRequest;
    const target =
      mgmtNavRef.current?.querySelector<HTMLElement>('[aria-current="page"]') ??
      mainRef.current?.querySelector<HTMLElement>('.mgmt-access h1');
    target?.focus();
  }, [focusRequest, path, readable]);
  // Scroll-direction condensing for the sticky sub-nav: scrolling
  // down condenses the bar, the first upward scroll (or being near
  // the top) restores it.
  const [subnavShrunk, setSubnavShrunk] = useState(false);

  // Any completed navigation closes the panel — the user is done with
  // the menu; Escape and a click/tap outside the navigation close it
  // without navigating.
  useEffect(() => {
    setMenuOpen(false);
  }, [route]);
  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setMenuOpen(false);
    }
    function onClick(event: MouseEvent) {
      const nav = navRef.current;
      if (nav && event.target instanceof Node && !nav.contains(event.target)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('click', onClick);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('click', onClick);
    };
  }, [menuOpen]);

  // Swipeable Management sub-nav (GUI_DESIGN §2.5): on phone widths
  // the row pans horizontally with a hidden scrollbar, so the active
  // sub view must be brought into view itself — scrollIntoView is a
  // no-op wherever the row already fits (and absent in jsdom).
  useEffect(() => {
    if (route.view !== 'management') return;
    const active = mgmtNavRef.current?.querySelector('[aria-current="page"]');
    active?.scrollIntoView?.({ inline: 'nearest', block: 'nearest' });
  }, [route]);

  // Condense the sticky sub-nav by scroll DIRECTION inside <main>
  // (the application's scroll container): downward movement condenses
  // the bar, any upward movement — or being near the top — restores
  // its full height. A small delta threshold absorbs sub-pixel
  // scroll jitter; every navigation starts expanded.
  useEffect(() => {
    setSubnavShrunk(false);
    if (route.view !== 'management') return;
    const main = mainRef.current;
    if (!main) return;
    let last = main.scrollTop;
    function onScroll() {
      const y = main!.scrollTop;
      // Asymmetric thresholds: condensing reacts quickly (+4), while
      // restoring requires a deliberate upward scroll (−30) — larger
      // than the bar's own height change, so the scrollTop clamp that
      // can fire when the bar shrinks near the bottom edge never
      // reads as an upward scroll (scroll anchoring itself is
      // disabled on <main>).
      if (y <= 8) setSubnavShrunk(false);
      else if (y > last + 4) setSubnavShrunk(true);
      else if (y < last - 30) setSubnavShrunk(false);
      last = y;
    }
    main.addEventListener('scroll', onScroll, { passive: true });
    return () => main.removeEventListener('scroll', onScroll);
  }, [route]);

  // Scan Station production mode and Production Board kiosk mode hide
  // the top application navigation (router-core). The persistent
  // Offline banner is NOT navigation and stays.
  const chromeHidden = isChromeHidden(route);
  return (
    <>
      {chromeHidden ? null : (
        <nav className="appnav" aria-label="Primary" ref={navRef}>
          <span className="logo">
            <span className="mark" aria-hidden="true">
              ⇄
            </span>
            Part<span className="pf">Flow</span>
          </span>
          <button
            className="menubtn"
            aria-label="Menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            ☰
          </button>
          <div className={`appnav-links${menuOpen ? ' open' : ''}`}>
            {TOP_NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={`navbtn ${item.matches(route) ? 'active' : ''}`}
                aria-current={item.matches(route) ? 'page' : undefined}
              >
                {item.label}
              </Link>
            ))}
          </div>
          <span className="spacer" />
          {import.meta.env.DEV ? (
            <span className="mock-tag">
              Development preview · <b>sample data</b>
            </span>
          ) : null}
          <AccountChip />
          <ThemeToggle />
          <ConnectivityChip />
        </nav>
      )}
      <OfflineBanner />
      <ReleaseNotice />
      <main ref={mainRef}>
        {/* The sub-nav lives INSIDE the scrolling content area and
            sticks to its top, so Management view content actually
            passes beneath it — the frosted-glass surface has real
            content to blur (a sibling of <main> never overlaps the
            scrolled content). The persistent Offline banner stays
            outside the scroller above it. */}
        {route.view === 'management' && (
          <nav
            className={`mgmtnav${subnavShrunk ? ' shrunk' : ''}`}
            aria-label="Management sub views"
            ref={mgmtNavRef}
          >
            <span className="subgrp">Management</span>
            {MANAGEMENT_NAV.filter(
              (item) => readable === null || readable.has(item.subview),
            ).map((item) => (
              <Link
                key={item.subview}
                to={`/management/${item.subview}`}
                className={`subbtn ${route.subview === item.subview ? 'active' : ''}`}
                aria-current={
                  route.subview === item.subview ? 'page' : undefined
                }
              >
                {item.label}
              </Link>
            ))}
          </nav>
        )}
        {/* One boundary for every view: a view crash or a failed view
            chunk load renders inside <main> while the shell stays
            interactive (navigation, banners, chip). */}
        <ViewErrorBoundary route={path} viewKey={viewKeyOf(route)}>
          <Suspense fallback={<LoadingState label="Loading view" />}>
            {route.view === 'management' ? (
              <SignInGate area="Management" onSignedIn={requestManagementFocus}>
                <ManagementSubviewContent route={route} />
              </SignInGate>
            ) : (
              <ViewForRoute route={route} />
            )}
          </Suspense>
        </ViewErrorBoundary>
      </main>
    </>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <ConnectivityProvider>
        <RouterProvider>
          <SessionProvider>
            <AppShell />
            <UserThemeBinding />
          </SessionProvider>
        </RouterProvider>
      </ConnectivityProvider>
    </ThemeProvider>
  );
}
