import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { AllocationScope } from '../api/management-allocations';
import { AllocationAdjustmentDialog } from './AllocationAdjustmentDialog';
import type { AllocationAdjustmentStart } from './AllocationAdjustmentDialog';

// The `Adjust WO Allocation` dialog (Phase 14 slice 5, GUI_DESIGN
// §11.6) against a fake of the wire contract: the routine allocation
// never beyond the remaining demand, the explicit beyond-demand step
// with its warning and mandatory reason, the reversal with its
// consequence, ONE key per intent, the unknown-outcome lock and the
// explicit refusal that ends it.

const STOCKROOM_ROW = {
  allocation_id: 31,
  quantity: 6,
  source: 'STOCKROOM',
  is_manual_override: false,
  exceeds_demand: false,
  allocation_reason: null,
  station_id: 'STOCK-1',
  allocated_at: '2026-08-02T08:00:00Z',
  actor_user: null,
};

const CORRECTION_ROW = {
  allocation_id: 32,
  quantity: 2,
  source: 'MANAGEMENT',
  is_manual_override: true,
  exceeds_demand: true,
  allocation_reason: 'customer accepted overage',
  station_id: null,
  allocated_at: '2026-08-03T08:00:00Z',
  actor_user: { id: 90, display_name: 'Mia Manager', avatar_updated_at: null },
};

function lineWire(extra: Record<string, unknown> = {}) {
  return {
    work_order_id: 1,
    work_order_number: '007201',
    work_order_completed: false,
    received_date: '2026-08-01',
    work_order_demand_id: 7,
    request_type: 'NEW',
    due_date: '2026-09-10',
    priority_rank: null,
    requested_quantity: 10,
    allocated_quantity: 6,
    remaining_shortage: 4,
    beyond_demand_quantity: 0,
    active_allocations: [STOCKROOM_ROW],
    ...extra,
  };
}

/** An open line: 6 of 10 allocated, 6 pcs available in stock. */
function openContext(available = 6) {
  return {
    part_number: 'A-100',
    stocked_quantity: 10 + available,
    active_allocated_quantity: 10,
    available_stocked_quantity: available,
    lines: [lineWire()],
  };
}

/** A completed Work Order's line: 10 of 10 allocated, 2 pcs in stock. */
function completedContext(
  rows: unknown[] = [{ ...STOCKROOM_ROW, quantity: 10 }],
) {
  return {
    part_number: 'A-100',
    stocked_quantity: 12,
    active_allocated_quantity: 10,
    available_stocked_quantity: 2,
    lines: [
      lineWire({
        work_order_completed: true,
        allocated_quantity: 10,
        remaining_shortage: 0,
        active_allocations: rows,
      }),
    ],
  };
}

