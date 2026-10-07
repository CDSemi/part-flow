import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import { EditAssignedRouteDialog } from './EditAssignedRouteDialog';

// Tracking → Corrections → Edit assigned Route (Phase 14 slice 6,
// GUI_DESIGN §7.2): the dialog against an in-memory fake of
// `GET /api/tracking/assigned-routes` and
// `POST /api/quantity-flows/{id}/route-adjustments` (plus the Areas,
// Operations and Machines listings) with the wire shapes, statuses and
// refusal flags of the backend contract.

const PN = '2027-60-8114-00';

const AREA = {
  1: { id: 1, name: 'Material', color: '#8d6e63', is_terminal: false },
  2: { id: 2, name: 'Lathe', color: '#1565c0', is_terminal: false },
  3: { id: 3, name: 'Mill', color: '#6a1b9a', is_terminal: false },
  4: { id: 4, name: 'Stockroom', color: '#2e7d32', is_terminal: true },
  5: { id: 5, name: 'Deburr', color: '#455a64', is_terminal: false },
} as const;

const OPERATION = {
  11: { id: 11, code: 'RCV', name: 'Receiving', is_external: false },
  21: { id: 21, code: 'TURN', name: 'Turning', is_external: false },
  31: { id: 31, code: 'MILL', name: 'Milling', is_external: false },
  41: { id: 41, code: 'STOCK', name: 'Stocking', is_external: false },
  51: { id: 51, code: 'DEB', name: 'Deburring', is_external: false },
} as const;

const AREA_OF_OPERATION: Record<number, keyof typeof AREA> = {
  11: 1,
  21: 2,
  31: 3,
  41: 4,
  51: 5,
};

function areaWire(id: keyof typeof AREA) {
  return {
    ...AREA[id],
    department_id: 1,
    barcode_value: `PF:AREA:${id}`,
    description: null,
    icon_url: null,
    is_active: true,
    worker_identification_mode: 'DISABLED',
    fixed_worker_id: null,
    worker_session_timeout_minutes: null,
  };
}

function operationWire(id: keyof typeof OPERATION) {
  return {
    ...OPERATION[id],
    area_id: AREA_OF_OPERATION[id],
    description: null,
    default_expected_duration: null,
    is_active: true,
  };
}

const MACHINE_WIRE = {
  id: 201,
  area_id: 2,
  name: 'Lathe 1',
  asset_tag: 'CD-201',
  barcode_value: 'PF:MACHINE:CD-201',
  description: null,
  manufacturer: null,
  model: null,
  serial_number: null,
  installed_on: null,
  notes: null,
  maintenance_since: null,
  maintenance_note: null,
  maintenance_expected_return: null,
  state_changed_at: '2030-07-01T00:00:00Z',
  retired_on: null,
  operational_state: 'IDLE',
  assigned_quantity: 0,
  assigned_lines: [],
};

function step(
  id: number,
  sequence: number,
  operationId: keyof typeof OPERATION,
  state: 'DONE' | 'CURRENT' | 'FUTURE',
  locked: boolean,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    sequence,
    area: AREA[AREA_OF_OPERATION[operationId]],
    operation: OPERATION[operationId],
    expected_duration: null,
    preferred_machine: null,
    instructions: null,
    state,
    locked,
    ...extra,
  };
}

/** QF-140 at step 2 of Material → Lathe → Mill → Stockroom. */
function flowAtStep2() {
  return {
    quantity_flow_id: 140,
    quantity: 6,
    position: {
      area: AREA[2],
      machine: { id: 201, name: 'Lathe 1' },
      operation: OPERATION[21],
      activity: null,
      state: 'MACHINE',
      since: '2030-07-22T11:20:00Z',
      expected_by: null,
    },
    off_route: false,
    source_template: { id: 7, name: 'Bracket std v3' },
    kept_through_sequence: 2,
    future_step_ids: [3, 4],
    steps: [
      step(1, 1, 11, 'DONE', true),
      step(2, 2, 21, 'CURRENT', true, { expected_duration: 'PT4H' }),
      step(3, 3, 31, 'FUTURE', false),
      step(4, 4, 41, 'FUTURE', false),
    ],
  };
}

