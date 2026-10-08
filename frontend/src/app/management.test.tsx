import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';
import { apiRequest } from '../api/client';
import type { HotListEntry } from '../api/hot-list';
import { PERMISSIONS } from '../api/roles';
import type { Permission } from '../api/roles';
import { AREA_BOARD_REFRESH_MS } from '../views/area-board/area-board-feed';
import { clearHotHistory, recordChange } from '../views/priority/hot-history';
import {
  clearPriorityFocus,
  peekPriorityFocus,
  requestPriorityFocus,
} from '../views/priority/priority-focus';

// Management access (Phase 14 slice 3), through the real application
// shell, router and sign-in provider against a fake server: the
// Management sign-in gate (the shared gate Administration uses), the
// access panel of a sub view the user may not open, the sub-view bar
// listing only what the user may open, the readable-aware entry
// redirect, focus after a sign-in from the gate, and the Priority
// Undo/Redo history belonging to the signed-in user. The server checks
// every read and write itself; these tests cover what the UI renders
// and which requests it sends.

const A1 =
  'You are not signed in, or your sign-in has ended. Sign in to continue.';

/** The seeded Administrator's Management keys (no View production data,
 * no Create and edit Work Orders, no priority keys). */
const ADMINISTRATOR_KEYS: Permission[] = [
  'EDIT_WORK_ORDER_DEMAND',
  'EDIT_WORK_ORDER_ALLOCATION',
  'MANAGE_MACHINES',
  'MANAGE_ROUTE_TEMPLATES',
  'MANAGE_PART_NUMBER_MASTER',
];

function wireUser(
  id: number,
  name: string,
  permissions: readonly Permission[],
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    login_name: name.toLowerCase().split(' ')[0],
    display_name: name,
    role_id: 2,
    role_name: 'Manager',
    avatar_updated_at: null,
    permissions,
    must_change_password: false,
    session_expires_at: null,
    theme_preference: null,
    ...overrides,
  };
}

const ADA = wireUser(1, 'Ada Admin', PERMISSIONS);
const BEN = wireUser(2, 'Ben Boss', PERMISSIONS);

const WORK_ORDER = {
  id: 1,
  work_order_number: '007201',
  received_date: '2026-09-01',
  due_date: '2026-10-30',
  status: 'OPEN',
  completed_at: null,
  done_date: null,
  due_outcome: null,
  days_late: null,
};

const DEMAND = {
  id: 11,
  work_order_id: 1,
  part_number: 'A-100',
  request_type: 'NEW',
  requested_quantity: 5,
  allocated_quantity: 0,
  due_date: '2026-10-30',
  priority_rank: null,
  job_numbers: [],
  requester: null,
  reason: null,
  notes: null,
  has_released_quantity: false,
  released_quantity: 0,
  remaining_quantity: 5,
  has_allocation_history: false,
};

let sessionUser: Record<string, unknown> | null;
let setupOpen: boolean;
/** The user the next sign-in (POST /api/session) answers with. */
let nextSignIn: Record<string, unknown>;
let requests: string[];

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  clearHotHistory();
  clearPriorityFocus();
  sessionUser = null;
  setupOpen = false;
  nextSignIn = ADA;
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url === '/api/health') return json({ status: 'ok' });
      if (url === '/api/session') {
        if (method === 'POST') {
          sessionUser = nextSignIn;
          return json({ user: sessionUser, setup_open: false });
        }
        if (method === 'DELETE') {
          sessionUser = null;
          return Promise.resolve(new Response(null, { status: 204 }));
        }
        return json({ user: sessionUser, setup_open: setupOpen });
      }
      requests.push(`${method} ${url}`);
      if (url === '/api/test-expire' || sessionUser === null) {
        return json({ detail: A1, authentication_required: true }, 401);
      }
      if (url === '/api/area-board') {
        return json({ department: { id: 1, name: 'Machining' }, areas: [] });
      }
      if (url === '/api/policies/due-soon') {
        return json({
          due_soon_min_days: 2,
          due_soon_lead_time_percent: 15,
          due_soon_max_days: 7,
          updated_at: '2026-10-01T08:00:00Z',
        });
      }
      if (url === '/api/work-orders' || url.startsWith('/api/work-orders?')) {
        return json([
          { ...WORK_ORDER, demand_line_count: 1, part_numbers: ['A-100'] },
        ]);
      }
      if (url === '/api/work-orders/1') {
        return json({ ...WORK_ORDER, demands: [DEMAND] });
      }
      if (url === '/api/hot-list') {
        return json({ department: { id: 1, name: 'Machining' }, entries: [] });
      }
      return json([]);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderAt(path: string) {
  window.history.replaceState({}, '', path);
  return render(<App />);
}