let contextWire: unknown;
let contextGets: number;
let posts: { url: string; body: Record<string, unknown> }[];
let answers: (() => Response | Promise<Response>)[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function resultWire(kind: string, extra: Record<string, unknown> = {}) {
  return {
    kind,
    part_number: 'A-100',
    allocation_quantity: 2,
    rows: [],
    completed_work_order_ids: [],
    reopened_work_order_ids: [],
    device_event_id: 'evt',
    ...extra,
  };
}

const NETWORK_FAILURE = (): Response => {
  throw new TypeError('Failed to fetch');
};

beforeEach(() => {
  contextWire = openContext();
  contextGets = 0;
  posts = [];
  answers = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/allocations/management/context')) {
        contextGets += 1;
        return json(contextWire);
      }
      posts.push({
        url,
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      const next = answers.shift();
      return next ? next() : json({ detail: 'Unanswered.' }, 500);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderDialog(
  start: AllocationAdjustmentStart = {
    step: 'allocate',
    workOrderDemandId: 7,
  },
  options: { scope?: AllocationScope; writeBlocked?: boolean } = {},
) {
  const onClose = vi.fn();
  const onCommitted = vi.fn();
  render(
    <AllocationAdjustmentDialog
      scope={options.scope ?? { workOrderDemandId: 7 }}
      start={start}
      writeBlocked={options.writeBlocked ?? false}
      onClose={onClose}
      onCommitted={onCommitted}
    />,
  );
  return { onClose, onCommitted };
}

function dialog() {
  return screen.getByRole('dialog');
}

function qtyField() {
  return screen.getByLabelText(
    'Quantity to allocate (pcs)',
  ) as HTMLInputElement;
}

function keys(): unknown[] {
  return posts.map((post) => post.body.device_event_id);
}

test('FC-3: Allocate from stock offers the routine limit and refuses beyond it before anything travels', async () => {
  const { onCommitted } = renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  expect(
    screen.getByRole('heading', { name: 'Allocate from stock' }),
  ).toBeInTheDocument();
  expect(qtyField().value).toBe('4');
  await waitFor(() => expect(document.activeElement).toBe(qtyField()));
  const facts = dialog().querySelector('.aad-facts') as HTMLElement;
  expect(facts).toHaveTextContent('Still needed4 pcs');
  expect(facts).toHaveTextContent('Available in stock6 pcs');
  const allocate = screen.getByRole('button', { name: 'Allocate' });

  fireEvent.change(qtyField(), { target: { value: '5' } });
  expect(
    screen.getByText(
      'Only 4 pcs are still needed on this line. To allocate more, use Allocate beyond demand….',
    ),
  ).toBeInTheDocument();
  expect(allocate).toBeDisabled();
  fireEvent.change(qtyField(), { target: { value: 'x' } });
  expect(
    screen.getByText('Enter a whole number of pieces greater than 0.'),
  ).toBeInTheDocument();
  expect(allocate).toBeDisabled();

  fireEvent.change(qtyField(), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Note (optional)'), {
    target: { value: 'left for later' },
  });
  answers.push(() =>
    json(
      resultWire('ALLOCATE', {
        allocation_quantity: 3,
        completed_work_order_ids: [1],
      }),
      201,
    ),
  );
  fireEvent.click(allocate);
  await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1));
  expect(posts).toEqual([
    {
      url: '/api/allocations/management',
      body: {
        part_number: 'A-100',
        allocation_quantity: 3,
        lines: [{ work_order_demand_id: 7, quantity: 3 }],
        reason: 'left for later',
        device_event_id: expect.any(String),
      },
    },
  ]);
  expect(onCommitted.mock.calls[0][1]).toBe(
    '✓ 3 pcs of A-100 allocated to Work Order 007201. Work Order 007201 is complete.',
  );
});

test('FC-3: with no more stock than the line needs, no beyond-demand step is offered and the stock caps the entry', async () => {
  contextWire = openContext(3);
  renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  expect(qtyField().value).toBe('3');
  expect(
    screen.queryByRole('button', { name: 'Allocate beyond demand…' }),
  ).toBeNull();
  fireEvent.change(qtyField(), { target: { value: '4' } });
  expect(
    screen.getByText('Only 3 pcs are available in stock.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Allocate' })).toBeDisabled();
});

test('FC-3: no write while writes are blocked', async () => {
  renderDialog(undefined, { writeBlocked: true });
  await screen.findByLabelText('Quantity to allocate (pcs)');
  expect(screen.getByRole('button', { name: 'Allocate' })).toBeDisabled();
});

