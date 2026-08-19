// Shared application view keys: the ten approved GUI views addressed
// by the router. Production-safe (types only) — the real views live in
// real-views.ts, the development-only mock views in dev-views.ts.

import type { ManagementSubview } from './router-core';

export type AppViewKey =
  'scan-station' | 'production-board' | 'administration' | ManagementSubview;
