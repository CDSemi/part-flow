import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';

import { RouterProvider } from './router-provider';
import { useRouter } from './router-context';
import type { RouterValue } from './router-context';
import type { ManagementSubview } from './router-core';

// The readable-aware bare '/management' entry of the router: a landing
// resolved before the readable sub views were known is corrected once
// they are known, and never again — a later change of the readable set
// (another sign-in, changed permissions) must not move a view that may
// hold unsaved work.

let router: RouterValue;

function Probe() {
  router = useRouter();
  return null;
}

function readable(...subviews: ManagementSubview[]) {
  return new Set<ManagementSubview>(subviews);
}

afterEach(() => {
  cleanup();
});

test('a changed readable set after the landing was corrected never moves a guarded view', () => {
  // Last used: PN Tracking.
  window.history.replaceState({}, '', '/management/tracking');
  render(
    <RouterProvider>
      <Probe />
    </RouterProvider>,
  );
  act(() => router.navigate('/'));
  // A bare entry while nobody is known to be signed in lands on the
  // last-used sub view.
  act(() => router.navigate('/management'));
  expect(window.location.pathname).toBe('/management/tracking');

  // The sign-in becomes known: the landing is corrected once.
  act(() => router.setManagementReadable(readable('work-orders')));
  expect(window.location.pathname).toBe('/management/work-orders');

  // The view now holds unsaved work; the sign-in ends and the same user
  // signs in again with View production data granted meanwhile.
  let asked = 0;
  router.setNavigationGuard(() => {
    asked += 1;
    return false;
  });
  act(() => router.setManagementReadable(null));
  act(() => router.setManagementReadable(readable('work-orders', 'tracking')));

  expect(window.location.pathname).toBe('/management/work-orders');
  expect(router.path).toBe('/management/work-orders');
  expect(asked).toBe(0);
});

test('a landing made while the readable set is known is never re-steered', () => {
  window.history.replaceState({}, '', '/management/tracking');
  render(
    <RouterProvider>
      <Probe />
    </RouterProvider>,
  );
  act(() => router.setManagementReadable(readable('work-orders')));
  act(() => router.navigate('/'));
  act(() => router.navigate('/management'));
  expect(window.location.pathname).toBe('/management/work-orders');

  act(() => router.setManagementReadable(readable('work-orders', 'tracking')));
  expect(window.location.pathname).toBe('/management/work-orders');
});