test('FC-4: a fully allocated line leads to the explicit beyond-demand step with its warning and mandatory reason', async () => {
  contextWire = completedContext();
  const { onCommitted } = renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  expect(
    screen.getByText(
      'This demand line is fully allocated. Allocating more is a correction — use Allocate beyond demand….',
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Allocate' })).toBeDisabled();

  fireEvent.click(
    screen.getByRole('button', { name: 'Allocate beyond demand…' }),
  );
  expect(
    screen.getByRole('heading', { name: 'Allocate beyond demand' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Correction beyond demand' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'This allocates more than the Work Order Demand requests. Use it only to record a correction — routine allocation never exceeds the remaining demand. It is recorded with your name and the reason, and it can be reversed.',
    ),
  ).toBeInTheDocument();
  expect(qtyField().value).toBe('1');
  expect(document.activeElement).toBe(qtyField());
  expect(
    screen.getByText(
      'After this correction: 11 of 10 pcs allocated (1 pcs beyond demand).',
    ),
  ).toBeInTheDocument();
  fireEvent.change(qtyField(), { target: { value: '2' } });
  expect(
    screen.getByText(
      'After this correction: 12 of 10 pcs allocated (2 pcs beyond demand).',
    ),
  ).toBeInTheDocument();

  const record = screen.getByRole('button', { name: 'Record correction' });
  fireEvent.click(record);
  expect(
    screen.getByText('Enter the reason for this correction.'),
  ).toBeInTheDocument();
  expect(posts).toEqual([]);

  const reason = screen.getByLabelText('Reason (required)');
  fireEvent.change(reason, { target: { value: 'customer accepted overage' } });

  // The server does not answer: the outcome is unknown and locked.
  answers.push(NETWORK_FAILURE);
  fireEvent.click(record);
  expect(
    await screen.findByText(
      'The PartFlow server did not answer, so this may or may not be recorded. Submit again to finish — PartFlow records it only once.',
    ),
  ).toBeInTheDocument();
  expect(qtyField()).toHaveAttribute('readonly');
  expect(reason).toHaveAttribute('readonly');
  expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
  expect(record).toBeEnabled();

  // A resubmit refused because the sign-in ended keeps everything.
  const A1 =
    'You are not signed in, or your sign-in has ended. Sign in to continue.';
  answers.push(() => json({ detail: A1, authentication_required: true }, 401));
  fireEvent.click(record);
  expect(
    await screen.findByText(
      `${A1} Sign in again, then submit again. PartFlow records this only once.`,
    ),
  ).toBeInTheDocument();

  // A missing permission after an unknown outcome never says
  // "nothing was recorded".
  const A2 = 'You do not have permission to do this.';
  answers.push(() => json({ detail: A2, permission_denied: true }, 403));
  fireEvent.click(record);
  expect(
    await screen.findByText(
      `${A2} It may already be recorded; close this dialog and open it again to check.`,
    ),
  ).toBeInTheDocument();

  // Another user's request id: the detail, fresh figures, same key.
  const R1 =
    'This request was already recorded by another user. Nothing more was recorded — reload to see the current state.';
  answers.push(() => json({ detail: R1, recorded_by_another_user: true }, 409));
  const before = contextGets;
  fireEvent.click(record);
  expect(await screen.findByText(R1)).toBeInTheDocument();
  await waitFor(() => expect(contextGets).toBe(before + 1));
  expect(qtyField()).toHaveAttribute('readonly');

  answers.push(() =>
    json(resultWire('ALLOCATE_BEYOND_DEMAND', { allocation_quantity: 2 })),
  );
  fireEvent.click(record);
  await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1));
  expect(new Set(keys()).size).toBe(1);
  expect(posts).toHaveLength(5);
  expect(posts[0]).toEqual({
    url: '/api/allocations/corrections',
    body: {
      part_number: 'A-100',
      work_order_demand_id: 7,
      quantity: 2,
      reason: 'customer accepted overage',
      device_event_id: expect.any(String),
    },
  });
  expect(onCommitted.mock.calls[0][1]).toBe(
    '✓ 2 pcs of A-100 allocated beyond demand to Work Order 007201 — correction recorded.',
  );
});

test('FC-4: a correction must exceed the remaining demand', async () => {
  renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  fireEvent.click(
    screen.getByRole('button', { name: 'Allocate beyond demand…' }),
  );
  expect(qtyField().value).toBe('5');
  fireEvent.change(qtyField(), { target: { value: '4' } });
  expect(
    screen.getByText(
      'A beyond-demand correction must be more than the 4 pcs still needed on this line.',
    ),
  ).toBeInTheDocument();
  // Back returns to the Overview and its opener.
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(
    screen.getByRole('heading', { name: 'Adjust WO Allocation' }),
  ).toBeInTheDocument();
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Allocate from stock…' }),
  );
});