/** The Management requests sent (sign-in, health and policy reads are
 * not Management reads). */
function managementRequests(): string[] {
  return requests.filter((r) => !r.endsWith('/api/policies/due-soon'));
}

/** The application content (the account chip in the navigation has
 * its own Sign in and Set up actions). */
function content(): HTMLElement {
  return document.querySelector('main') as HTMLElement;
}

function subNavLinks(): string[] {
  const nav = screen.getByRole('navigation', { name: 'Management sub views' });
  return within(nav)
    .queryAllByRole('link')
    .map((a) => a.textContent ?? '');
}

/** Sign in through the open Sign-in dialog as `nextSignIn`. */
async function signInFromDialog() {
  const dialog = await screen.findByRole('dialog', { name: 'Sign in' });
  fireEvent.change(within(dialog).getByLabelText('Login name'), {
    target: { value: 'someone' },
  });
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: 'secret-password' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull(),
  );
}

/** The server ends the sign-in: the next request is refused (401). */
async function expireSignIn() {
  await act(async () => {
    await apiRequest('/api/test-expire').catch(() => undefined);
  });
}

/* ============ FM-1: the Management sign-in gate ============ */

test('FM-1: signed out, Management shows its sign-in panel, opens Sign in once and reads nothing', async () => {
  renderAt('/management/work-orders');

  expect(
    await screen.findByText("Sign in to use PartFlow's Management screens."),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Management', level: 1 }),
  ).toBeInTheDocument();
  expect(
    await screen.findByRole('dialog', { name: 'Sign in' }),
  ).toBeInTheDocument();
  // Navigation is never authorization: signed out, every sub view is
  // listed.
  expect(subNavLinks()).toEqual([
    'Area Board',
    'Work Orders',
    'PN Tracking',
    'Priority',
    'Planned Routes',
    'Part Numbers',
    'Machines',
  ]);

  // Cancelling leaves the panel; switching sub views opens nothing again.
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
  expect(
    within(content()).getByRole('button', { name: 'Sign in' }),
  ).toHaveFocus();
  fireEvent.click(screen.getByRole('link', { name: 'Machines' }));
  expect(window.location.pathname).toBe('/management/machines');
  expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
  expect(managementRequests()).toEqual([]);
});

test('FM-1: while PartFlow has no administrator, the panel offers setup first and opens nothing', async () => {
  setupOpen = true;
  renderAt('/management/machines');

  const setup = await within(content()).findByRole('button', {
    name: 'Set up PartFlow',
  });
  expect(setup).toHaveClass('primary');
  expect(
    screen.getByText(
      'PartFlow has no administrator yet. Set up PartFlow with the setup token from the server log, or sign in if you already have an account.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
  expect(managementRequests()).toEqual([]);
});

test('FM-1: a password an administrator set keeps Management closed and unread', async () => {
  sessionUser = wireUser(5, 'Bea Buyer', PERMISSIONS, {
    must_change_password: true,
  });
  renderAt('/management/work-orders');

  expect(
    await screen.findByText(
      "Choose a new password to use PartFlow's Management screens.",
    ),
  ).toBeInTheDocument();
  expect(
    within(content()).queryByRole('button', { name: 'Sign in' }),
  ).toBeNull();
  expect(managementRequests()).toEqual([]);
});

test('FM-1: a sign-in that cannot be read shows the checking error with Retry — never the panel', async () => {
  vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/health') return json({ status: 'ok' });
    if (url === '/api/session') return json({ detail: 'Down.' }, 503);
    requests.push(`GET ${url}`);
    return json([]);
  });
  renderAt('/management/machines');

  expect(
    await screen.findByText('Your sign-in could not be checked.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  expect(
    within(content()).queryByRole('button', { name: 'Sign in' }),
  ).toBeNull();
  expect(managementRequests()).toEqual([]);
});

test('FM-1: an ended sign-in keeps open work for the same user only; signing out shows the panel', async () => {
  sessionUser = ADA;
  renderAt('/management/work-orders');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Open Work Order 007201' }),
  );
  const details = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  const jobField = () =>
    within(
      screen.getByRole('dialog', { name: 'Work Order Details' }),
    ).getByLabelText('Job Numbers for A-100');
  fireEvent.change(within(details).getByLabelText('Job Numbers for A-100'), {
    target: { value: 'J-77' },
  });

  // The server ends the sign-in: the draft stays under the dialog.
  await expireSignIn();
  expect(
    await screen.findByRole('dialog', { name: 'Sign in' }),
  ).toBeInTheDocument();
  expect(jobField()).toHaveValue('J-77');

  // The same user signs in again: the draft is still there.
  nextSignIn = { ...ADA };
  await signInFromDialog();
  expect(jobField()).toHaveValue('J-77');

  // The sign-in ends again and another user signs in: Management is
  // remounted for them, the draft is gone.
  await expireSignIn();
  nextSignIn = BEN;
  await signInFromDialog();
  expect(
    await screen.findByRole('button', { name: 'Open Work Order 007201' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('dialog', { name: 'Work Order Details' }),
  ).toBeNull();

  // An explicit sign-out shows the panel and opens nothing.
  fireEvent.click(screen.getByRole('button', { name: 'Account: Ben Boss' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  expect(
    await screen.findByText("Sign in to use PartFlow's Management screens."),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
});

test('FM-1: an ended sign-in pauses a kept live view: a closed Sign in stays closed until the same user signs in again', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    sessionUser = ADA;
    renderAt('/management/area-board');
    expect(
      await screen.findByText(
        'No active Areas are configured in this Department.',
      ),
    ).toBeInTheDocument();
    const boardReads = () =>
      requests.filter((r) => r === 'GET /api/area-board').length;
    expect(boardReads()).toBe(1);

    // The server ends the sign-in: the next refresh is refused and the
    // Sign-in dialog opens over the kept board.
    sessionUser = null;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AREA_BOARD_REFRESH_MS);
    });
    expect(
      await screen.findByRole('dialog', { name: 'Sign in' }),
    ).toBeInTheDocument();
    expect(boardReads()).toBe(2);

    // Closed, the dialog stays closed: the feed no longer polls.
    fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * AREA_BOARD_REFRESH_MS);
    });
    expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
    expect(boardReads()).toBe(2);

    // The same user signs in again: the feed reads at once and keeps
    // refreshing.
    nextSignIn = { ...ADA };
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await signInFromDialog();
    await waitFor(() => expect(boardReads()).toBe(3));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AREA_BOARD_REFRESH_MS);
    });
    expect(boardReads()).toBe(4);
  } finally {
    vi.useRealTimers();
  }
});

