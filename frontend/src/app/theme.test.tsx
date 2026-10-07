import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';
import { resolveTheme } from './theme-context';
import type { Theme } from './theme-context';

beforeEach(() => {
  window.history.replaceState({}, '', '/scan-station');
  document.body.className = '';
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            String(input) === '/api/session'
              ? { user: null, setup_open: false }
              : { status: 'ok' },
          ),
          { status: 200 },
        ),
      ),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('Dark is the default theme', () => {
  render(<App />);

  expect(document.body.classList.contains('dark')).toBe(true);
  expect(document.body.classList.contains('light')).toBe(false);
  expect(screen.getByRole('button', { name: '🌙 Dark' })).toBeInTheDocument();
});

test('the theme toggle switches the whole application between Dark and Light', () => {
  render(<App />);

  fireEvent.click(screen.getByRole('button', { name: '🌙 Dark' }));

  // The theme class lives on <body>, so navigation chrome, dialogs,
  // banners and view content all follow the selected mode.
  expect(document.body.classList.contains('light')).toBe(true);
  expect(document.body.classList.contains('dark')).toBe(false);
  expect(screen.getByRole('button', { name: '☀️ Light' })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '☀️ Light' }));

  expect(document.body.classList.contains('dark')).toBe(true);
  expect(document.body.classList.contains('light')).toBe(false);
});

test('the theme applies across views after navigation', () => {
  render(<App />);

  fireEvent.click(screen.getByRole('button', { name: '🌙 Dark' }));
  fireEvent.click(screen.getByRole('link', { name: 'Management' }));

  expect(window.location.pathname).toBe('/management/area-board');
  expect(document.body.classList.contains('light')).toBe(true);
});

test.each<[Theme | null, Theme | null, Theme]>([
  [null, null, 'dark'],
  [null, 'light', 'light'],
  [null, 'dark', 'dark'],
  ['dark', 'light', 'dark'],
  ['light', null, 'light'],
])(
  'resolveTheme(user %s, station %s) is %s (GUI_DESIGN §2.1 precedence)',
  (user, station, expected) => {
    expect(resolveTheme(user, station)).toBe(expected);
  },
);

test('the Kiosk toggle switches the theme for the session and saves nothing', async () => {
  window.history.replaceState({}, '', '/production-board/kiosk');
  render(<App />);

  fireEvent.click(await screen.findByRole('button', { name: '🌙 Dark' }));

  expect(document.body.classList.contains('light')).toBe(true);
  const calls = vi.mocked(fetch).mock.calls;
  expect(
    calls.filter(
      ([, init]) => init?.method !== undefined && init.method !== 'GET',
    ),
  ).toHaveLength(0);
});
