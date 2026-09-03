// Development-only mock view boundary.
//
// The views listed here are Phase 2 mock views: they render
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
// The Phase 3.5 views (Management → Machines and Administration), the
// Phase 4 view (Management → Work Orders), the Phase 5 Scan Station and
// the Phase 11 Production Board are REAL views against the /api
// surface — they live in real-views.ts and ship in every build, so
// they are deliberately absent here. The
// mock Scan Station (the approved Phase 6+ workflows) survives as a
// development-only preview behind the real view's own DEV boundary.
//
// Production builds keep the application shell (routes, navigation,
// themes, connectivity) and the real views, and show an explicit
// not-connected state for every remaining route (see UnconnectedView).

import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

import type { AppViewKey } from './view-keys';

/** The view keys still served by development-only mock views. */
export type DevMockViewKey = Exclude<
  AppViewKey,
  | 'machines'
  | 'administration'
  | 'work-orders'
  | 'scan-station'
  | 'production-board'
>;

type ViewRegistry = Readonly<
  Record<DevMockViewKey, LazyExoticComponent<ComponentType>>
>;

export const DEV_MOCK_VIEWS: ViewRegistry | null = import.meta.env.DEV
  ? {
      'area-board': lazy(() =>
        import('../views/area-board/AreaBoardView').then((m) => ({
          default: m.AreaBoardView,
        })),
      ),
      'planned-routes': lazy(() =>
        import('../views/planned-routes/PlannedRoutesView').then((m) => ({
          default: m.PlannedRoutesView,
        })),
      ),
      'part-numbers': lazy(() =>
        import('../views/part-numbers/PartNumbersView').then((m) => ({
          default: m.PartNumbersView,
        })),
      ),
      tracking: lazy(() =>
        import('../views/tracking/TrackingView').then((m) => ({
          default: m.TrackingView,
        })),
      ),
      priority: lazy(() =>
        import('../views/priority/PriorityView').then((m) => ({
          default: m.PriorityView,
        })),
      ),
    }
  : null;