/** QF-150: its arrival at step 2 was undone — step 2 stays (Recorded)
 * and is the expected next step; the tail starts at step 3. */
function flowWithRecordedStep() {
  return {
    quantity_flow_id: 150,
    quantity: 4,
    position: {
      area: AREA[1],
      machine: null,
      operation: OPERATION[11],
      activity: null,
      state: 'QUEUE',
      since: '2030-07-22T09:00:00Z',
      expected_by: null,
    },
    off_route: false,
    source_template: null,
    kept_through_sequence: 2,
    future_step_ids: [13],
    steps: [
      step(11, 1, 11, 'CURRENT', true),
      step(12, 2, 21, 'FUTURE', true),
      step(13, 3, 41, 'FUTURE', false),
    ],
  };
}

type Answer = Response | 'network' | Promise<Response>;

let flows: unknown[];
let routeAnswers: (() => Answer)[];
let postAnswers: ((body: Record<string, unknown>) => Answer)[];
let posts: { url: string; body: Record<string, unknown> }[];
let calls: string[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function refusal(status: number, detail: string, flags = {}): Response {
  return json({ detail, ...flags }, status);
}

function resultOf(body: Record<string, unknown>, flowId = 140) {
  return {
    device_event_id: body.device_event_id,
    quantity_flow_id: flowId,
    part_number: PN,
    assigned_route_id: 900,
    kept_through_sequence: 2,
    reason: body.reason,
    steps: [],
  };
}

function settle(answer: Answer): Promise<Response> {
  return answer === 'network'
    ? Promise.reject(new TypeError('Failed to fetch'))
    : Promise.resolve(answer);
}

beforeEach(() => {
  flows = [flowAtStep2()];
  routeAnswers = [];
  postAnswers = [];
  posts = [];
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url === '/api/areas') {
        return json(([1, 2, 3, 4, 5] as const).map(areaWire));
      }
      if (url === '/api/operations') {
        return json(([11, 21, 31, 41, 51] as const).map(operationWire));
      }
      if (url === '/api/machines') return json([MACHINE_WIRE]);
      if (url.startsWith('/api/tracking/assigned-routes')) {
        const next = routeAnswers.shift();
        return settle(next ? next() : json({ part_number: PN, flows }));
      }
      if (/^\/api\/quantity-flows\/\d+\/route-adjustments$/.test(url)) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        posts.push({ url, body });
        const next = postAnswers.shift();
        return settle(next ? next(body) : json(resultOf(body), 201));
      }
      return json({ detail: `unexpected ${url}` }, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderDialog(status: 'connected' | 'unavailable' = 'connected') {
  const onClose = vi.fn();
  render(
    <ConnectivityContext.Provider value={{ status, retry: () => {} }}>
      <EditAssignedRouteDialog pn={PN} onClose={onClose} />
    </ConnectivityContext.Provider>,
  );
  await act(async () => {});
  return onClose;
}

function dialog(): HTMLElement {
  return screen.getByRole('dialog', { name: `Edit assigned Route — ${PN}` });
}

function reviewButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Review adjustment' });
}

function setReason(text: string) {
  fireEvent.change(screen.getByLabelText('Reason (required)'), {
    target: { value: text },
  });
}

/** Replace the tail Mill → Stockroom by Deburr → Stockroom. */
function editTail() {
  fireEvent.change(screen.getByLabelText('Step 3 Area'), {
    target: { value: '5' },
  });
}

async function adjust() {
  fireEvent.click(reviewButton());
  const confirm = screen.getByRole('dialog', {
    name: 'Adjust the assigned route?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Adjust route' }),
  );
  await act(async () => {});
  await act(async () => {});
}

const UNKNOWN =
  'The server did not answer — the route may or may not have been adjusted. Use Retry to send the same adjustment again; PartFlow applies it only once.';

