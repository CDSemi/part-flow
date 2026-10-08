import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';
import { ApiError } from '../api/client';
import { STATION_PERMISSIONS } from '../api/scan-station';
import type { SessionUser } from '../api/session';
import { NOTICE_WARN_MS } from '../views/scan-station/scan-station-wizard';
import { ConnectivityContext } from './connectivity-context';
import { SessionContext } from './session-context';
import type { SessionValue } from './session-context';
import { useStationTheme, useTheme, useUserTheme } from './theme-context';
import type {
  StationThemeOptions,
  StationThemeRead,
  Theme,
  ThemeValue,
  UserThemeOptions,
} from './theme-context';
import { ThemeProvider } from './theme-provider';
import { UserThemeBinding } from './user-theme-binding';

// The theme's User tier (Phase 14 slice 8, GUI_DESIGN §2.1) against a
// fake `/api` that models the wire contract exactly: `GET /api/session`
// reports the signed-in User's `theme_preference` (`DARK` / `LIGHT` /
// null), and `PUT /api/session/theme-preference` saves an absolute value
// and echoes it. Covers the precedence User → Scan Station → Dark on
// desk, station and kiosk routes; the toggle saving the User tier while
// signed in (never the station's) and the station tier only while
// nobody is known to be signed in; single-flight saves; session-only
// toggles offline, while a new password is required or while the
// sign-in state is unknown; the failure warnings and their surfaces; an
// ended sign-in during a save (no Sign-in dialog); and sign-out
// returning to Station → Dark. A provider-level harness pins the rules
// that need exact ordering.

const STATION = 'S1';
const THEME_BUTTON = /^(🌙 Dark|☀️ Light)$/;
const NOT_CONFIRMED_LIGHT =
  'Light mode applies to this browser session only — PartFlow did not confirm saving it to your account. To save Light for your account, switch to Dark and back.';
const SIGN_IN_ENDED =
  'Your sign-in has ended. Sign in again to save your theme to your account.';

type WireTheme = 'DARK' | 'LIGHT' | null;

/** One planned answer of a theme PUT: held until released, and/or
 * answered with an error status (nothing saved). */
interface PutPlan {
  hold?: Promise<void>;
  status?: number;
}

