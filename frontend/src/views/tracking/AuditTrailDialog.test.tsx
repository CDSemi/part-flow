import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { AuditTrailDialog } from './AuditTrailDialog';
import { timestamp } from './tracking-logic';

// FT-3: the PN audit trail dialog (GUI_DESIGN §7.4; Phase 14 slice 7)
// against a fake of `GET /api/tracking/audit-trail` with the exact wire
// contract: loading, entries newest first with their actor (avatar and
// name, the legacy text, or nothing), the deleted-line marker and the
// reason, the empty state, the first-page error with Retry, keyset
// paging with the count line from the latest page and the reopen
// sentence, the paging error, the stale-response guard and focus.

const PN = '2027-60-8114-00';

const NO_SUBJECT = {
  work_order_id: null,
  work_order_number: null,
  work_order_demand_id: null,
  demand_exists: null,
  quantity_flow_id: null,
};

function entryWire(id: number, extra: Record<string, unknown> = {}) {
  return {
    source: 'AUDIT',
    id,
    occurred_at: `2030-07-23T08:${String(10 + id).padStart(2, '0')}:00Z`,
    kind: 'PART_NUMBER_UPDATED',
    actor_user: null,
    legacy_actor: null,
    reason: null,
    subject: NO_SUBJECT,
    changes: [{ field: 'name', before: `old ${id}`, after: `new ${id}` }],
    priority: null,
    completion_trigger: null,
    allocation: null,
    route: null,
    ...extra,
  };
}