/* ============ FM-2: access, sub-view bar, entry redirect ============ */

test('FM-2: a sub view the user may not open shows its access panel and reads nothing', async () => {
  sessionUser = wireUser(3, 'Pat Parts', ['MANAGE_PART_NUMBER_MASTER']);
  renderAt('/management/area-board');

  const panel = await screen.findByRole('region', { name: 'Area Board' });
  expect(
    within(panel).getByRole('heading', { name: 'Area Board', level: 1 }),
  ).toBeInTheDocument();
  expect(panel).toHaveTextContent(
    'Your account cannot open Area Board. Opening it needs the View all current and historical production data permission.',
  );
  expect(panel).toHaveTextContent('Ask an administrator if you need access.');

  // Signed in, the bar lists exactly the sub views the user may open.
  expect(subNavLinks()).toEqual(['Part Numbers']);

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  await waitFor(() =>
    expect(window.location.pathname).toBe('/management/part-numbers'),
  );
  window.history.pushState({}, '', '/management/work-orders');
  fireEvent(window, new PopStateEvent('popstate'));
  const woPanel = await screen.findByRole('region', { name: 'Work Orders' });
  expect(woPanel).toHaveTextContent(
    'Your account cannot open Work Orders. Opening it needs one of these permissions: View all current and historical production data, Create and edit Work Orders, Edit Work Order Demand, Edit Work Order Allocation.',
  );
  expect(
    requests.filter(
      (r) => r.includes('/api/area-board') || r.includes('/api/work-orders'),
    ),
  ).toEqual([]);
});

test('FM-2: a signed-in bare /management lands on the first sub view the user may open', async () => {
  sessionUser = wireUser(1, 'Ada Admin', ADMINISTRATOR_KEYS);
  renderAt('/management');

  await waitFor(() =>
    expect(window.location.pathname).toBe('/management/work-orders'),
  );
  expect(
    await screen.findByRole('heading', { name: 'Work Orders', level: 1 }),
  ).toBeInTheDocument();
  expect(subNavLinks()).toEqual([
    'Work Orders',
    'PN Tracking',
    'Planned Routes',
    'Part Numbers',
    'Machines',
  ]);
  expect(requests.some((r) => r.includes('/api/area-board'))).toBe(false);
});

