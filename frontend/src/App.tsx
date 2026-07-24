import './styles/tokens.css';
import './styles/global.css';
import './app/shell.css';

import { ConnectivityProvider, useConnectivity } from './app/connectivity';
import { NotFoundView } from './app/NotFoundView';
import { Link, RouterProvider, useRouter } from './app/router';
import type { ManagementSubview, Route } from './app/router';
import { ThemeProvider, useTheme } from './app/theme';
import { AdministrationView } from './views/administration/AdministrationView';
import { AreaBoardView } from './views/area-board/AreaBoardView';
import { PriorityView } from './views/priority/PriorityView';
import { ProductionBoardView } from './views/production-board/ProductionBoardView';
import { PurchaseOrdersView } from './views/purchase-orders/PurchaseOrdersView';
import { ScanStationView } from './views/scan-station/ScanStationView';
import { TrackingView } from './views/tracking/TrackingView';

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
  { subview: 'tracking', label: 'Tracking' },
  { subview: 'purchase-orders', label: 'Purchase Orders' },
  { subview: 'priority', label: 'Priority' },
];

function ConnectivityChip() {
  const { status } = useConnectivity();
  const text =
    status === 'connected'
      ? 'ONLINE'
      : status === 'connecting'
        ? 'CONNECTING…'
        : 'OFFLINE';
  return (
    <span
      className={`connchip ${status === 'unavailable' ? 'off' : status === 'connecting' ? 'connecting' : ''}`}
      role="status"
      aria-label={`Backend connection: ${text}`}
    >
      <span className="cdot" aria-hidden="true" />
      <span className="ctxt">{text}</span>
    </span>
  );
}

function OfflineBanner() {
  const { status, retry } = useConnectivity();
  if (status !== 'unavailable') return null;
  return (
    <div className="offbanner" role="alert">
      <span>
        ⚠ OFFLINE — the backend is unavailable. Production writes are blocked;
        nothing is recorded or queued while disconnected. Already loaded
        read-only information stays visible.
      </span>
      <button className="retry" onClick={retry}>
        Retry connection
      </button>
    </div>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      className="navbtn"
      onClick={toggleTheme}
      aria-pressed={theme === 'light'}
      title="Switch between Dark and Light mode — every view follows"
    >
      {theme === 'dark' ? '🌙 Dark' : '☀️ Light'}
    </button>
  );
}

function ViewForRoute({ route }: { route: Route }) {
  switch (route.view) {
    case 'scan-station':
      return <ScanStationView />;
    case 'production-board':
      return <ProductionBoardView />;
    case 'administration':
      return <AdministrationView />;
    case 'management':
      switch (route.subview) {
        case 'area-board':
          return <AreaBoardView />;
        case 'tracking':
          return <TrackingView />;
        case 'purchase-orders':
          return <PurchaseOrdersView />;
        case 'priority':
          return <PriorityView />;
      }
      break;
    case 'not-found':
      return <NotFoundView path={route.path} />;
  }
  return null;
}

function AppShell() {
  const { route } = useRouter();
  return (
    <>
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
        <span className="mock-tag">
          Phase 2 · <b>mock data</b>
        </span>
      </nav>
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
        <ViewForRoute route={route} />
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
