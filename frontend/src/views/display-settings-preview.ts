// Development-only display settings of the `?state=` previews (the
// Production Board and Area Board previews and the Scan Station mock
// view).
//
// A preview renders without any request, so it cannot read the
// Department's rotation timing or the Due Soon warning policy from the
// server. These values stand in for them ONLY there; they mirror the
// canonical initial values so a preview looks like a fresh
// installation. They are previews, never defaults: the real views read
// the server's values in every build and have no fallback.
//
// `import.meta.env.DEV` is replaced statically by Vite, so both values
// are `null` in a production build and never ship (verified by
// src/production-boundary.test.ts). This module and `src/mocks/` are the
// only non-test sources allowed to hold policy or timing literals.

import type { DueSoonPolicy as Policy } from './dates';
import type { BoardRotationTiming as Rotation } from './production-board/board-logic';

export const PREVIEW_BOARD_ROTATION: Rotation | null = import.meta.env.DEV
  ? { secondsPerRow: 3, minPageSeconds: 6 }
  : null;

export const PREVIEW_DUE_SOON_POLICY: Policy | null = import.meta.env.DEV
  ? { minDays: 2, leadTimePercent: 15, maxDays: 7 }
  : null;