test('FM-2: signing in from the panel of a bare /management entry moves to the first sub view the user may open', async () => {
  renderAt('/management');
  expect(window.location.pathname).toBe('/management/area-board');
  await screen.findByText("Sign in to use PartFlow's Management screens.");

  nextSignIn = wireUser(1, 'Ada Admin', ADMINISTRATOR_KEYS);
  await signInFromDialog();

  await waitFor(() =>
    expect(window.location.pathname).toBe('/management/work-orders'),
  );
  // Focus moves to the active sub-view link.
  await waitFor(() =>
    expect(screen.getByRole('link', { name: 'Work Orders' })).toHaveFocus(),
  );
  expect(requests.some((r) => r.includes('/api/area-board'))).toBe(false);
});

test('FM-2: a deep link keeps its URL after a sign-in and shows the access panel with focus on its heading', async () => {
  renderAt('/management/area-board');
  await screen.findByText("Sign in to use PartFlow's Management screens.");

  nextSignIn = wireUser(1, 'Ada Admin', ADMINISTRATOR_KEYS);
  await signInFromDialog();

  const heading = await screen.findByRole('heading', {
    name: 'Area Board',
    level: 1,
  });
  expect(window.location.pathname).toBe('/management/area-board');
  await waitFor(() => expect(heading).toHaveFocus());
  expect(requests.some((r) => r.includes('/api/area-board'))).toBe(false);
});

/* ============ FM-1: the Priority history belongs to its user ============ */

const STEP_ENTRY = { workOrderDemandId: 99 } as unknown as HotListEntry;

function recordStep() {
  act(() =>
    recordChange({
      demandId: 99,
      entrySnapshot: STEP_ENTRY,
      undo: { kind: 'remove' },
      redo: { kind: 'insert', index: 0 },
    }),
  );
}

async function undoButton() {
  return screen.findByRole('button', { name: '⟲ Undo' });
}

test('FM-1: the Priority Undo/Redo history is never offered to another user', async () => {
  sessionUser = ADA;
  renderAt('/management/priority');
  await undoButton();
  recordStep();
  await waitFor(async () => expect(await undoButton()).toBeEnabled());

  // An ended sign-in renewed by the same user keeps the history.
  await expireSignIn();
  nextSignIn = { ...ADA };
  await signInFromDialog();
  await waitFor(async () => expect(await undoButton()).toBeEnabled());

  // Another user signing in after an ended sign-in (no sign-out) never
  // sees it.
  await expireSignIn();
  nextSignIn = BEN;
  await signInFromDialog();
  await waitFor(async () => expect(await undoButton()).toBeDisabled());
  expect(screen.getByRole('button', { name: '⟳ Redo' })).toBeDisabled();

  // Ben's own step, then an explicit sign-out: Ada signing in after it
  // does not see Ben's history either.
  recordStep();
  await waitFor(async () => expect(await undoButton()).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Account: Ben Boss' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  await screen.findByText("Sign in to use PartFlow's Management screens.");
  fireEvent.click(within(content()).getByRole('button', { name: 'Sign in' }));
  nextSignIn = ADA;
  await signInFromDialog();
  await waitFor(async () => expect(await undoButton()).toBeDisabled());
});

/* ============ FT-7: an unread Change priority hand-off ends ============ */

test('FT-7: a Change priority hand-off Priority never read reaches no other user and no later visit', async () => {
  sessionUser = ADA;
  renderAt('/management/work-orders');
  await screen.findByRole('heading', { name: 'Work Orders', level: 1 });
  // Priority never mounted (its view failed to load, say): still pending.
  act(() => requestPriorityFocus('A-100'));

  // An ended sign-in renewed by the same user keeps it.
  await expireSignIn();
  nextSignIn = { ...ADA };
  await signInFromDialog();
  expect(peekPriorityFocus()).toBe('A-100');

  // Another user signing in after an ended sign-in never receives it.
  await expireSignIn();
  nextSignIn = BEN;
  await signInFromDialog();
  expect(peekPriorityFocus()).toBeNull();

  // An explicit sign-out ends it.
  act(() => requestPriorityFocus('A-100'));
  fireEvent.click(screen.getByRole('button', { name: 'Account: Ben Boss' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  await screen.findByText("Sign in to use PartFlow's Management screens.");
  expect(peekPriorityFocus()).toBeNull();
});

test('FT-7: leaving Priority before it read the hand-off ends it', async () => {
  sessionUser = ADA;
  renderAt('/management/priority');
  await undoButton();
  // Requested after Priority read its hand-off at mount: unread.
  act(() => requestPriorityFocus('A-100'));
  fireEvent.click(screen.getByRole('link', { name: 'Work Orders' }));
  await screen.findByRole('heading', { name: 'Work Orders', level: 1 });
  expect(peekPriorityFocus()).toBeNull();
});