test('FC-5: a reverse start with one active allocation opens the Reverse step with its consequence', async () => {
  contextWire = completedContext();
  const { onCommitted } = renderDialog({
    step: 'reverse',
    workOrderDemandId: 7,
  });
  const reason = await screen.findByLabelText('Reason (required)');
  expect(
    screen.getByRole('heading', { name: 'Reverse allocation' }),
  ).toBeInTheDocument();
  await waitFor(() => expect(document.activeElement).toBe(reason));
  expect(dialog()).toHaveTextContent(
    '10 pcs allocated to Work Order 007201 on',
  );
  expect(dialog()).toHaveTextContent('(Stockroom)');
  expect(dialog()).toHaveTextContent('10 pcs return to available stock.');
  expect(dialog()).toHaveTextContent('Work Order 007201 becomes Open again.');

  const reverse = screen.getByRole('button', { name: 'Reverse allocation' });
  fireEvent.click(reverse);
  expect(
    screen.getByText('Enter the reason for this reversal.'),
  ).toBeInTheDocument();
  expect(posts).toEqual([]);

  fireEvent.change(reason, { target: { value: 'counted twice' } });
  answers.push(() =>
    json(
      resultWire('REVERSE_ALLOCATION', {
        allocation_quantity: 10,
        reopened_work_order_ids: [1],
      }),
      201,
    ),
  );
  fireEvent.click(reverse);
  await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1));
  expect(posts).toEqual([
    {
      url: '/api/allocations/31/reversals',
      body: { reason: 'counted twice', device_event_id: expect.any(String) },
    },
  ]);
  expect(onCommitted.mock.calls[0][1]).toBe(
    '✓ Allocation of 10 pcs reversed — returned to available stock. Work Order 007201 reopened.',
  );
});

test('FC-5: a reversal that keeps the Work Order complete names no reopen; two allocations open the Overview of the line', async () => {
  const overAllocated = completedContext([
    { ...STOCKROOM_ROW, quantity: 10 },
    CORRECTION_ROW,
  ]);
  overAllocated.lines[0] = {
    ...overAllocated.lines[0],
    allocated_quantity: 12,
    beyond_demand_quantity: 2,
  };
  contextWire = overAllocated;
  renderDialog({ step: 'reverse', workOrderDemandId: 7 });
  const reverseCorrection = await screen.findByRole('button', {
    name: 'Reverse allocation of 2 pcs to Work Order 007201',
  });
  expect(
    screen.getByRole('heading', { name: 'Adjust WO Allocation' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', {
      name: 'Reverse allocation of 10 pcs to Work Order 007201',
    }),
  ).toBeInTheDocument();
  // The correction entry: marked, with the User who recorded it.
  const entry = reverseCorrection.closest('li') as HTMLElement;
  expect(entry).toHaveTextContent('2 pcs · Management ·');
  expect(entry).toHaveTextContent('· beyond demand');
  expect(entry).toHaveTextContent('Mia Manager');
  expect(entry).toHaveTextContent('· reason: customer accepted overage');
  expect(entry.querySelector('.worker-avatar')).not.toBeNull();

  fireEvent.click(reverseCorrection);
  await screen.findByLabelText('Reason (required)');
  expect(dialog()).toHaveTextContent('2 pcs return to available stock.');
  expect(dialog()).not.toHaveTextContent('becomes Open again');
});

test('FC-6: an explicit refusal shows the detail, re-reads the figures and a changed intent travels under a new key', async () => {
  renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  const C52 =
    "Only 2 pcs of Part Number 'A-100' are available in stock (12 stocked, 10 already allocated); 3 pcs cannot be allocated. Nothing was allocated.";
  answers.push(() => json({ detail: C52 }, 409));
  fireEvent.change(qtyField(), { target: { value: '3' } });
  const before = contextGets;
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  expect(await screen.findByText(C52)).toBeInTheDocument();
  await waitFor(() => expect(contextGets).toBe(before + 1));

  fireEvent.change(qtyField(), { target: { value: '2' } });
  answers.push(() => json(resultWire('ALLOCATE'), 201));
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body.device_event_id).not.toBe(posts[0].body.device_event_id);
});

