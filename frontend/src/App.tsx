import './styles/tokens.css';
import './styles/global.css';
import './app/shell.css';

import { Suspense } from 'react';

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
import { ViewErrorBoundary } from './app/ViewErrorBoundary';
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

const MANAGEMENT_NAV: { subview: ManagementSubview; label: string }[] = [
  { subview: 'area-board', label: 'Area Board' },
  { subview: 'machines', label: 'Machines' },
  { subview: 'tracking', label: 'Tracking' },
  { subview: 'work-orders', label: 'Work Orders' },
  { subview: 'planned-routes', label: 'Planned Routes' },
  { subview: 'priority', label: 'Priority' },
];

function OfflineBanner() {
  const { status, retry } = useConnectivity();
  if (status !== 'unavailable') return null;
  // One compact line: the exact offline statement, a standalone
  // separator, and a quiet text action — no button chrome, no extra
  // explanatory sentences (the write-blocked behavior itself is the
  // explanation on every surface).
  return (
    <div className="offbanner" role="alert">
      <span className="msg">
        ⚠ OFFLINE — Connection to the PartFlow server has been lost. Production
        actions are disabled
      </span>
      <span className="sep" aria-hidden="true" />
      <button className="retry" onClick={retry}>
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
  tracking: 'Tracking',
  'work-orders': 'Work Orders',
  'planned-routes': 'Planned Routes',
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

/** Resolved view key for logging/registry lookup — 'not-found' aside. */
function viewKeyForRoute(route: Route): string {
  if (route.view === 'not-found') return 'not-found';
  return route.view === 'management' ? route.subview : route.view;
}

function AppShell() {
  const { route, path } = useRouter();
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
        <nav className="appnav" aria-label="Primary">
          <span className="logo">
            <span className="mark" aria-hidden="true">
              ⇄
            </span>
            Part<span className="pf">Flow</span>
          </span>
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
          <span className="spacer" />
          <ConnectivityChip />
          <ThemeToggle />
          {import.meta.env.DEV ? (
            <span className="mock-tag">
              Development preview · <b>sample data</b>
            </span>
          ) : null}
        </nav>
      )}
      {route.view === 'management' && (
        <nav className="mgmtnav" aria-label="Management sub views">
          <span className="subgrp">Management</span>
          {MANAGEMENT_NAV.map((item) => (
            <Link
              key={item.subview}
              to={`/management/${item.subview}`}
              className={`subbtn ${route.subview === item.subview ? 'active' : ''}`}
              aria-current={route.subview === item.subview ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      )}
      <OfflineBanner />
      <main>
        {/* The boundary wraps Suspense so a failed lazy chunk load is
            caught the same way as a runtime error inside the view. No
            remount key: route changes reset the boundary in place and
            keep the view's own mounted state (see ViewErrorBoundary). */}
        <ViewErrorBoundary route={path} viewKey={viewKeyForRoute(route)}>
          <Suspense fallback={<LoadingState label="Loading view" />}>
            <ViewForRoute route={route} />
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
          <AppShell />
        </RouterProvider>
      </ConnectivityProvider>
    </ThemeProvider>
  );
}
