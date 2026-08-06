import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

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