/** An answer the test settles later (a slow server). */
function pendingAnswer(): {
  resolve: (response: Response) => void;
  reject: (error: unknown) => void;
} {
  const handle: {
    resolve: (response: Response) => void;
    reject: (error: unknown) => void;
  } = { resolve: () => {}, reject: () => {} };
  answers.push(
    () =>
      new Promise<Response>((resolve, reject) => {
        handle.resolve = resolve;
        handle.reject = reject;
      }),
  );
  return handle;
}

test('FC-6: an in-flight correction locks the inputs and the steps, so its resubmit after an unknown outcome keeps its key', async () => {
  contextWire = completedContext();
  renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  fireEvent.click(
    screen.getByRole('button', { name: 'Allocate beyond demand…' }),
  );
  const reason = screen.getByLabelText('Reason (required)');
  fireEvent.change(reason, { target: { value: 'customer accepted overage' } });
  const slow = pendingAnswer();
  fireEvent.click(screen.getByRole('button', { name: 'Record correction' }));
  await waitFor(() => expect(posts).toHaveLength(1));

  // While the request is in flight nothing may change the intent.
  expect(qtyField()).toHaveAttribute('readonly');
  expect(reason).toHaveAttribute('readonly');
  expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
  fireEvent.change(reason, { target: { value: 'edited while sending' } });
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(
    screen.getByRole('heading', { name: 'Allocate beyond demand' }),
  ).toBeInTheDocument();
  expect(reason).toHaveValue('customer accepted overage');

  await act(async () => {
    slow.reject(new TypeError('Failed to fetch'));
  });
  await screen.findByText(/did not answer/);
  answers.push(() => json(resultWire('ALLOCATE_BEYOND_DEMAND'), 201));
  fireEvent.click(screen.getByRole('button', { name: 'Record correction' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body.device_event_id).toBe(posts[0].body.device_event_id);
  expect(posts[1].body.reason).toBe('customer accepted overage');
});

test('FC-6: a request still running when its dialog closed never closes a dialog opened since', async () => {
  const committed = vi.fn();
  const closed = vi.fn();
  function Host() {
    const [opening, setOpening] = useState<number | null>(1);
    return (
      <>
        <button onClick={() => setOpening((n) => (n ?? 0) + 1)}>Reopen</button>
        {opening !== null ? (
          <AllocationAdjustmentDialog
            key={opening}
            scope={{ workOrderDemandId: 7 }}
            start={{ step: 'allocate', workOrderDemandId: 7 }}
            writeBlocked={false}
            onClose={(outcomeUnknown) => {
              closed(outcomeUnknown);
              setOpening(null);
            }}
            onCommitted={(_result, notice) => {
              committed(notice);
              setOpening(null);
            }}
          />
        ) : null}
      </>
    );
  }
  render(<Host />);
  await screen.findByLabelText('Quantity to allocate (pcs)');
  const slow = pendingAnswer();
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(closed).toHaveBeenCalledWith(true);
  expect(screen.queryByRole('dialog')).toBeNull();

  // A second intent in a new dialog ends with an unknown outcome.
  fireEvent.click(screen.getByRole('button', { name: 'Reopen' }));
  await screen.findByLabelText('Quantity to allocate (pcs)');
  answers.push(NETWORK_FAILURE);
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await screen.findByText(/did not answer/);

  // The first request answers late: the second dialog stays open.
  await act(async () => {
    slow.resolve(json(resultWire('ALLOCATE'), 201));
  });
  expect(committed).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.getByText(/did not answer/)).toBeInTheDocument();
  expect(qtyField()).toHaveAttribute('readonly');
});

test('FC-6: closing while the outcome is unknown tells the host', async () => {
  const { onClose } = renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  answers.push(NETWORK_FAILURE);
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await screen.findByText(/did not answer/);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(onClose).toHaveBeenCalledWith(true);
});

test('FC-6: an explicit refusal of the resubmit ends the unknown outcome; another user’s request id keeps it', async () => {
  const { onClose } = renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  answers.push(NETWORK_FAILURE);
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await screen.findByText(/did not answer/);
  expect(qtyField()).toHaveAttribute('readonly');

  const C52 =
    "Only 2 pcs of Part Number 'A-100' are available in stock (12 stocked, 10 already allocated); 4 pcs cannot be allocated. Nothing was allocated.";
  answers.push(() => json({ detail: C52 }, 409));
  const before = contextGets;
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  expect(await screen.findByText(C52)).toBeInTheDocument();
  await waitFor(() => expect(contextGets).toBe(before + 1));
  expect(qtyField()).not.toHaveAttribute('readonly');
  expect(posts[1].body.device_event_id).toBe(posts[0].body.device_event_id);

  fireEvent.change(qtyField(), { target: { value: '2' } });
  answers.push(() => json({ detail: C52 }, 409));
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await waitFor(() => expect(posts).toHaveLength(3));
  expect(posts[2].body.device_event_id).not.toBe(posts[0].body.device_event_id);
  await screen.findByText(C52);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(onClose).toHaveBeenCalledWith(false);
});

test('FC-6: another user’s request id after an unknown outcome keeps the lock', async () => {
  const { onClose } = renderDialog();
  await screen.findByLabelText('Quantity to allocate (pcs)');
  answers.push(NETWORK_FAILURE);
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  await screen.findByText(/did not answer/);
  const R1 =
    'This request was already recorded by another user. Nothing more was recorded — reload to see the current state.';
  answers.push(() => json({ detail: R1, recorded_by_another_user: true }, 409));
  fireEvent.click(screen.getByRole('button', { name: 'Allocate' }));
  expect(await screen.findByText(R1)).toBeInTheDocument();
  expect(qtyField()).toHaveAttribute('readonly');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(onClose).toHaveBeenCalledWith(true);
});

test('the PN Overview lists the open lines with their figures; no open demand points at Work Order Details', async () => {
  contextWire = {
    ...completedContext([{ ...STOCKROOM_ROW, quantity: 10 }, CORRECTION_ROW]),
    lines: [
      lineWire({
        allocated_quantity: 12,
        remaining_shortage: 0,
        beyond_demand_quantity: 2,
        active_allocations: [
          { ...STOCKROOM_ROW, quantity: 10 },
          CORRECTION_ROW,
        ],
      }),
      lineWire({
        work_order_id: 2,
        work_order_number: null,
        work_order_demand_id: 8,
        due_date: null,
        allocated_quantity: 0,
        remaining_shortage: 10,
        active_allocations: [],
      }),
    ],
  };
  renderDialog({ step: 'overview' }, { scope: { partNumber: 'A-100' } });
  const blocks = await screen.findAllByRole('button', {
    name: 'Allocate from stock…',
  });
  expect(blocks).toHaveLength(2);
  await waitFor(() => expect(document.activeElement).toBe(blocks[0]));
  expect(dialog()).toHaveTextContent(
    'A-100 · stocked 12 pcs · allocated 10 · available 2',
  );
  const first = blocks[0].closest('li') as HTMLElement;
  expect(first).toHaveTextContent('WO 007201 · due Sep 10, 2026');
  expect(first).toHaveTextContent(
    '12 of 10 pcs allocated · fully allocated · +2 pcs beyond demand',
  );
  const second = blocks[1].closest('li') as HTMLElement;
  expect(second).toHaveTextContent('WO — · due —');
  expect(second).toHaveTextContent(
    '0 of 10 pcs allocated · 10 pcs still needed',
  );
  expect(second).toHaveTextContent('No active allocation.');
  expect(screen.getByRole('button', { name: 'Close (Esc)' })).toBeEnabled();

  cleanup();
  contextWire = { ...openContext(), lines: [] };
  renderDialog({ step: 'overview' }, { scope: { partNumber: 'A-100' } });
  expect(
    await screen.findByText(
      "No open Work Order Demand for this PN. A completed Work Order's allocation is adjusted from its Work Order Details.",
    ),
  ).toBeInTheDocument();
  expect(within(dialog()).getAllByRole('button')).toHaveLength(1);
});

test('a context read that fails offers Retry', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json({ detail: 'The database is unavailable.' }, 500)),
  );
  renderDialog();
  expect(
    await screen.findByText('The allocation could not be loaded.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument();
});
