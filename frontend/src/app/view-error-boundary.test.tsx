import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { isChunkLoadError } from './chunk-load-error';
import { ViewErrorBoundary } from './ViewErrorBoundary';

// A runtime error inside a lazy-loaded view must not blank the page:
// the boundary renders the standard ErrorState (no raw stack trace)
// and logs the original error with its route and view key.

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Bomb({ defused }: { defused?: boolean }) {
  if (!defused) throw new Error('boom — view exploded');
  return <div data-testid="view-content">view content</div>;
}

test('a view crash renders the ErrorState instead of a blank page and logs route + view key + original error', () => {
  const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  render(
    <ViewErrorBoundary route="/production-board" viewKey="production-board">
      <Bomb />
    </ViewErrorBoundary>,
  );

  // Visible, non-blank error surface — without the raw stack trace.
  const alert = screen.getByRole('alert');
  expect(alert.textContent).toContain(
    'This view ran into an error and could not be displayed.',
  );
  expect(alert.textContent).not.toContain('boom — view exploded');
  expect(alert.textContent).not.toMatch(/at Bomb/);

  // The log carries the full context for diagnosis.
  const logged = errorSpy.mock.calls.find(
    (call) =>
      typeof call[0] === 'string' && call[0].includes('[PartFlow] View'),
  );
  expect(logged).toBeDefined();
  expect(logged?.[0]).toContain('production-board');
  expect(logged?.[0]).toContain('/production-board');
  expect(logged?.[1]).toMatchObject({
    route: '/production-board',
    viewKey: 'production-board',
  });
  expect((logged?.[1] as { error: Error }).error.message).toContain(
    'boom — view exploded',
  );
});

test('navigating to another route resets the boundary without a remount or retry', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const { rerender } = render(
    <ViewErrorBoundary route="/production-board" viewKey="production-board">
      <Bomb />
    </ViewErrorBoundary>,
  );
  expect(screen.getByRole('alert')).toBeInTheDocument();

  rerender(
    <ViewErrorBoundary route="/administration" viewKey="administration">
      <Bomb defused />
    </ViewErrorBoundary>,
  );
  expect(await screen.findByTestId('view-content')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('Retry re-renders the children after the failure cause is gone', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const { rerender } = render(
    <ViewErrorBoundary route="/administration" viewKey="administration">
      <Bomb />
    </ViewErrorBoundary>,
  );
  expect(screen.getByRole('alert')).toBeInTheDocument();

  // The underlying cause is fixed (e.g. transient chunk-load failure) …
  rerender(
    <ViewErrorBoundary route="/administration" viewKey="administration">
      <Bomb defused />
    </ViewErrorBoundary>,
  );
  // … but the boundary still shows the error until the user retries.
  expect(screen.queryByTestId('view-content')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByTestId('view-content')).toBeInTheDocument();
});

/* ============ Chunk-load recovery (Phase 16 slice 3) ============ */

function ChunkBomb(): never {
  throw new TypeError(
    'Failed to fetch dynamically imported module: /assets/x.js',
  );
}

test('FR-23: a failed view chunk load offers Reload page instead of Retry', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const reload = vi.fn();
  const locationDescriptor = Object.getOwnPropertyDescriptor(
    window,
    'location',
  )!;
  Object.defineProperty(window, 'location', {
    configurable: true,
    get: () => ({ reload, pathname: '/production-board' }),
  });
  try {
    render(
      <ViewErrorBoundary route="/production-board" viewKey="production-board">
        <ChunkBomb />
      </ViewErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(
      'This view could not be loaded — PartFlow may have been updated.',
    );
    expect(alert).toHaveTextContent(
      'Reload the page to continue. The rest of the application is still available.',
    );
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reload page' }));
    expect(reload).toHaveBeenCalledTimes(1);
  } finally {
    Object.defineProperty(window, 'location', locationDescriptor);
  }
});

test('FR-24: isChunkLoadError recognises the browsers’ chunk-load failures only', () => {
  for (const message of [
    'Failed to fetch dynamically imported module: /assets/x.js',
    'error loading dynamically imported module: /assets/x.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/x.css',
  ]) {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
  }
  const named = new Error('Loading chunk 7 failed.');
  named.name = 'ChunkLoadError';
  expect(isChunkLoadError(named)).toBe(true);
  expect(isChunkLoadError(new Error('boom'))).toBe(false);
  expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(
    false,
  );
});
