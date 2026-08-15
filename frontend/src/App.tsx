import './styles/tokens.css';
import './styles/global.css';
import './app/shell.css';

import { Suspense, useEffect, useRef, useState } from 'react';

import { useConnectivity } from './app/connectivity-context';
import { ConnectivityProvider } from './app/connectivity-provider';
import { ConnectivityChip } from './components/ConnectivityChip';
import { DEV_MOCK_VIEWS } from './app/dev-views';
import type { AppViewKey } from './app/dev-views';
import { Link } from './app/link';
import { NotFoundView } from './app/NotFoundView';
import { useRouter } from './app/router-context';
import type { ManagementSubview, Route } from './app/router-core';
import { RouterProvider } from './app/router-provider';
import { ThemeProvider } from './app/theme-provider';
import { ThemeToggle } from './components/ThemeToggle';
import { UnconnectedView } from './app/UnconnectedView';
import { LoadingState } from './components/view-states';

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

// Sub-view order: Part Numbers sits next to last, Machines last —
// directly after it (GUI_DESIGN §1.1).
const MANAGEMENT_NAV: { subview: ManagementSubview; label: string }[] = [
  { subview: 'area-board', label: 'Area Board' },
  { subview: 'work-orders', label: 'Work Orders' },
  { subview: 'tracking', label: 'PN Tracking' },
  { subview: 'priority', label: 'Priority' },
  { subview: 'planned-routes', label: 'Planned Routes' },
  { subview: 'part-numbers', label: 'Part Numbers' },
  { subview: 'machines', label: 'Machines' },
];

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

const VIEW_TITLES: Record<AppViewKey, string> = {
  'scan-station': 'Scan Station',
  'production-board': 'Production Board',
  'area-board': 'Area Board',
  machines: 'Machines',
  tracking: 'PN Tracking',
  'work-orders': 'Work Orders',
  'planned-routes': 'Planned Routes',
  'part-numbers': 'Part Numbers',
  priority: 'Priority Management',
  administration: 'Administration',
};

function ViewForRoute({ route }: { route: Route }) {
  if (route.view === 'not-found') {
    return <NotFoundView path={route.path} />;
  }
  const key: AppViewKey =
    route.view === 'management' ? route.subview : route.view;
  // Mock views exist only in development builds (see dev-views.ts). A
  // production build renders the explicit not-connected state instead.
  const DevView = DEV_MOCK_VIEWS?.[key];
  if (!DevView) {
    return <UnconnectedView title={VIEW_TITLES[key]} />;
  }
  return <DevView />;
}

function AppShell() {
  const { route } = useRouter();
  // Phone-width top navigation (GUI_DESIGN §2.5): the nav links live
  // behind an explicit menu button and open as a vertical panel. The
  // state exists at every width — CSS decides whether the button is
  // visible and whether the links render inline (desktop) or as the
  // panel (phone), so the DOM and accessibility tree stay identical.
  const [menuOpen, setMenuOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const mgmtNavRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
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
  // the top application navigation: production mode keeps operators on
  // the configured station, kiosk mode keeps a wall display clean.
  // Both are presentation choices only — never an authorization or
  // security boundary. The persistent Offline banner is NOT navigation
  // and stays.
  const chromeHidden =
    (route.view === 'scan-station' &&
      route.stationId !== null &&
      route.mode === 'production') ||
    (route.view === 'production-board' && route.mode === 'kiosk');
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
          <ThemeToggle />
          <ConnectivityChip />
        </nav>
      )}
      <OfflineBanner />
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
            {MANAGEMENT_NAV.map((item) => (
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
        <Suspense fallback={<LoadingState label="Loading view" />}>
          <ViewForRoute route={route} />
        </Suspense>
      </main>
    </>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <ConnectivityProvider>
        <RouterProvider>
          <AppShell />
        </RouterProvider>
      </ConnectivityProvider>
    </ThemeProvider>
  );
}
