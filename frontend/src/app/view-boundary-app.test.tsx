import { cleanup, render, screen, within } from '@testing-library/react';
import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';
import type { AppViewKey } from './view-keys';

// The shell mounts ONE view boundary around its lazy views (Phase 16
// slice 3): a crashing view and a view whose code chunk cannot be
// loaded both render inside <main> while the navigation stays. The
// registry is replaced here so one view throws and one view's import
// rejects the way a browser reports a chunk the server no longer has.

vi.mock('./real-views', () => {
  function Crashing(): never {
    throw new Error('boom — view exploded');
  }
  function Plain() {
    return <p>plain view</p>;
  }
  const plain = lazy(() => Promise.resolve({ default: Plain }));
  const views: Record<AppViewKey, LazyExoticComponent<ComponentType>> = {
    'scan-station': plain,
    'production-board': lazy(() =>
      Promise.reject(
        new TypeError(
          'Failed to fetch dynamically imported module: /assets/ProductionBoardView-x.js',
        ),
      ),
    ),
    administration: lazy(() => Promise.resolve({ default: Crashing })),
    'area-board': plain,
    'work-orders': plain,
    tracking: plain,
    priority: plain,
    'planned-routes': plain,
    'part-numbers': plain,
    machines: plain,
  };
  return { REAL_VIEWS: views };
});

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const body =
        url === '/api/session'
          ? { user: null, setup_open: false }
          : { status: 'ok' };
      return Promise.resolve(new Response(JSON.stringify(body)));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});

test('FR-22: a crashing view renders the boundary inside <main>; the navigation stays', async () => {
  window.history.replaceState({}, '', '/administration');
  render(<App />);
  const main = document.querySelector('main')!;
  const alert = await within(main).findByRole('alert');
  expect(alert).toHaveTextContent(
    'This view ran into an error and could not be displayed.',
  );
  expect(within(alert).getByRole('button', { name: 'Retry' })).toBeVisible();
  expect(
    screen.getByRole('navigation', { name: 'Primary' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Production Board' }),
  ).toBeInTheDocument();
});

test('FR-23: a view whose chunk cannot load offers Reload page, not Retry', async () => {
  window.history.replaceState({}, '', '/production-board');
  render(<App />);
  const main = document.querySelector('main')!;
  const alert = await within(main).findByRole('alert');
  expect(alert).toHaveTextContent(
    'This view could not be loaded — PartFlow may have been updated.',
  );
  expect(
    within(alert).getByRole('button', { name: 'Reload page' }),
  ).toBeInTheDocument();
  expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull();
  expect(
    screen.getByRole('navigation', { name: 'Primary' }),
  ).toBeInTheDocument();
});