// ---------------------------------------------------------------------------
// FE-2: dialog states
// ---------------------------------------------------------------------------

test('FE-2: the dialog reads the PN’s assigned routes, showing the loading state first', async () => {
  let release: (response: Response) => void = () => {};
  routeAnswers.push(
    () => new Promise<Response>((resolve) => (release = resolve)),
  );
  await renderDialog();
  expect(dialog()).toBeInTheDocument();
  expect(
    screen.getByRole('status', { name: 'Loading the assigned routes…' }),
  ).toBeInTheDocument();
  expect(calls).toContain(`/api/tracking/assigned-routes?part_number=${PN}`);

  await act(async () => release(json({ part_number: PN, flows })));
  expect(screen.queryByRole('status', { name: /Loading/ })).toBeNull();
  expect(screen.getByLabelText('Step 3 Area')).toBeInTheDocument();
});

test('FE-2: a failed read is the error state with Retry', async () => {
  routeAnswers.push(() => refusal(500, 'The server failed.'));
  await renderDialog();
  const alert = within(dialog()).getByRole('alert');
  expect(alert.textContent).toContain('The server failed.');
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(screen.getByLabelText('Step 3 Area')).toBeInTheDocument();
});

test('FE-2: without the connection the error names the missing connection', async () => {
  routeAnswers.push(() => 'network');
  await renderDialog('unavailable');
  expect(within(dialog()).getByRole('alert').textContent).toContain(
    'Changing a route needs the connection to the PartFlow server.',
  );
});

test('FE-2: a PN without an active Planned flow says so and only closes', async () => {
  flows = [];
  const onClose = await renderDialog();
  expect(dialog().textContent).toContain(
    `No active Quantity Flow of ${PN} follows a Planned Route. Only an active Planned flow has an assigned route to change.`,
  );
  expect(
    screen.queryByRole('button', { name: 'Review adjustment' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(onClose).toHaveBeenCalledWith({ changed: false, notice: null });
});

test('FE-2: one active Planned flow is preselected', async () => {
  await renderDialog();
  expect(screen.queryByRole('radiogroup')).toBeNull();
  expect(dialog().querySelector('.ear-oneflow')?.textContent).toMatch(
    /^QF-140 · 6 pcs · Lathe · Lathe 1 · .+ in Area · Bracket std v3$/,
  );
  expect(reviewButton()).toBeDisabled();
  // Focus starts on the first future-step control.
  expect(document.activeElement).toBe(screen.getByLabelText('Step 3 Area'));
});

test('FE-2: several flows need an explicit choice before the editor appears', async () => {
  flows = [flowWithRecordedStep(), flowAtStep2()];
  await renderDialog();
  const group = screen.getByRole('radiogroup', { name: 'Quantity Flow' });
  const options = within(group).getAllByRole('radio');
  expect(options).toHaveLength(2);
  expect(options.every((option) => !(option as HTMLInputElement).checked)).toBe(
    true,
  );
  const labels = Array.from(group.querySelectorAll('label'), (label) =>
    label.textContent?.trim(),
  );
  expect(labels[0]).toMatch(
    /^QF-150 · 4 pcs · Material · .+ in Area · Planned Route$/,
  );
  expect(labels[1]).toMatch(
    /^QF-140 · 6 pcs · Lathe · Lathe 1 · .+ in Area · Bracket std v3$/,
  );
  expect(dialog().textContent).toContain(
    `Choose the Quantity Flow whose route changes — other Quantity Flows of ${PN} keep their routes.`,
  );
  expect(screen.queryByLabelText('Step 3 Area')).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Review adjustment' }),
  ).toBeNull();
  expect(document.activeElement).toBe(options[0]);

  fireEvent.click(options[1]);
  expect(screen.getByLabelText('Step 3 Area')).toHaveValue('3');
  expect(reviewButton()).toBeDisabled();
  expect(
    calls.filter((url) => url.startsWith('/api/tracking/assigned-routes')),
  ).toEqual([`/api/tracking/assigned-routes?part_number=${PN}`]);
});

// ---------------------------------------------------------------------------
// FE-3: locked and future steps
// ---------------------------------------------------------------------------

test('FE-3: locked steps are read-only rows; the future steps continue the numbering', async () => {
  await renderDialog();
  const locked = dialog().querySelector('.ear-locked') as HTMLElement;
  const rows = Array.from(locked.querySelectorAll('li'));
  expect(rows.map((row) => row.textContent)).toEqual([
    '1. Material · Receiving · Est. —Done',
    '2. Lathe · Turning · Est. 4h 00mCurrent',
  ]);
  expect(locked.querySelectorAll('select, input, button')).toHaveLength(0);
  expect(dialog().textContent).toContain(
    'Steps the quantity has reached, or that its history records, stay as they are.',
  );
  expect(dialog().textContent).toContain(
    "The quantity's next on-route arrival is checked against the first step below.",
  );
  expect(screen.getByLabelText('Step 3 Area')).toHaveValue('3');
  expect(screen.getByLabelText('Step 4 Area')).toHaveValue('4');
  expect(screen.queryByLabelText('Step 2 Area')).toBeNull();

  // Removing down to zero future steps is allowed.
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 4' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 3' }));
  expect(screen.queryByLabelText('Step 3 Area')).toBeNull();
  expect(dialog().textContent).toContain(
    'No further steps — the route ends after step 2.',
  );
  // `+ Add step` starts in the last locked step's Area.
  fireEvent.click(screen.getByRole('button', { name: '+ Add step' }));
  expect(screen.getByLabelText('Step 3 Area')).toHaveValue('2');
  expect(screen.getByLabelText('Step 3 Operation')).toHaveValue('21');
  expect(
    screen.getByRole('button', { name: 'Remove step 3' }),
  ).not.toBeDisabled();
});

