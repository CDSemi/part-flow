import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';

import {
  MOCK_BADGE_CONFIRM_POLICY,
  setBadgeConfirmRequirement,
} from '../../mocks/scan-station';
import { AdministrationView } from './AdministrationView';

// Administration → Worker sessions (post-v18): the sliding-timeout
// preview and the three badge-confirmation options for the sensitive
// Scan Station actions (PROJECT_PROFILE §19). The switches edit shared
// mock module state — restore the defaults after every test.

afterEach(() => {
  cleanup();
  setBadgeConfirmRequirement('done', true);
  setBadgeConfirmRequirement('queue', true);
  setBadgeConfirmRequirement('undo', true);
});

function openWorkerSessions() {
  render(<AdministrationView />);
  fireEvent.click(screen.getByRole('button', { name: 'Worker sessions' }));
}

test('Worker sessions shows the timeout values and the three badge-confirmation switches', () => {
  openWorkerSessions();

  // Timeout preview: the default plus the per-Area override.
  expect(screen.getByText('Default timeout')).toBeInTheDocument();
  expect(screen.getByText('15 minutes')).toBeInTheDocument();
  expect(screen.getByText('Lathe override')).toBeInTheDocument();
  expect(screen.getByText('20 minutes')).toBeInTheDocument();

  // Three slide switches — one per sensitive action, default ON.
  const switches = screen.getAllByRole('switch');
  expect(switches).toHaveLength(3);
  for (const sw of switches) {
    expect(sw.getAttribute('aria-checked')).toBe('true');
    expect(sw.textContent).toContain('On');
  }
  for (const name of [
    'Require badge scan — DONE — Complete Area processing',
    'Require badge scan — QUEUE — Return unfinished quantity to queue',
    'Require badge scan — UNDO — Reverse the last action',
  ]) {
    expect(screen.getByRole('switch', { name })).toBeInTheDocument();
  }
  // A settings panel, not an entry table — no entry action renders.
  expect(screen.queryByRole('button', { name: /New entry/ })).toBeNull();
});

test('toggling a badge-confirmation switch updates the shared policy per action', () => {
  openWorkerSessions();

  const undoSwitch = screen.getByRole('switch', {
    name: 'Require badge scan — UNDO — Reverse the last action',
  });
  fireEvent.click(undoSwitch);
  expect(undoSwitch.getAttribute('aria-checked')).toBe('false');
  expect(undoSwitch.textContent).toContain('Off');
  expect(MOCK_BADGE_CONFIRM_POLICY.undo).toBe(false);
  // The other actions stay independent.
  expect(MOCK_BADGE_CONFIRM_POLICY.done).toBe(true);
  expect(MOCK_BADGE_CONFIRM_POLICY.queue).toBe(true);

  fireEvent.click(undoSwitch);
  expect(undoSwitch.getAttribute('aria-checked')).toBe('true');
  expect(MOCK_BADGE_CONFIRM_POLICY.undo).toBe(true);
});