/** The server's sign-in of this browser (null = nobody signed in). */
let signedIn: boolean;
let userTheme: WireTheme;
let mustChange: boolean;
/** The answer of the next sign-in (`POST /api/session`). */
let signInTheme: WireTheme;
let stationTheme: WireTheme;
let healthDown: boolean;
/** `GET /api/session` fails: with a 500 answer, or with no answer. */
let sessionFailure: 'status' | 'network' | null;
let sessionPlans: PutPlan[];
let stationPlans: PutPlan[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any; headers: any }[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function wireUser(theme: WireTheme = userTheme) {
  return {
    id: 7,
    login_name: 'jdoe',
    display_name: 'Jane Doe',
    role_id: 2,
    role_name: 'Manager',
    avatar_updated_at: null,
    permissions: ['VIEW_PRODUCTION_DATA'],
    must_change_password: mustChange,
    session_expires_at: null,
    theme_preference: theme,
  };
}

function sessionState() {
  return { user: signedIn ? wireUser() : null, setup_open: false };
}

function areaRef() {
  return {
    id: 2,
    name: 'Plating',
    color: '#33aa66',
    description: null,
    is_terminal: false,
  };
}

function refusal(status: number): Response {
  if (status === 401) {
    signedIn = false;
    return json(
      {
        detail:
          'You are not signed in, or your sign-in has ended. Sign in to continue.',
        authentication_required: true,
      },
      401,
    );
  }
  if (status === 403) {
    mustChange = true;
    return json(
      {
        detail: 'Choose a new password before you continue.',
        password_change_required: true,
      },
      403,
    );
  }
  return json({ detail: 'The server is restarting.' }, status);
}

function handle(
  url: string,
  method: string,
  body: unknown,
  plan: PutPlan | undefined,
): Response {
  if (url === '/api/health') {
    return healthDown
      ? json({ status: 'unavailable' }, 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/session' && method === 'GET') {
    if (sessionFailure === 'status') {
      return json({ detail: 'The server is restarting.' }, 500);
    }
    return json(sessionState());
  }
  if (url === '/api/session' && method === 'DELETE') {
    signedIn = false;
    return new Response(null, { status: 204 });
  }
  if (url === '/api/session' && method === 'POST') {
    signedIn = true;
    userTheme = signInTheme;
    return json(sessionState());
  }
  if (url === '/api/session/password' && method === 'PUT') {
    mustChange = false;
    return json(sessionState());
  }
  if (url === '/api/session/theme-preference' && method === 'PUT') {
    if (plan?.status !== undefined) return refusal(plan.status);
    const value = (body as { theme_preference: 'DARK' | 'LIGHT' })
      .theme_preference;
    userTheme = value;
    return json({ theme_preference: value });
  }
  if (url === '/api/policies/due-soon') {
    return json({
      due_soon_min_days: 2,
      due_soon_lead_time_percent: 15,
      due_soon_max_days: 7,
      updated_at: '2026-10-01T08:00:00Z',
    });
  }
  if (url === '/api/machines') return json([]);
  if (url === `/api/scan-stations/${STATION}/context`) {
    return json({
      station_id: STATION,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(),
      operations: [
        { id: 20, code: 'PLATE', name: 'Plating', is_external: false },
      ],
      has_machines: false,
      worker_identification: {
        mode: 'DISABLED',
        fixed_worker: null,
        session: null,
        final_gates: { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' },
      },
      theme_preference: stationTheme,
      device: { id: 1, label: 'Station PC' },
      station_permissions: [...STATION_PERMISSIONS],
    });
  }
  if (
    url === `/api/scan-stations/${STATION}/theme-preference` &&
    method === 'PUT'
  ) {
    if (plan?.status !== undefined) return refusal(plan.status);
    const value = (body as { theme_preference: 'DARK' | 'LIGHT' })
      .theme_preference;
    stationTheme = value;
    return json({ station_id: STATION, theme_preference: value });
  }
  if (url === '/api/areas/2/inventory') {
    return json({
      area: areaRef(),
      demand_context: [],
      scrapped: [],
      has_machines: false,
      lines: [],
      total_part_numbers: 0,
      total_quantity: 0,
      queued: [],
      queued_quantity: 0,
      machines: [],
      on_machine_quantity: 0,
      processing: [],
      processing_quantity: 0,
      finished: [],
      finished_quantity: 0,
    });
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  document.body.className = '';
  signedIn = true;
  userTheme = null;
  mustChange = false;
  signInTheme = null;
  stationTheme = null;
  healthDown = false;
  sessionFailure = null;
  sessionPlans = [];
  stationPlans = [];
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (url !== '/api/health') {
        requests.push({ url, method, body, headers: init?.headers });
      }
      if (url === '/api/session' && sessionFailure === 'network') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      const plan =
        url === '/api/session/theme-preference'
          ? sessionPlans.shift()
          : url.endsWith('/theme-preference')
            ? stationPlans.shift()
            : undefined;
      // The answer reflects the server state when the request was SENT.
      const response = handle(url, method, body, plan);
      return (plan?.hold ?? Promise.resolve()).then(() => response);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ============ Helpers ============ */

function shown(): string {
  return document.body.className;
}

function toggle() {
  fireEvent.click(screen.getByRole('button', { name: THEME_BUTTON }));
}

function sessionPuts() {
  return requests.filter(
    (r) => r.method === 'PUT' && r.url === '/api/session/theme-preference',
  );
}

function sessionPutBodies() {
  return sessionPuts().map((r) => r.body.theme_preference as string);
}

function stationPuts() {
  return requests.filter(
    (r) =>
      r.method === 'PUT' &&
      r.url === `/api/scan-stations/${STATION}/theme-preference`,
  );
}

function sessionReads() {
  return requests.filter((r) => r.url === '/api/session' && r.method === 'GET')
    .length;
}

/** Let pending answers and effects settle. */
async function settle(ms = 50) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function holdUntilReleased(): { hold: Promise<void>; release: () => void } {
  let release!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { hold, release };
}

async function goOffline() {
  healthDown = true;
  window.dispatchEvent(new Event('offline'));
  await screen.findByText(/OFFLINE — Connection to the PartFlow server/);
}

async function goOnline() {
  healthDown = false;
  window.dispatchEvent(new Event('online'));
  await waitFor(() =>
    expect(
      screen.queryByText(/OFFLINE — Connection to the PartFlow server/),
    ).toBeNull(),
  );
  await settle();
}

/** In-app navigation (the router follows history). */
function go(path: string) {
  act(() => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
}

/** Render the app and wait until the sign-in state is known. */
async function renderAt(path: string) {
  window.history.replaceState({}, '', path);
  render(<App />);
  await waitFor(() => expect(sessionReads()).toBeGreaterThan(0));
  await settle();
}

async function renderStation(path = `/scan-station/${STATION}`) {
  window.history.replaceState({}, '', path);
  render(<App />);
  await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  await settle();
}

/** The app toast(s) (`.toast`), by text. */
function appToasts(): string[] {
  return [...document.querySelectorAll('.toast')].map(
    (node) => node.textContent ?? '',
  );
}

function stationNotice(): HTMLElement | null {
  return document.querySelector('.ss-toast');
}

async function signOutFromChip() {
  fireEvent.click(screen.getByRole('button', { name: 'Account: Jane Doe' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Account: Jane Doe' }),
    ).toBeNull(),
  );
  // The release runs in an effect after the chip changed.
  await settle();
}

/* ============ Applying the User preference ============ */

test.each([
  ['LIGHT' as const, 'light'],
  [null, 'dark'],
])(
  'FU-1: a signed-in User with preference %s shows %s on a desk route; nothing is saved',
  async (saved, expected) => {
    userTheme = saved;
    await renderAt('/management/area-board');
    await waitFor(() => expect(shown()).toBe(expected));
    expect(sessionPuts()).toHaveLength(0);
    expect(stationPuts()).toHaveLength(0);
  },
);

test('FU-3: at a station the User preference is above the station preference; the toggle saves only the User tier (production and standard)', async () => {
  stationTheme = 'LIGHT';
  userTheme = 'DARK';
  await renderStation(`/scan-station/${STATION}/production`);
  expect(shown()).toBe('dark');

  toggle();
  expect(shown()).toBe('light');
  await waitFor(() => expect(sessionPutBodies()).toEqual(['LIGHT']));
  await settle();
  expect(stationPuts()).toHaveLength(0);

  go(`/scan-station/${STATION}`);
  await screen.findByText('Total PNs');
  toggle();
  expect(shown()).toBe('dark');
  await waitFor(() => expect(sessionPutBodies()).toEqual(['LIGHT', 'DARK']));
  await settle();
  expect(stationPuts()).toHaveLength(0);
  expect(stationTheme).toBe('LIGHT');
  expect(userTheme).toBe('DARK');
});

/* ============ Saving the User preference ============ */

test('FU-2: signed in on a desk route, the toggle switches at once and saves the User preference once', async () => {
  await renderAt('/management/area-board');
  const { hold, release } = holdUntilReleased();
  sessionPlans.push({ hold });

  toggle();
  expect(shown()).toBe('light');
  await waitFor(() => expect(sessionPuts()).toHaveLength(1));
  expect(sessionPuts()[0].body).toEqual({ theme_preference: 'LIGHT' });
  expect(sessionPuts()[0].headers).toMatchObject({ 'X-PartFlow-CSRF': '1' });

  release();
  await settle();
  expect(sessionPuts()).toHaveLength(1);
  expect(stationPuts()).toHaveLength(0);
  expect(userTheme).toBe('LIGHT');
  expect(shown()).toBe('light');
});

test('FU-5: quick toggles keep one request in flight; the latest choice is sent next, once', async () => {
  await renderAt('/management/area-board');
  const { hold, release } = holdUntilReleased();
  sessionPlans.push({ hold });

  toggle();
  toggle();
  expect(shown()).toBe('dark');
  await settle();
  expect(sessionPutBodies()).toEqual(['LIGHT']);

  release();
  await waitFor(() => expect(sessionPutBodies()).toEqual(['LIGHT', 'DARK']));
  await settle();
  expect(sessionPuts()).toHaveLength(2);
  expect(userTheme).toBe('DARK');
});

test('FU-6: offline the toggle is session-only; nothing is sent, shown or queued for reconnection', async () => {
  await renderAt('/management/area-board');
  await goOffline();

  toggle();
  expect(shown()).toBe('light');
  await settle();
  expect(sessionPuts()).toHaveLength(0);
  expect(appToasts()).toEqual([]);

  await goOnline();
  expect(sessionPuts()).toHaveLength(0);
  expect(stationPuts()).toHaveLength(0);
  expect(shown()).toBe('light');
});

test('FU-7: while a new password is required the toggle is session-only (production mode shows no dialog)', async () => {
  mustChange = true;
  userTheme = 'LIGHT';
  await renderStation(`/scan-station/${STATION}/production`);
  // The preference still applies.
  expect(shown()).toBe('light');

  toggle();
  expect(shown()).toBe('dark');
  await settle();
  expect(sessionPuts()).toHaveLength(0);
  expect(stationPuts()).toHaveLength(0);
  expect(stationNotice()).toBeNull();
});

test('FU-12: a later session answer for the same User never changes the screen', async () => {
  userTheme = 'LIGHT';
  await renderAt('/production-board');
  await waitFor(() => expect(shown()).toBe('light'));
  // Another browser saved DARK meanwhile; the password change answers it.
  userTheme = 'DARK';

  fireEvent.click(screen.getByRole('button', { name: 'Account: Jane Doe' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Change password' });
  fireEvent.change(within(dialog).getByLabelText('Current password'), {
    target: { value: 'old password 12' },
  });
  fireEvent.change(within(dialog).getByLabelText('New password'), {
    target: { value: 'a new password 34' },
  });
  fireEvent.change(within(dialog).getByLabelText('Repeat new password'), {
    target: { value: 'a new password 34' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Change password' }),
    ).toBeNull(),
  );
  await settle();
  expect(shown()).toBe('light');
  expect(sessionPuts()).toHaveLength(0);
});

/* ============ Failures ============ */

test('FU-8a: a failed save on a desk route shows the app toast; no retry; switching away and back saves again in order', async () => {
  await renderAt('/management/area-board');
  sessionPlans.push({ status: 500 });

  toggle();
  await waitFor(() =>
    expect(appToasts()).toEqual([`⚠ ${NOT_CONFIRMED_LIGHT}`]),
  );
  expect(shown()).toBe('light');
  await settle();
  expect(sessionPuts()).toHaveLength(1);

  toggle();
  toggle();
  await waitFor(() =>
    expect(sessionPutBodies()).toEqual(['LIGHT', 'DARK', 'LIGHT']),
  );
  await settle();
  expect(shown()).toBe('light');
  expect(userTheme).toBe('LIGHT');
});

test('FU-8b: at a loaded station the failure shows in the station floating warning (8 s, closable), never the app toast', async () => {
  await renderStation(`/scan-station/${STATION}/production`);
  vi.useFakeTimers({ shouldAdvanceTime: true });
  sessionPlans.push({ status: 500 });

  toggle();
  const notice = await waitFor(() => {
    const found = stationNotice();
    if (!found) throw new Error('no notice');
    return found;
  });
  expect(notice.className).toContain('warn');
  expect(notice.querySelector('.fic')).toHaveTextContent('⚠');
  expect(notice.querySelector('.t1')?.textContent).toBe(
    'Theme not confirmed for your account',
  );
  expect(notice.querySelector('.t2')?.textContent).toBe(NOT_CONFIRMED_LIGHT);
  expect(
    within(notice).getByRole('button', { name: 'Dismiss notification' }),
  ).toBeInTheDocument();
  expect(appToasts()).toEqual([]);
  expect(stationPuts()).toHaveLength(0);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(NOTICE_WARN_MS - 500);
  });
  expect(stationNotice()).not.toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(stationNotice()).toBeNull();
});

test('FU-9a: an ended sign-in during a save signs out without a dialog or another read; the Management area stays', async () => {
  userTheme = 'LIGHT';
  await renderAt('/management/area-board');
  await waitFor(() => expect(shown()).toBe('light'));
  const reads = sessionReads();
  sessionPlans.push({ status: 401 });

  toggle();
  expect(shown()).toBe('dark');
  await waitFor(() => expect(appToasts()).toEqual([`⚠ ${SIGN_IN_ENDED}`]));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument(),
  );
  await settle();
  expect(shown()).toBe('dark');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.queryByText("Sign in to use PartFlow's Management screens."),
  ).toBeNull();
  expect(document.querySelector('.signin-gate')).toBeNull();
  expect(sessionReads()).toBe(reads);
  expect(sessionPuts()).toHaveLength(1);
});

test('FU-9b: at a station an ended sign-in warns in the station notice and returns to the station theme; no dialog in standard mode', async () => {
  stationTheme = 'LIGHT';
  userTheme = 'LIGHT';
  await renderStation(`/scan-station/${STATION}/production`);
  expect(shown()).toBe('light');
  const reads = sessionReads();
  sessionPlans.push({ status: 401 });

  toggle();
  const notice = await waitFor(() => {
    const found = stationNotice();
    if (!found) throw new Error('no notice');
    return found;
  });
  expect(notice.querySelector('.t1')?.textContent).toBe('Sign-in ended');
  expect(notice.querySelector('.t2')?.textContent).toBe(SIGN_IN_ENDED);
  await waitFor(() => expect(shown()).toBe('light'));
  expect(appToasts()).toEqual([]);

  go(`/scan-station/${STATION}`);
  await screen.findByText('Total PNs');
  await settle();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  expect(sessionReads()).toBe(reads);
  // Signed out now: the toggle saves the station tier again.
  toggle();
  await waitFor(() => expect(stationPuts()).toHaveLength(1));
  expect(sessionPuts()).toHaveLength(1);
});

test('FU-17: a required password change during a save opens the forced dialog through the re-read; no theme warning', async () => {
  await renderAt('/management/area-board');
  const reads = sessionReads();
  sessionPlans.push({ status: 403 });

  toggle();
  await screen.findByRole('dialog', { name: 'Choose a new password' });
  expect(sessionReads()).toBe(reads + 1);
  await settle();
  expect(appToasts()).toEqual([]);
  expect(sessionPuts()).toHaveLength(1);
  expect(stationPuts()).toHaveLength(0);
});

test('FU-15: in the Kiosk the compact toggle saves the User tier; a failure shows the app toast', async () => {
  await renderAt('/production-board/kiosk');
  toggle();
  await waitFor(() => expect(sessionPutBodies()).toEqual(['LIGHT']));

  sessionPlans.push({ status: 500 });
  toggle();
  await waitFor(() =>
    expect(appToasts()).toEqual([
      '⚠ Dark mode applies to this browser session only — PartFlow did not confirm saving it to your account. To save Dark for your account, switch to Light and back.',
    ]),
  );
  expect(sessionPutBodies()).toEqual(['LIGHT', 'DARK']);
  expect(stationPuts()).toHaveLength(0);
});

/* ============ Sign-out and sign-in ============ */

test('FU-10: sign-out returns a desk route to Dark', async () => {
  userTheme = 'LIGHT';
  await renderAt('/management/area-board');
  await waitFor(() => expect(shown()).toBe('light'));
  await signOutFromChip();
  expect(shown()).toBe('dark');
});

test('FU-10: sign-out at a station returns to the station preference', async () => {
  stationTheme = 'LIGHT';
  userTheme = 'DARK';
  await renderStation();
  expect(shown()).toBe('dark');
  await signOutFromChip();
  expect(shown()).toBe('light');
  expect(sessionPuts()).toHaveLength(0);
  expect(stationPuts()).toHaveLength(0);
});

test.each([['saved'], ['session-only']])(
  'FU-10: a %s toggle by a User without a preference still returns to Dark on sign-out',
  async (how) => {
    await renderAt('/management/area-board');
    if (how === 'saved') {
      toggle();
      await waitFor(() => expect(userTheme).toBe('LIGHT'));
      await settle();
    } else {
      await goOffline();
      toggle();
      await goOnline();
      expect(sessionPuts()).toHaveLength(0);
    }
    expect(shown()).toBe('light');
    await signOutFromChip();
    expect(shown()).toBe('dark');
  },
);

test('FU-11: signing in applies a saved preference; without one the session choice stays', async () => {
  signedIn = false;
  signInTheme = 'LIGHT';
  await renderAt('/production-board');
  expect(shown()).toBe('dark');

  const signIn = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    const dialog = await screen.findByRole('dialog', { name: 'Sign in' });
    fireEvent.change(within(dialog).getByLabelText('Login name'), {
      target: { value: 'jdoe' },
    });
    fireEvent.change(within(dialog).getByLabelText('Password'), {
      target: { value: 'correct horse battery' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
    await screen.findByRole('button', { name: 'Account: Jane Doe' });
    await settle();
  };

  await signIn();
  expect(shown()).toBe('light');
  expect(sessionPuts()).toHaveLength(0);

  await signOutFromChip();
  expect(shown()).toBe('dark');
  // Anonymous, away from a station: session-only.
  toggle();
  expect(shown()).toBe('light');
  signInTheme = null;
  await signIn();
  expect(shown()).toBe('light');
  expect(sessionPuts()).toHaveLength(0);
  expect(stationPuts()).toHaveLength(0);
});

/* ============ Anonymous and unknown sign-in states ============ */

test('FU-13: anonymous at a station, the toggle saves the station tier, never the User tier', async () => {
  signedIn = false;
  await renderStation(`/scan-station/${STATION}/production`);
  toggle();
  await waitFor(() => expect(stationPuts()).toHaveLength(1));
  await settle();
  expect(stationPuts()[0].body).toEqual({ theme_preference: 'LIGHT' });
  expect(sessionPuts()).toHaveLength(0);
});

test('FU-16: while the sign-in state is unknown the station toggle is session-only; once known signed out it saves the station tier', async () => {
  signedIn = false;
  sessionFailure = 'status';
  stationTheme = 'LIGHT';
  await renderStation();
  expect(shown()).toBe('light');

  toggle();
  expect(shown()).toBe('dark');
  await settle();
  expect(stationPuts()).toHaveLength(0);
  expect(sessionPuts()).toHaveLength(0);

  // Regaining the connection reads the sign-in again.
  sessionFailure = null;
  await goOffline();
  await goOnline();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument(),
  );
  toggle();
  await waitFor(() => expect(stationPuts()).toHaveLength(1));
  expect(stationPuts()[0].body).toEqual({ theme_preference: 'LIGHT' });
  expect(sessionPuts()).toHaveLength(0);
});

test('FU-18: a failed re-read keeps the known sign-in; the next toggle still saves only the User tier', async () => {
  stationTheme = 'DARK';
  userTheme = 'LIGHT';
  await renderStation();
  expect(shown()).toBe('light');
  // A required password change re-reads the sign-in; that read fails.
  sessionFailure = 'network';
  const reads = sessionReads();
  sessionPlans.push({ status: 403 });

  toggle();
  await waitFor(() => expect(sessionReads()).toBe(reads + 1));
  await settle();
  expect(
    screen.getByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeTruthy();
  expect(shown()).toBe('dark');

  toggle();
  await waitFor(() => expect(sessionPutBodies()).toEqual(['DARK', 'LIGHT']));
  await settle();
  expect(shown()).toBe('light');
  expect(stationPuts()).toHaveLength(0);
});

/* ============ Provider-level harness ============ */

let api: ThemeValue;

function Controls({ stationSaves }: { stationSaves?: boolean }) {
  api = useTheme();
  const { setStationSaves } = api;
  useEffect(() => {
    if (stationSaves !== undefined) setStationSaves(stationSaves);
  }, [stationSaves, setStationSaves]);
  return (
    <>
      <span data-testid="theme">{api.theme}</span>
      <button onClick={api.toggleTheme}>toggle</button>
    </>
  );
}

function StationProbe(props: {
  read: StationThemeRead | undefined;
  options: StationThemeOptions;
}) {
  useStationTheme(STATION, props.read, props.options);
  return null;
}

function UserProbe(props: {
  user: { id: number; themePreference: Theme | null } | null;
  options: UserThemeOptions;
}) {
  useUserTheme(props.user, props.options);
  return null;
}

interface HarnessProps {
  station?: {
    read: StationThemeRead | undefined;
    options: StationThemeOptions;
  };
  user?: {
    user: { id: number; themePreference: Theme | null } | null;
    options: UserThemeOptions;
  };
  stationSaves?: boolean;
}

function harness(props: HarnessProps) {
  return (
    <ThemeProvider>
      <Controls stationSaves={props.stationSaves} />
      {props.station ? <StationProbe {...props.station} /> : null}
      {props.user ? <UserProbe {...props.user} /> : null}
    </ThemeProvider>
  );
}

function current(): string {
  return screen.getByTestId('theme').textContent ?? '';
}

function clickToggle() {
  fireEvent.click(screen.getByRole('button', { name: 'toggle' }));
}

interface Deferred {
  promise: Promise<unknown>;
  resolve: () => void;
  reject: (error: unknown) => void;
}

function deferred(): Deferred {
  let resolve!: () => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<unknown>((res, rej) => {
    resolve = () => res(undefined);
    reject = rej;
  });
  return { promise, resolve, reject };
}

function userOptions(overrides: Partial<UserThemeOptions> = {}) {
  return {
    writable: true,
    save: vi.fn(() => Promise.resolve()),
    onSaveFailed: vi.fn(),
    ...overrides,
  } satisfies UserThemeOptions;
}

function stationOptions(overrides: Partial<StationThemeOptions> = {}) {
  return {
    writable: true,
    save: vi.fn(() => Promise.resolve()),
    onSaveFailed: vi.fn(),
    ...overrides,
  } satisfies StationThemeOptions;
}

const read = (preference: Theme | null, epoch = 0): StationThemeRead => ({
  preference,
  epoch,
});

test.each([['fulfilled'], ['rejected']])(
  'PU-1: a User save %s after release changes nothing on screen and never restarts',
  async (outcome) => {
    const pending = deferred();
    const options = userOptions({ save: vi.fn(() => pending.promise) });
    const user = { id: 7, themePreference: null };
    const view = render(harness({ user: { user, options } }));

    clickToggle();
    clickToggle();
    expect(options.save).toHaveBeenCalledExactlyOnceWith('light');
    expect(current()).toBe('dark');
    clickToggle();
    expect(current()).toBe('light');

    view.rerender(harness({ user: { user: null, options } }));
    expect(current()).toBe('dark');

    const error = new Error('lost');
    await act(async () => {
      if (outcome === 'fulfilled') pending.resolve();
      else pending.reject(error);
    });
    expect(current()).toBe('dark');
    expect(options.save).toHaveBeenCalledTimes(1);
    if (outcome === 'fulfilled') {
      expect(options.onSaveFailed).not.toHaveBeenCalled();
    } else {
      expect(options.onSaveFailed).toHaveBeenCalledExactlyOnceWith(
        'dark',
        error,
        undefined,
        true,
      );
    }
  },
);

test('PU-2: User then station resolves User above Station; station then a User without preference keeps the station theme', () => {
  const user = {
    user: { id: 7, themePreference: 'dark' as Theme },
    options: userOptions(),
  };
  const station = { read: read('light'), options: stationOptions() };
  const first = render(harness({ user }));
  expect(current()).toBe('dark');
  first.rerender(harness({ user, station }));
  expect(current()).toBe('dark');
  first.unmount();

  const second = render(harness({ station }));
  expect(current()).toBe('light');
  second.rerender(
    harness({
      station,
      user: { user: { id: 7, themePreference: null }, options: userOptions() },
    }),
  );
  expect(current()).toBe('light');
});

test('PU-3: binding a different User releases the first (Station → Dark), then applies the new preference', () => {
  const station = { read: read('light'), options: stationOptions() };
  const view = render(
    harness({
      station,
      user: {
        user: { id: 7, themePreference: 'dark' },
        options: userOptions(),
      },
    }),
  );
  expect(current()).toBe('dark');
  view.rerender(
    harness({
      station,
      user: { user: { id: 8, themePreference: null }, options: userOptions() },
    }),
  );
  expect(current()).toBe('light');
  view.rerender(
    harness({
      station,
      user: {
        user: { id: 9, themePreference: 'dark' },
        options: userOptions(),
      },
    }),
  );
  expect(current()).toBe('dark');
  expect(api.theme).toBe('dark');
});

test('PU-4: a non-writable User saves nothing; the station is never saved while a User is bound', async () => {
  const station = { read: read(null), options: stationOptions() };
  const options = userOptions({ writable: false });
  const user = { id: 7, themePreference: null };
  const view = render(harness({ station, user: { user, options } }));
  clickToggle();
  expect(current()).toBe('light');
  expect(options.save).not.toHaveBeenCalled();
  expect(station.options.save).not.toHaveBeenCalled();

  const writable = userOptions();
  view.rerender(harness({ station, user: { user, options: writable } }));
  clickToggle();
  await act(async () => {});
  expect(writable.save).toHaveBeenCalledExactlyOnceWith('dark');
  expect(station.options.save).not.toHaveBeenCalled();
});

test.each([['resolves'], ['rejects']])(
  'PU-4c: an anonymous station save that %s after a User binds sends no queued choice and reports nothing',
  async (outcome) => {
    const pending = deferred();
    const station = {
      read: read(null),
      options: stationOptions({ save: vi.fn(() => pending.promise) }),
    };
    const view = render(harness({ station }));
    clickToggle();
    clickToggle();
    expect(station.options.save).toHaveBeenCalledExactlyOnceWith('light');

    view.rerender(
      harness({
        station,
        user: {
          user: { id: 7, themePreference: null },
          options: userOptions(),
        },
      }),
    );
    await act(async () => {
      if (outcome === 'resolves') pending.resolve();
      else pending.reject(new Error('lost'));
    });
    expect(station.options.save).toHaveBeenCalledTimes(1);
    expect(station.options.onSaveFailed).not.toHaveBeenCalled();
  },
);

test('PU-5: station reads apply while the User tier is empty, never while a User save is pending or after it is saved', async () => {
  const pending = deferred();
  const options = userOptions({ save: vi.fn(() => pending.promise) });
  const user = { user: { id: 7, themePreference: null }, options };
  const stationOpts = stationOptions();
  const view = render(
    harness({ user, station: { read: read(null), options: stationOpts } }),
  );
  expect(current()).toBe('dark');
  view.rerender(
    harness({ user, station: { read: read('light'), options: stationOpts } }),
  );
  expect(current()).toBe('light');

  clickToggle();
  expect(current()).toBe('dark');
  view.rerender(
    harness({ user, station: { read: read(null), options: stationOpts } }),
  );
  view.rerender(
    harness({ user, station: { read: read('light'), options: stationOpts } }),
  );
  expect(current()).toBe('dark');

  await act(async () => pending.resolve());
  view.rerender(
    harness({ user, station: { read: read(null), options: stationOpts } }),
  );
  view.rerender(
    harness({ user, station: { read: read('light'), options: stationOpts } }),
  );
  expect(current()).toBe('dark');
  expect(stationOpts.save).not.toHaveBeenCalled();
});

test('PU-6: with station saves closed the toggle never saves the station; reopened, it does', async () => {
  const station = { read: read(null), options: stationOptions() };
  const view = render(harness({ station, stationSaves: false }));
  clickToggle();
  await act(async () => {});
  expect(station.options.save).not.toHaveBeenCalled();

  view.rerender(harness({ station, stationSaves: true }));
  clickToggle();
  await act(async () => {});
  expect(station.options.save).toHaveBeenCalledExactlyOnceWith('dark');
});

test('PU-7: a failed User save hands over the warning of a loaded, writable station only', async () => {
  const showWarning = vi.fn();
  const error = new ApiError(500, 'The server is restarting.');
  const onSaveFailed = vi.fn();
  const options = userOptions({
    save: vi.fn(() => Promise.reject(error)),
    onSaveFailed,
  });
  const user = { user: { id: 7, themePreference: null }, options };

  for (const [station, expected] of [
    [{ read: read(null), options: stationOptions({ showWarning }) }, true],
    [
      {
        read: read(null),
        options: stationOptions({ showWarning, writable: false }),
      },
      false,
    ],
    [undefined, false],
  ] as const) {
    onSaveFailed.mockClear();
    const view = render(harness({ user, station }));
    clickToggle();
    await act(async () => {});
    expect(onSaveFailed).toHaveBeenCalledTimes(1);
    const handed = onSaveFailed.mock.calls[0][2] as
      ((title: string, detail: string) => void) | undefined;
    expect(handed === undefined).toBe(!expected);
    if (handed) {
      handed('t', 'd');
      expect(showWarning).toHaveBeenLastCalledWith('t', 'd');
    }
    expect(onSaveFailed.mock.calls[0][3]).toBe(false);
    view.unmount();
  }
});

/* ============ The bridge, independent of render order (FU-9c) ============ */

const BRIDGE_USER: SessionUser = {
  id: 7,
  loginName: 'jdoe',
  displayName: 'Jane Doe',
  roleId: 2,
  roleName: 'Manager',
  avatarUpdatedAt: null,
  permissions: [],
  mustChangePassword: false,
  sessionExpiresAt: null,
  themePreference: 'light',
};

function sessionValue(user: SessionUser | null): SessionValue {
  return {
    status: user ? 'signed-in' : 'signed-out',
    user,
    setupOpen: false,
    checking: false,
    endedBy: user ? null : 'expired',
    can: () => false,
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    signOut: vi.fn(() => Promise.resolve()),
    refresh: vi.fn(() => Promise.resolve()),
  };
}

function bridge(user: SessionUser | null) {
  return (
    <ThemeProvider>
      <ConnectivityContext.Provider
        value={{ status: 'connected', retry: vi.fn() }}
      >
        <SessionContext.Provider value={sessionValue(user)}>
          <Controls />
          <UserThemeBinding />
        </SessionContext.Provider>
      </ConnectivityContext.Provider>
    </ThemeProvider>
  );
}

test.each([
  [401, [`⚠ ${SIGN_IN_ENDED}`]],
  [500, []],
])(
  'FU-9c: a save refused with %s after the User was already released reports only an ended sign-in',
  async (status, expected) => {
    const { hold, release } = holdUntilReleased();
    sessionPlans.push({ hold, status });
    const view = render(bridge(BRIDGE_USER));
    await act(async () => {});
    expect(current()).toBe('light');

    clickToggle();
    await waitFor(() => expect(sessionPuts()).toHaveLength(1));
    // The release happens before the refusal settles.
    view.rerender(bridge(null));
    expect(current()).toBe('dark');

    release();
    await settle();
    expect(appToasts()).toEqual(expected);
    expect(current()).toBe('dark');
    expect(sessionPuts()).toHaveLength(1);
  },
);