test('FE-3: a locked step an undone arrival recorded reads Recorded and is the next expected step', async () => {
  flows = [flowWithRecordedStep()];
  await renderDialog();
  const rows = Array.from(dialog().querySelectorAll('.ear-locked li'));
  expect(rows[1].textContent).toBe('2. Lathe · Turning · Est. —Recorded');
  expect(rows[1].querySelector('.ear-state')?.getAttribute('title')).toBe(
    'An undone arrival recorded this step, so it stays.',
  );
  expect(dialog().textContent).toContain(
    "The quantity's next on-route arrival is checked against step 2 (recorded above), then the steps that follow.",
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 3' }));
  expect(dialog().textContent).toContain('No further steps after step 2.');
  expect(dialog().textContent).not.toContain('the route ends after step 2');
});

test('FE-3: an off-route quantity says where it is', async () => {
  flows = [{ ...flowAtStep2(), off_route: true }];
  await renderDialog();
  expect(dialog().textContent).toContain(
    'This quantity is currently off its Planned Route (in Lathe).',
  );
});

// ---------------------------------------------------------------------------
// FE-4: Review adjustment gate and the unsaved marker
// ---------------------------------------------------------------------------

test('FE-4: Review adjustment needs a changed, valid tail and a reason; the marker follows the edits', async () => {
  await renderDialog();
  expect(reviewButton()).toBeDisabled();
  expect(dialog().textContent).not.toContain('● Unsaved changes');

  setReason('Mill is down for the week');
  expect(dialog().textContent).toContain('● Unsaved changes');
  // An unchanged tail is not an adjustment.
  expect(reviewButton()).toBeDisabled();

  editTail();
  expect(reviewButton()).toBeEnabled();

  fireEvent.change(screen.getByLabelText('Step 3 expected duration'), {
    target: { value: 'soon' },
  });
  expect(reviewButton()).toBeDisabled();
  expect(dialog().querySelector('.ear-problem')?.textContent).toBe(
    'Step 3: enter the estimated time like 45m, 4h or 2d 03h.',
  );
  fireEvent.change(screen.getByLabelText('Step 3 expected duration'), {
    target: { value: '2h' },
  });
  expect(dialog().querySelector('.ear-problem')).toBeNull();
  expect(reviewButton()).toBeEnabled();

  setReason('   ');
  expect(reviewButton()).toBeDisabled();

  // Undoing the edits and clearing the reason clears the marker.
  fireEvent.change(screen.getByLabelText('Step 3 expected duration'), {
    target: { value: '' },
  });
  fireEvent.change(screen.getByLabelText('Step 3 Area'), {
    target: { value: '3' },
  });
  setReason('');
  expect(dialog().textContent).not.toContain('● Unsaved changes');
});

test('FE-4: Review adjustment stays disabled without the connection', async () => {
  await renderDialog('unavailable');
  editTail();
  setReason('Mill is down');
  expect(reviewButton()).toBeDisabled();
  expect(dialog().textContent).toContain(
    'Changing a route needs the connection to the PartFlow server.',
  );
});

// ---------------------------------------------------------------------------
// FE-5: review and the write
// ---------------------------------------------------------------------------

test('FE-5: the review shows the route before and after; Adjust route posts exactly the contract body', async () => {
  const onClose = await renderDialog();
  editTail();
  setReason('  Mill is down for the week  ');
  fireEvent.click(reviewButton());
  const confirm = screen.getByRole('dialog', {
    name: 'Adjust the assigned route?',
  });
  const lines = Array.from(
    confirm.querySelectorAll('.ear-review > div'),
    (line) => line.textContent,
  );
  expect(lines).toEqual([
    'QF-140 · steps after step 2',
    'Now: Mill → Stockroom',
    'New: Deburr → Stockroom',
    'Reason: Mill is down for the week',
  ]);
  // Keep editing returns to the editor, focus on Review adjustment.
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  );
  expect(
    screen.queryByRole('dialog', { name: 'Adjust the assigned route?' }),
  ).toBeNull();
  expect(document.activeElement).toBe(reviewButton());

  await adjust();
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toBe('/api/quantity-flows/140/route-adjustments');
  expect(Object.keys(posts[0].body).sort()).toEqual([
    'device_event_id',
    'expected_future_step_ids',
    'reason',
    'steps',
  ]);
  expect(posts[0].body.device_event_id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  expect(posts[0].body).toMatchObject({
    expected_future_step_ids: [3, 4],
    reason: 'Mill is down for the week',
    steps: [
      {
        area_id: 5,
        operation_id: 51,
        expected_duration: null,
        preferred_machine_id: null,
        instructions: null,
      },
      {
        area_id: 4,
        operation_id: 41,
        expected_duration: null,
        preferred_machine_id: null,
        instructions: null,
      },
    ],
  });
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledWith({
    changed: true,
    notice: '✓ Route adjusted for QF-140.',
  });
});

test('FE-5: removing every future step reads `no further steps` in the review and posts an empty tail', async () => {
  await renderDialog();
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 4' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 3' }));
  setReason('Finish at the Lathe');
  fireEvent.click(reviewButton());
  const confirm = screen.getByRole('dialog', {
    name: 'Adjust the assigned route?',
  });
  expect(confirm.textContent).toContain('New: no further steps');
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Adjust route' }),
  );
  await act(async () => {});
  expect(posts[0].body.steps).toEqual([]);
});

