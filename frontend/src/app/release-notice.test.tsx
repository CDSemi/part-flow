import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityChip } from '../components/ConnectivityChip';
import { WorkerSignInDialog } from '../views/scan-station/scan-station-sign-in-dialog';
import { StationEnrollment } from '../views/scan-station/station-enrollment';
import { ConnectivityContext, useConnectivity } from './connectivity-context';
import type { ConnectivityStatus } from './connectivity-context';
import { ReleaseNotice } from './ReleaseNotice';
import {
  noDialogOpen,
  RELEASE_AUTO_RELOAD_STORAGE_KEY,
} from './release-reload';
import { RouterProvider } from './router-provider';
import { hasUnsavedWork, useUnsavedWork } from './unsaved-work';

// The update notice (GUI_DESIGN §3 rule 13, OD-16-07): the persistent
// banner with its manual `Reload page`, and the automatic reload — after
// a 10 s countdown beside `Reload page`, never while any dialog is open,
// at most once per 10 minutes per tab, and only into a served shell of
// the server's release. The countdown starts over once nothing holds it
// back any more. The Scan Station and Production Board routes count down
// on those conditions alone; every other route also waits for 1 minute
// without input (counting down its last 10 s) and for no unsaved work on
// the page.

const SERVER_RELEASE = 'v2.0.0';
const NOTICE =
  '⚠ UPDATED — PartFlow was updated on the server. Reload this page to continue. Production actions are disabled';

let setStatus: (status: ConnectivityStatus) => void = () => undefined;

function Harness({
  initial,
  children,
}: {
  initial: ConnectivityStatus;
  children?: ReactNode;
}) {
  const [status, set] = useState<ConnectivityStatus>(initial);
  setStatus = set;
  return (
    <ConnectivityContext.Provider
      value={{
        status,
        retry: () => undefined,
        serverRelease: SERVER_RELEASE,
      }}
    >
      <RouterProvider>
        <ReleaseNotice />
        <ConnectivityChip />
        {children}
      </RouterProvider>
    </ConnectivityContext.Provider>
  );
}

function shell(release: string): Response {
  return new Response(
    `<!doctype html><html><head><meta name="partflow-release" content="${release}"></head><body><div id="root"></div></body></html>`,
    { status: 200, headers: { 'Content-Type': 'text/html' } },
  );
}

/** The answer of `GET /` (the served shell). */
let shellAnswer: () => Response;
/** Other requests (badge scans, enrollment) stay pending. */
let fetchMock: ReturnType<typeof vi.fn>;
let reload: ReturnType<typeof vi.fn<() => void>>;
/** The marker `sessionStorage` held when the page reloaded. */
let markerAtReload: (string | null)[];
const locationDescriptor = Object.getOwnPropertyDescriptor(window, 'location')!;

/** `window.location` with a recording `reload` (jsdom's own cannot be
 * replaced); the members the application reads stay the real ones. */
