import { Suspense, lazy } from 'react';

// Development-only demo badges of the Worker sign-in modal and the
// badge-confirmation gate: the ONE place that reaches
// scan-station-dev-badges.tsx, through the lazy import behind
// `import.meta.env.DEV`, so production builds drop the module from the
// graph (verified by src/production-boundary.test.ts).
const DevBadges = import.meta.env.DEV
  ? lazy(() =>
      import('./scan-station-dev-badges').then((module) => ({
        default: module.DevBadges,
      })),
    )
  : null;

/**
 * The demo badges in development builds; nothing in production. A click
 * is handed to `onScan`, which runs the owner's wedge-scan submit path;
 * it is inert while `disabled` (disconnected, or a check in flight).
 */
export function DevBadgesSlot({
  onScan,
  disabled,
}: {
  onScan: (badge: string) => void;
  disabled: boolean;
}) {
  if (!DevBadges) return null;
  return (
    <Suspense fallback={null}>
      <DevBadges
        onScan={(badge) => {
          if (!disabled) onScan(badge);
        }}
      />
    </Suspense>
  );
}
