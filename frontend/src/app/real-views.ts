// Real production views (Phase 3.5 + Phase 4 + Phase 5 + Phase 11 +
// Phase 12 + Phase 13) — every approved GUI view.
//
// The views listed here read and write real server state through the
// /api surface and ship in EVERY build — development and production
// alike. They must never import from src/mocks/ (verified by
// src/production-boundary.test.ts); their development-only extras
// (state previews, the Scan Station demo badges and mock preview) sit
// behind their own `import.meta.env.DEV` boundaries inside the modules.
// Phase 13 connected the last mock view (Management → Planned Routes),
// so no development-only view registry remains.

import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

import type { AppViewKey } from './view-keys';

export const REAL_VIEWS: Readonly<
  Record<AppViewKey, LazyExoticComponent<ComponentType>>
> = {
  machines: lazy(() =>
    import('../views/machines/MachinesView').then((m) => ({
      default: m.MachinesView,
    })),
  ),
  administration: lazy(() =>
    import('../views/administration/AdministrationView').then((m) => ({
      default: m.AdministrationView,
    })),
  ),
  'work-orders': lazy(() =>
    import('../views/work-orders/WorkOrdersView').then((m) => ({
      default: m.WorkOrdersView,
    })),
  ),
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
  priority: lazy(() =>
    import('../views/priority/PriorityView').then((m) => ({
      default: m.PriorityView,
    })),
  ),
  'part-numbers': lazy(() =>
    import('../views/part-numbers/PartNumbersView').then((m) => ({
      default: m.PartNumbersView,
    })),
  ),
  'planned-routes': lazy(() =>
    import('../views/planned-routes/PlannedRoutesView').then((m) => ({
      default: m.PlannedRoutesView,
    })),
  ),
};