function page(
  entries: unknown[],
  total: number,
  next: { source: string; id: number } | null = null,
) {
  return {
    part_number: PN,
    entries,
    total,
    has_more: next !== null,
    next_before_source: next?.source ?? null,
    next_before_id: next?.id ?? null,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

/** The answer of one request: a response, a thrown transport error, or a
 * promise the test resolves later. */
type Answer = (url: URL) => Response | Promise<Response>;

let answer: Answer;
let requests: URL[];

beforeEach(() => {
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://partflow.test');
      requests.push(url);
      return answer(url);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderTrail(pn = PN) {
  const onClose = vi.fn();
  const view = render(<AuditTrailDialog pn={pn} onClose={onClose} />);
  await act(async () => {});
  return { onClose, view };
}

function entryTexts(): string[] {
  return Array.from(
    document.querySelectorAll('.tk-trail > li'),
    (li) => li.textContent ?? '',
  );
}

function countLine(): string {
  return document.querySelector('.tk-paging > span')?.textContent ?? '';
}

test('the first page loads on open and lists the entries newest first with their actor', async () => {
  let release: (response: Response) => void = () => {};
  answer = () =>
    new Promise<Response>((resolve) => {
      release = resolve;
    });
  render(<AuditTrailDialog pn={PN} onClose={() => {}} />);
  const dialog = screen.getByRole('dialog', { name: `Audit trail — ${PN}` });
  expect(
    within(dialog).getByRole('status', { name: 'Loading the audit trail…' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText(/Recorded changes to this PN/),
  ).toHaveTextContent(
    "Recorded changes to this PN's details, its Work Orders, Work Order Demand and priority, its allocation corrections and its assigned routes — newest first. Production Movements, Undo included, are in the Movement history; nothing here can be edited.",
  );
  expect(requests[0].searchParams.get('part_number')).toBe(PN);
  expect(requests[0].searchParams.has('before_source')).toBe(false);

  await act(async () => {
    release(
      json(
        page(
          [
            entryWire(3, {
              actor_user: {
                id: 90,
                display_name: 'Mia Manager',
                avatar_updated_at: '2030-07-01T00:00:00Z',
              },
            }),
            entryWire(2, {
              kind: 'DEMAND_UPDATED',
              legacy_actor: 'legacy-x',
              reason: 'customer request',
              subject: {
                work_order_id: 4,
                work_order_number: '007001',
                work_order_demand_id: 40,
                demand_exists: false,
                quantity_flow_id: null,
              },
              changes: [{ field: 'requested_quantity', before: 10, after: 12 }],
            }),
            entryWire(1),
          ],
          3,
        ),
      ),
    );
  });

  const texts = entryTexts();
  expect(texts).toHaveLength(3);
  expect(texts[0]).toBe(
    `${timestamp('2030-07-23T08:13:00Z')} · Part Number details edited · Mia ManagerName: old 3 → new 3`,
  );
  const avatar = document.querySelector<HTMLImageElement>(
    '.tk-trail > li .tk-trail-actor img',
  );
  expect(avatar?.getAttribute('src')).toBe(
    `/api/users/90/avatar?v=${encodeURIComponent('2030-07-01T00:00:00Z')}`,
  );
  expect(texts[1]).toBe(
    `${timestamp('2030-07-23T08:12:00Z')} · Demand line edited · WO 007001 · demand line (since deleted) · legacy-xRequested quantity: 10 pcs → 12 pcsReason: customer request`,
  );
  // No User and no legacy text: nothing is named.
  expect(texts[2]).toBe(
    `${timestamp('2030-07-23T08:11:00Z')} · Part Number details editedName: old 1 → new 1`,
  );
  expect(countLine()).toBe('Showing 3 of 3 entries');
  expect(
    screen.queryByRole('button', { name: 'Show older entries' }),
  ).toBeNull();
});

test('an empty trail says so', async () => {
  answer = () => json(page([], 0));
  await renderTrail();
  expect(
    screen.getByText(`No recorded changes for ${PN} yet.`),
  ).toBeInTheDocument();
  expect(document.querySelector('.tk-paging')).toBeNull();
});

test('a failed first page shows its error with Retry; Retry reads it again', async () => {
  let fail = true;
  answer = () => {
    if (fail) throw new TypeError('Failed to fetch');
    return json(page([entryWire(1)], 1));
  };
  await renderTrail();
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The PartFlow server could not be reached. Nothing was changed.',
  );
  // Retry again while still unreachable: focus moves to the new Retry
  // (the pressed one was replaced by the loading state).
  const firstRetry = within(dialog).getByRole('button', { name: 'Retry' });
  firstRetry.focus();
  fireEvent.click(firstRetry);
  await act(async () => {});
  expect(document.activeElement).toBe(
    within(dialog).getByRole('button', { name: 'Retry' }),
  );
  fail = false;
  within(dialog).getByRole('button', { name: 'Retry' }).focus();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
  await act(async () => {});
  expect(entryTexts()).toHaveLength(1);
  expect(requests).toHaveLength(3);
  // The page arrived: focus is back inside the dialog, never on the body.
  expect(document.activeElement).toBe(
    within(dialog).getByRole('button', { name: 'Close' }),
  );
});

test('Show older entries appends the next page below the cursor; the count follows the latest total', async () => {
  answer = (url) => {
    const before = url.searchParams.get('before_id');
    if (before === null) {
      return json(
        page([entryWire(5), entryWire(4)], 5, { source: 'AUDIT', id: 4 }),
      );
    }
    if (before === '4') {
      return json(
        page(
          [
            entryWire(3),
            entryWire(4),
            entryWire(31, {
              source: 'ALLOCATION',
              kind: 'ALLOCATED',
              changes: [],
              allocation: {
                quantity: 2,
                source: 'MANAGEMENT',
                is_manual_override: false,
                exceeds_demand: false,
                reverses_allocation_id: null,
                station_id: null,
              },
            }),
          ],
          6,
          { source: 'ALLOCATION', id: 31 },
        ),
      );
    }
    return json(page([entryWire(1)], 6));
  };
  await renderTrail();
  expect(countLine()).toBe('Showing 2 of 5 entries');
  const older = screen.getByRole('button', { name: 'Show older entries' });
  older.focus();
  fireEvent.click(older);
  await act(async () => {});
  expect(requests[1].searchParams.get('before_source')).toBe('AUDIT');
  expect(requests[1].searchParams.get('before_id')).toBe('4');
  // The repeated entry 4 is listed once (defensive de-duplication).
  expect(entryTexts()).toHaveLength(4);
  expect(entryTexts()[3]).toContain('Allocated from stock');
  expect(countLine()).toBe('Showing 4 of 6 entries');
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Show older entries' }),
  );

  fireEvent.click(screen.getByRole('button', { name: 'Show older entries' }));
  await act(async () => {});
  expect(requests[2].searchParams.get('before_source')).toBe('ALLOCATION');
  expect(requests[2].searchParams.get('before_id')).toBe('31');
  expect(entryTexts()).toHaveLength(5);
  // Fewer entries than the latest total: changes recorded while open.
  expect(countLine()).toBe(
    'Showing 5 of 6 entries — reopen the audit trail to include changes recorded since it was opened.',
  );
  expect(
    screen.queryByRole('button', { name: 'Show older entries' }),
  ).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Close' }),
  );
});

test('the last page that reaches the total reads as the plain count', async () => {
  answer = (url) =>
    url.searchParams.get('before_id') === null
      ? json(page([entryWire(2)], 2, { source: 'AUDIT', id: 2 }))
      : json(page([entryWire(1)], 2));
  await renderTrail();
  fireEvent.click(screen.getByRole('button', { name: 'Show older entries' }));
  await act(async () => {});
  expect(countLine()).toBe('Showing 2 of 2 entries');
});

test('a failed older page keeps the entries and the button for a retry', async () => {
  let fail = true;
  answer = (url) => {
    if (url.searchParams.get('before_id') === null) {
      return json(page([entryWire(2)], 2, { source: 'AUDIT', id: 2 }));
    }
    if (fail) throw new TypeError('Failed to fetch');
    return json(page([entryWire(1)], 2));
  };
  await renderTrail();
  const older = screen.getByRole('button', { name: 'Show older entries' });
  older.focus();
  fireEvent.click(older);
  await act(async () => {});
  expect(entryTexts()).toHaveLength(1);
  expect(document.querySelector('.tk-paging [role="alert"]')).toHaveTextContent(
    'The PartFlow server could not be reached. Nothing was changed.',
  );
  const retry = screen.getByRole('button', { name: 'Show older entries' });
  expect(retry).toBeEnabled();
  expect(document.activeElement).toBe(retry);
  fail = false;
  fireEvent.click(retry);
  await act(async () => {});
  expect(entryTexts()).toHaveLength(2);
  expect(document.querySelector('.tk-paging [role="alert"]')).toBeNull();
});

test('Show older entries keeps focus while its page loads and ignores a second press', async () => {
  let release: (response: Response) => void = () => {};
  answer = (url) =>
    url.searchParams.get('before_id') === null
      ? json(page([entryWire(2)], 2, { source: 'AUDIT', id: 2 }))
      : new Promise<Response>((resolve) => {
          release = resolve;
        });
  await renderTrail();
  const older = screen.getByRole('button', { name: 'Show older entries' });
  older.focus();
  fireEvent.click(older);
  await act(async () => {});
  const loading = screen.getByRole('button', { name: 'Loading…' });
  // Never `disabled`: a disabled button drops focus to the page body,
  // outside the dialog's Escape and Tab handling.
  expect(loading).toBeEnabled();
  expect(loading).toHaveAttribute('aria-disabled', 'true');
  expect(document.activeElement).toBe(loading);
  fireEvent.click(loading);
  expect(requests).toHaveLength(2);
  await act(async () => {
    release(json(page([entryWire(1)], 2)));
  });
  expect(entryTexts()).toHaveLength(2);
});

test('only the latest request presents its answer', async () => {
  let releaseFirst: (response: Response) => void = () => {};
  answer = (url) => {
    if (url.searchParams.get('part_number') === PN) {
      return new Promise<Response>((resolve) => {
        releaseFirst = resolve;
      });
    }
    return json(page([entryWire(7)], 1));
  };
  const { view } = await renderTrail();
  view.rerender(<AuditTrailDialog pn="142-260" onClose={() => {}} />);
  await act(async () => {});
  expect(entryTexts()).toHaveLength(1);
  await act(async () => {
    releaseFirst(json(page([entryWire(1), entryWire(2)], 2)));
  });
  expect(entryTexts()).toHaveLength(1);
  expect(entryTexts()[0]).toContain('new 7');
});

test('Close and Escape request the close', async () => {
  answer = () => json(page([], 0));
  const { onClose } = await renderTrail();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(2);
});
