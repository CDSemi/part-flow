import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';

beforeEach(() => {
  window.history.replaceState({}, '', '/scan-station');
  document.body.className = '';
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
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
