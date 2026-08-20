// Real production views (Phase 3.5 + Phase 4).
//
// The views listed here read and write real server state through the
// /api surface and ship in EVERY build — development and production
// alike. They must never import from src/mocks/ (verified by
// src/production-boundary.test.ts); their development-only extras
// (state previews, the Worker sessions policy preview) sit behind
// their own `import.meta.env.DEV` boundaries inside the modules.
//
// Every other view remains a development-only mock view in
// dev-views.ts until its backend slice exists.

import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

import type { AppViewKey } from './view-keys';

export const REAL_VIEWS: Partial<
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
};
