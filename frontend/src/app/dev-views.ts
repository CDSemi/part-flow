// Development-only mock view boundary.
//
// The seven approved GUI views are Phase 2 mock views: they render
// development-only sample data from src/mocks/ and mock interactions.
// They are reachable ONLY through this registry, and the registry is
// populated ONLY when `import.meta.env.DEV` is true. Vite replaces
// `import.meta.env.DEV` statically, so in a production build the
// conditional below is dead code: the dynamic imports — and with them
// every mock view and mock dataset — are dropped from the module graph
// and no mock chunk is emitted. `scripts/check-production-boundary.mjs`
// verifies this against the built assets with known mock sentinel
// values as part of `npm run build`.
//
// Production builds keep the application shell (routes, navigation,
// themes, connectivity) and show an explicit not-connected state per
// route instead (see UnconnectedView).

import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

import type { ManagementSubview } from './router-core';

export type AppViewKey =
  'scan-station' | 'production-board' | 'administration' | ManagementSubview;

type ViewRegistry = Readonly<
  Record<AppViewKey, LazyExoticComponent<ComponentType>>
>;

export const DEV_MOCK_VIEWS: ViewRegistry | null = import.meta.env.DEV
  ? {
      'scan-station': lazy(() =>
        import('../views/scan-station/ScanStationView').then((m) => ({
          default: m.ScanStationView,
        })),
      ),
      'production-board': lazy(() =>
        import('../views/production-board/ProductionBoardView').then((m) => ({
          default: m.ProductionBoardView,
        })),
      ),
      'area-board': lazy(() =>
        import('../views/area-board/AreaBoardView').then((m) => ({
          default: m.AreaBoardView,
        })),
      ),
      tracking: lazy(() =>
        import('../views/tracking/TrackingView').then((m) => ({
          default: m.TrackingView,
        })),
      ),
      'work-orders': lazy(() =>
        import('../views/work-orders/WorkOrdersView').then((m) => ({
          default: m.WorkOrdersView,
        })),
      ),
      priority: lazy(() =>
        import('../views/priority/PriorityView').then((m) => ({
          default: m.PriorityView,
        })),
      ),
      administration: lazy(() =>
        import('../views/administration/AdministrationView').then((m) => ({
          default: m.AdministrationView,
        })),
      ),
    }
  : null;