// ---------------------------------------------------------------------------
// FE-6: unknown outcome and the idempotency key
// ---------------------------------------------------------------------------

for (const [label, answer] of [
  ['a network error', () => 'network' as const],
  ['503', () => refusal(503, 'Service unavailable')],
  ['408', () => refusal(408, 'Request timeout')],
] as const) {
  test(`FE-6: ${label} is an unknown outcome — read-only editor, Retry resends the same request`, async () => {
    const onClose = await renderDialog();
    editTail();
    setReason('Mill is down');
    postAnswers.push(answer);
    await adjust();

    expect(within(dialog()).getByRole('alert').textContent).toBe(UNKNOWN);
    expect(screen.getByLabelText('Step 3 Area')).toBeDisabled();
    expect(screen.getByLabelText('Reason (required)')).toHaveAttribute(
      'readonly',
    );
    expect(screen.getByRole('button', { name: '+ Add step' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Review adjustment' }),
    ).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await act(async () => {});
    await act(async () => {});
    expect(posts).toHaveLength(2);
    expect(posts[1].body).toEqual(posts[0].body);
    expect(onClose).toHaveBeenCalledWith({
      changed: true,
      notice: '✓ Route adjusted for QF-140.',
    });
  });
}

test('FE-5 / FE-6: closing while the outcome is unknown reloads and leaves the status line', async () => {
  const onClose = await renderDialog();
  editTail();
  setReason('Mill is down');
  postAnswers.push(() => 'network');
  await adjust();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(onClose).toHaveBeenCalledWith({
    changed: true,
    notice:
      "The last route adjustment may have been applied. Check the Quantity Flow's route before trying again.",
  });
  // Never the discard question: the edits may have been applied.
  expect(
    screen.queryByRole('dialog', { name: 'Discard unsaved route changes?' }),
  ).toBeNull();
});

test('FE-6: after a definite refusal, a changed body gets a new device_event_id; the identical one keeps it', async () => {
  await renderDialog();
  editTail();
  setReason('Mill is down');
  postAnswers.push(() =>
    refusal(409, "Step 3: Area 'Deburr' is inactive. Choose an active Area."),
  );
  await adjust();
  expect(within(dialog()).getByRole('alert').textContent).toBe(
    "Step 3: Area 'Deburr' is inactive. Choose an active Area.",
  );
  // Inputs kept, still editable.
  expect(screen.getByLabelText('Step 3 Area')).toHaveValue('5');
  expect(screen.getByLabelText('Step 3 Area')).toBeEnabled();

  postAnswers.push(() => refusal(422, 'Step 3 needs an Operation.'));
  await adjust();
  expect(posts[1].body.device_event_id).toBe(posts[0].body.device_event_id);

  fireEvent.change(screen.getByLabelText('Step 3 Area'), {
    target: { value: '1' },
  });
  await adjust();
  expect(posts).toHaveLength(3);
  expect(posts[2].body.device_event_id).not.toBe(posts[0].body.device_event_id);
});

// ---------------------------------------------------------------------------
// FE-7: route changed, another user, sign-in and permission refusals
// ---------------------------------------------------------------------------

test('FE-7: a route changed meanwhile offers Reload route, which re-reads and resets the editor', async () => {
  const onClose = await renderDialog();
  editTail();
  setReason('Mill is down');
  const changed =
    'The route of Quantity Flow 140 changed since you opened it — the quantity moved on or the route was changed by someone else. Nothing was changed. Review the current route and make the change again.';
  postAnswers.push(() => refusal(409, changed, { route_changed: true }));
  await adjust();
  expect(within(dialog()).getByRole('alert').textContent).toBe(changed);
  expect(
    screen.queryByRole('button', { name: 'Review adjustment' }),
  ).toBeNull();

  // The quantity moved on: step 3 is now past.
  const moved = flowAtStep2();
  moved.kept_through_sequence = 3;
  moved.future_step_ids = [4];
  moved.steps = [
    step(1, 1, 11, 'DONE', true),
    step(2, 2, 21, 'DONE', true),
    step(3, 3, 31, 'CURRENT', true),
    step(4, 4, 41, 'FUTURE', false),
  ];
  flows = [moved];
  const reads = () =>
    calls.filter((url) => url.startsWith('/api/tracking/assigned-routes'))
      .length;
  const before = reads();
  fireEvent.click(screen.getByRole('button', { name: 'Reload route' }));
  await act(async () => {});
  await act(async () => {});
  expect(reads()).toBe(before + 1);
  expect(screen.queryByLabelText('Step 3 Area')).toBeNull();
  expect(screen.getByLabelText('Step 4 Area')).toHaveValue('4');
  expect(screen.getByLabelText('Reason (required)')).toHaveValue('');
  expect(within(dialog()).queryByRole('alert')).toBeNull();
  expect(dialog().textContent).not.toContain('● Unsaved changes');
  expect(onClose).not.toHaveBeenCalled();
});

test('FE-7: a request id recorded by another user is final — Close reloads', async () => {
  const onClose = await renderDialog();
  editTail();
  setReason('Mill is down');
  postAnswers.push(() =>
    refusal(409, 'This request was already recorded by another user.', {
      recorded_by_another_user: true,
    }),
  );
  await adjust();
  expect(within(dialog()).getByRole('alert').textContent).toBe(
    'This request was already recorded by another user.',
  );
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(onClose).toHaveBeenCalledWith({ changed: true, notice: null });
});

test('FE-7: a 401 or 403 on a first submission is an ordinary refusal', async () => {
  await renderDialog();
  editTail();
  setReason('Mill is down');
  postAnswers.push(() =>
    refusal(401, 'You are not signed in, or your sign-in has ended.', {
      authentication_required: true,
    }),
  );
  await adjust();
  expect(within(dialog()).getByRole('alert').textContent).toBe(
    'You are not signed in, or your sign-in has ended.',
  );
  expect(reviewButton()).toBeEnabled();

  postAnswers.push(() =>
    refusal(403, 'Your account does not have permission to do this.', {
      permission_denied: true,
    }),
  );
  await adjust();
  expect(within(dialog()).getByRole('alert').textContent).toBe(
    'Your account does not have permission to do this.',
  );
});

test('FE-7: a 401 or 403 on a Retry keeps the unknown outcome and never claims nothing was applied', async () => {
  await renderDialog();
  editTail();
  setReason('Mill is down');
  postAnswers.push(() => 'network');
  await adjust();

  postAnswers.push(() =>
    refusal(401, 'You are not signed in, or your sign-in has ended.', {
      authentication_required: true,
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  const signIn = within(dialog()).getByRole('alert').textContent ?? '';
  expect(signIn).toContain(
    'Sign in again, then use Retry. The adjustment is applied only once.',
  );
  expect(signIn).not.toMatch(/Nothing was/);
  expect(screen.getByLabelText('Step 3 Area')).toBeDisabled();

  postAnswers.push(() =>
    refusal(403, 'Your account does not have permission to do this.', {
      permission_denied: true,
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(within(dialog()).getByRole('alert').textContent).toContain(
    'The adjustment may already be applied; reload the route to check.',
  );
  expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  expect(posts.map((post) => post.body)).toEqual([
    posts[0].body,
    posts[0].body,
    posts[0].body,
  ]);
});

// ---------------------------------------------------------------------------
// FE-8: the dirty close guard
// ---------------------------------------------------------------------------

test('FE-8: Escape with edits asks before discarding them; nothing is written', async () => {
  const onClose = await renderDialog();
  editTail();
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  const discard = screen.getByRole('dialog', {
    name: 'Discard unsaved route changes?',
  });
  expect(discard.textContent).toContain(
    'The changes to this route have not been saved and will be lost.',
  );
  fireEvent.click(
    within(discard).getByRole('button', { name: 'Keep editing' }),
  );
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Step 3 Area')).toHaveValue('5');

  fireEvent.keyDown(dialog(), { key: 'Escape' });
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Discard unsaved route changes?' }),
    ).getByRole('button', { name: 'Discard changes' }),
  );
  expect(onClose).toHaveBeenCalledWith({ changed: false, notice: null });
  expect(posts).toHaveLength(0);
});

test('FE-8: without edits Escape closes at once', async () => {
  const onClose = await renderDialog();
  fireEvent.keyDown(dialog(), { key: 'Escape' });
  expect(onClose).toHaveBeenCalledWith({ changed: false, notice: null });
});