function mockReload() {
  const real = window.location;
  const fake = {
    reload: () => reload(),
    get pathname() {
      return real.pathname;
    },
    get search() {
      return real.search;
    },
    get href() {
      return real.href;
    },
  };
  Object.defineProperty(window, 'location', {
    configurable: true,
    get: () => fake,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  window.sessionStorage.clear();
  markerAtReload = [];
  reload = vi.fn(() => {
    let marker: string | null;
    try {
      marker = window.sessionStorage.getItem(RELEASE_AUTO_RELOAD_STORAGE_KEY);
    } catch {
      marker = null;
    }
    markerAtReload.push(marker);
  });
  mockReload();
  shellAnswer = () => shell(SERVER_RELEASE);
  fetchMock = vi.fn((input: RequestInfo | URL) =>
    String(input) === '/'
      ? Promise.resolve(shellAnswer())
      : new Promise<Response>(() => {}),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  Object.defineProperty(window, 'location', locationDescriptor);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
});

function at(path: string) {
  window.history.replaceState({}, '', path);
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function shellChecks(): number {
  return fetchMock.mock.calls.filter(([input]) => String(input) === '/').length;
}

function openDialogElement(): HTMLElement {
  const element = document.createElement('div');
  element.setAttribute('role', 'dialog');
  document.body.appendChild(element);
  return element;
}

test('FR-11: the notice shows the exact copy and Reload page, takes no focus, and reloads once on click', async () => {
  at('/management/work-orders');
  render(
    <Harness initial="connected">
      <input aria-label="Work field" />
    </Harness>,
  );
  const field = screen.getByLabelText('Work field');
  field.focus();
  expect(screen.queryByText(NOTICE)).toBeNull();

  act(() => setStatus('outdated'));
  const notice = screen.getByRole('alert');
  expect(notice).toHaveClass('offbanner', 'outdated');
  expect(notice).toHaveTextContent(NOTICE);
  const button = within(notice).getByRole('button', { name: 'Reload page' });
  expect(field).toHaveFocus();
  // A page in use shows no countdown.
  expect(within(notice).queryByRole('timer')).toBeNull();

  fireEvent.click(button);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-12: an attended route counts down the last 10 s of 1 minute without input, then reloads itself', async () => {
  at('/management/work-orders');
  render(<Harness initial="outdated" />);
  await advance(49_000);
  expect(screen.queryByRole('timer')).toBeNull();

  await advance(1_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(9_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 1 s');
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);

  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(markerAtReload[0]).not.toBeNull();
});

test('FR-34: input on an attended page stops its countdown at once and postpones the reload; an unattended route ignores input', async () => {
  at('/administration');
  const attended = render(<Harness initial="outdated" />);
  await advance(55_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 5 s');
  fireEvent.keyDown(window, { key: 'Shift' });
  expect(screen.queryByRole('timer')).toBeNull();
  await advance(30_000);
  fireEvent.pointerMove(window);
  await advance(59_000);
  expect(reload).not.toHaveBeenCalled();
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
  attended.unmount();

  window.sessionStorage.clear();
  at('/scan-station/S1/production');
  render(<Harness initial="outdated" />);
  await advance(5_000);
  fireEvent.keyDown(window, { key: 'Shift' });
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 5 s');
  await advance(5_000);
  expect(reload).toHaveBeenCalledTimes(2);
});

test('FR-37: a scroll alone is not use, because the page also scrolls by itself', async () => {
  at('/administration');
  render(<Harness initial="outdated" />);
  await advance(50_000);
  fireEvent.scroll(window);
  fireEvent.scroll(document.body);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(10_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

/** A form on the page with an unsaved edit while `unsaved` is set. */
let setUnsaved: (unsaved: boolean) => void = () => undefined;
function DraftForm() {
  const [unsaved, set] = useState(true);
  setUnsaved = set;
  useUnsavedWork(unsaved);
  return null;
}

test('FR-35: unsaved work or an open dialog holds the countdown of an idle attended page', async () => {
  at('/administration');
  render(
    <Harness initial="outdated">
      <DraftForm />
    </Harness>,
  );
  await advance(10 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);
  expect(screen.queryByRole('timer')).toBeNull();

  const dialog = openDialogElement();
  act(() => setUnsaved(false));
  await advance(60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(screen.queryByRole('timer')).toBeNull();

  dialog.remove();
  await advance(1_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(9_000);
  expect(reload).not.toHaveBeenCalled();
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-36: unsaved work is reported while any reporter holds it and ends with the reporter', () => {
  function Reporter({ unsaved }: { unsaved: boolean }) {
    useUnsavedWork(unsaved);
    return null;
  }
  expect(hasUnsavedWork()).toBe(false);
  const view = render(
    <>
      <Reporter unsaved />
      <Reporter unsaved={false} />
    </>,
  );
  expect(hasUnsavedWork()).toBe(true);
  view.rerender(
    <>
      <Reporter unsaved={false} />
      <Reporter unsaved={false} />
    </>,
  );
  expect(hasUnsavedWork()).toBe(false);
  view.rerender(
    <>
      <Reporter unsaved />
      <Reporter unsaved />
    </>,
  );
  expect(hasUnsavedWork()).toBe(true);
  view.unmount();
  expect(hasUnsavedWork()).toBe(false);
});

test('FR-13: a Scan Station route counts down 10 s beside Reload page, then reloads itself, marker first', async () => {
  at('/scan-station/S1/production');
  render(<Harness initial="outdated" />);
  const notice = screen.getByRole('alert');
  const timer = within(notice).getByRole('timer');
  expect(timer).toHaveTextContent('Reloading in 10 s');
  // Right before the action rail, and never read out as it ticks.
  expect(timer.nextElementSibling).toBe(
    within(notice).getByRole('button', { name: 'Reload page' }),
  );
  expect(timer).toHaveAttribute('aria-live', 'off');
  await advance(9_000);
  expect(timer).toHaveTextContent('Reloading in 1 s');
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);

  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(markerAtReload[0]).not.toBeNull();
  expect(shellChecks()).toBe(1);
  expect(fetchMock).toHaveBeenCalledWith('/', { cache: 'no-store' });
});

test('FR-14: an open dialog holds the countdown, which starts over once the dialog closes', async () => {
  at('/scan-station/S1/production');
  render(<Harness initial="outdated" />);
  await advance(5_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 5 s');

  const dialog = openDialogElement();
  await advance(1_000);
  expect(screen.queryByRole('timer')).toBeNull();
  await advance(90_000);
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);

  dialog.remove();
  await advance(1_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(9_000);
  expect(reload).not.toHaveBeenCalled();
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-15: the Production Board kiosk reloads itself after the 10 s countdown', async () => {
  at('/production-board/kiosk');
  render(<Harness initial="outdated" />);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(9_000);
  expect(reload).not.toHaveBeenCalled();
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-16: a recent marker or an unusable sessionStorage blocks the automatic reload; the button still works', async () => {
  at('/production-board/kiosk');
  window.sessionStorage.setItem(
    RELEASE_AUTO_RELOAD_STORAGE_KEY,
    String(Date.now() - 5 * 60_000),
  );
  const first = render(<Harness initial="outdated" />);
  await advance(4 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(screen.queryByRole('timer')).toBeNull();
  first.unmount();

  window.sessionStorage.clear();
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('storage denied');
  });
  render(<Harness initial="outdated" />);
  await advance(2 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);
  expect(screen.queryByRole('timer')).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Reload page' }));
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-16: a marker that cannot be written blocks the automatic reload and ends the countdown', async () => {
  at('/production-board/kiosk');
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  render(<Harness initial="outdated" />);
  await advance(2 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(1);
  expect(screen.queryByRole('timer')).toBeNull();
});

test('FR-17: returning to connected ends the countdown; the next outdated period counts down from 10 s again', async () => {
  at('/production-board/kiosk');
  render(<Harness initial="outdated" />);
  await advance(5_000);
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 5 s');
  act(() => setStatus('connected'));
  expect(screen.queryByText(NOTICE)).toBeNull();
  await advance(40_000);
  expect(reload).not.toHaveBeenCalled();

  act(() => setStatus('outdated'));
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading in 10 s');
  await advance(9_000);
  expect(reload).not.toHaveBeenCalled();
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-18: the connectivity chip reads UPDATED while outdated', () => {
  at('/management/work-orders');
  render(<Harness initial="outdated" />);
  const chip = screen.getByRole('status', {
    name: 'Backend connection: UPDATED',
  });
  expect(chip).toHaveTextContent('UPDATED');
  expect(chip).toHaveClass('connchip', 'outdated');
});

test('FR-28: the reload waits for a served shell of the server release; failed checks write no marker', async () => {
  at('/production-board/kiosk');
  shellAnswer = () => shell('v1.0.0');
  render(<Harness initial="outdated" />);
  await advance(12_000);
  expect(shellChecks()).toBeGreaterThan(0);
  expect(reload).not.toHaveBeenCalled();
  // The countdown has run out: the page waits for the new shell.
  expect(screen.getByRole('timer')).toHaveTextContent('Reloading…');

  shellAnswer = () =>
    new Response(JSON.stringify({ detail: 'down' }), { status: 502 });
  await advance(3_000);
  expect(reload).not.toHaveBeenCalled();
  expect(
    window.sessionStorage.getItem(RELEASE_AUTO_RELOAD_STORAGE_KEY),
  ).toBeNull();

  shellAnswer = () => shell(SERVER_RELEASE);
  await advance(1_000);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-29: noDialogOpen sees every dialog form and ignores a closed <dialog>', () => {
  const doc = document.implementation.createHTMLDocument('t');
  expect(noDialogOpen(doc)).toBe(true);

  const plain = doc.createElement('div');
  plain.setAttribute('role', 'dialog');
  doc.body.appendChild(plain);
  expect(noDialogOpen(doc)).toBe(false);
  plain.remove();

  const alert = doc.createElement('div');
  alert.setAttribute('role', 'alertdialog');
  doc.body.appendChild(alert);
  expect(noDialogOpen(doc)).toBe(false);
  alert.remove();

  const native = doc.createElement('dialog');
  doc.body.appendChild(native);
  expect(noDialogOpen(doc)).toBe(true);
  native.setAttribute('open', '');
  expect(noDialogOpen(doc)).toBe(false);
});

/** The Worker sign-in modal, write-blocked like the station renders it. */
function SignIn() {
  const { status } = useConnectivity();
  return (
    <WorkerSignInDialog
      stationId="S1"
      expired={false}
      writeBlocked={status !== 'connected'}
      ticket={() => 0}
      onSignedIn={() => undefined}
      onModeChanged={() => undefined}
    />
  );
}

test('FR-25: a station with only the Worker sign-in modal open never reloads itself; the modal offers Reload page', async () => {
  at('/scan-station/S1/production');
  render(
    <Harness initial="outdated">
      <SignIn />
    </Harness>,
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Worker sign-in required',
  });
  expect(within(dialog).getByLabelText('Scan Worker badge')).toHaveAttribute(
    'placeholder',
    'PartFlow was updated — reload to continue scanning',
  );
  await advance(10 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(shellChecks()).toBe(0);
  expect(screen.queryByText(/Reloading/)).toBeNull();
  expect(
    within(dialog).getByRole('button', { name: 'Reload page' }),
  ).toBeEnabled();
});

/** The enrollment form, write-blocked like the station renders it. */
function Enrollment({ variant }: { variant: 'dialog' | 'panel' }) {
  const { status } = useConnectivity();
  return (
    <StationEnrollment
      stationId="S1"
      variant={variant}
      reason="revoked"
      pendingUnknownOutcome={false}
      writeBlocked={status !== 'connected'}
      onEnrolled={() => undefined}
    />
  );
}

test('FR-26: a station with only the enrollment dialog open never reloads itself; the dialog offers Reload page', async () => {
  at('/scan-station/S1/production');
  render(
    <Harness initial="outdated">
      <Enrollment variant="dialog" />
    </Harness>,
  );
  const dialog = screen.getByRole('dialog', { name: 'Enroll this device' });
  expect(
    within(dialog).getByText(
      'PartFlow was updated — reload this page to enroll the device.',
    ),
  ).toBeInTheDocument();
  await advance(10 * 60_000);
  expect(reload).not.toHaveBeenCalled();
  expect(screen.queryByText(/Reloading/)).toBeNull();
  // The in-dialog control sits before the disabled Enroll device.
  const buttons = within(dialog).getAllByRole('button');
  const reloadAt = buttons.findIndex((b) => b.textContent === 'Reload page');
  expect(reloadAt).toBeGreaterThanOrEqual(0);
  expect(buttons[reloadAt + 1]).toHaveTextContent('Enroll device');
  expect(buttons[reloadAt + 1]).toBeDisabled();
});

test('FR-27: the sign-in modal Reload page shows only while outdated, waits for a running check, never takes focus and reloads once', async () => {
  at('/scan-station/S1');
  render(
    <Harness initial="connected">
      <SignIn />
    </Harness>,
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Worker sign-in required',
  });
  const field = within(dialog).getByLabelText('Scan Worker badge');
  expect(field).toHaveFocus();
  expect(within(dialog).queryByRole('button', { name: 'Reload page' })).toBe(
    null,
  );

  // A badge check is in flight (the request never answers here).
  fireEvent.change(field, { target: { value: 'B-100' } });
  fireEvent.keyDown(field, { key: 'Enter' });
  act(() => setStatus('outdated'));
  const button = within(dialog).getByRole('button', { name: 'Reload page' });
  expect(button).toBeDisabled();
  expect(button).not.toHaveFocus();
  cleanup();

  render(
    <Harness initial="outdated">
      <SignIn />
    </Harness>,
  );
  const idle = screen.getByRole('dialog', { name: 'Worker sign-in required' });
  const reloadButton = within(idle).getByRole('button', {
    name: 'Reload page',
  });
  expect(reloadButton).not.toHaveFocus();
  fireEvent.click(reloadButton);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('FR-27: the enrollment Reload page (both forms) shows only while outdated, waits for enrolling, keeps focus on the code field and reloads once', async () => {
  for (const variant of ['dialog', 'panel'] as const) {
    reload.mockClear();
    render(
      <Harness initial="connected">
        <Enrollment variant={variant} />
      </Harness>,
    );
    const form =
      variant === 'dialog'
        ? screen.getByRole('dialog', { name: 'Enroll this device' })
        : screen.getByRole('region', { name: 'Enroll this device' });
    const code = within(form).getByRole('textbox', {
      name: 'Enrollment code',
    });
    expect(code).toHaveFocus();
    expect(within(form).queryByRole('button', { name: 'Reload page' })).toBe(
      null,
    );

    act(() => setStatus('outdated'));
    const button = within(form).getByRole('button', { name: 'Reload page' });
    expect(button).toBeEnabled();
    expect(code).toHaveFocus();
    fireEvent.click(button);
    expect(reload).toHaveBeenCalledTimes(1);

    // An enrollment in flight (connected when it was sent).
    act(() => setStatus('connected'));
    fireEvent.change(code, { target: { value: 'ABCDE-12345' } });
    fireEvent.click(
      within(form).getByRole('button', { name: 'Enroll device' }),
    );
    act(() => setStatus('outdated'));
    expect(
      within(form).getByRole('button', { name: 'Reload page' }),
    ).toBeDisabled();
    cleanup();
  }
});

test('FR-33: a badge check refused for another release shows the refusal alone and re-reads nothing', async () => {
  const detail =
    'PartFlow was updated while this page was open, so this request was refused and nothing was changed by it. Reload the page to continue. If an earlier attempt had no answer, check whether it was recorded before repeating it.';
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ detail, release_mismatch: true }), {
        status: 409,
      }),
    ),
  );
  const onModeChanged = vi.fn();
  at('/scan-station/S1');
  render(
    <Harness initial="connected">
      <WorkerSignInDialog
        stationId="S1"
        expired={false}
        writeBlocked={false}
        ticket={() => 0}
        onSignedIn={() => undefined}
        onModeChanged={onModeChanged}
      />
    </Harness>,
  );
  const field = screen.getByLabelText('Scan Worker badge');
  fireEvent.change(field, { target: { value: 'B-100' } });
  await act(async () => {
    fireEvent.keyDown(field, { key: 'Enter' });
  });
  const dialog = screen.getByRole('dialog', {
    name: 'Worker sign-in required',
  });
  expect(dialog).toHaveTextContent(`Badge could not be checked — ${detail}`);
  expect(dialog).not.toHaveTextContent('Nothing was recorded.');
  expect(onModeChanged).not.toHaveBeenCalled();
});
