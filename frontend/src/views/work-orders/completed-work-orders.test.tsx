import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';
import { RouterProvider } from '../../app/router-provider';
import { MOCK_COMPLETED_WORK_ORDERS } from '../../mocks/work-orders';
import { CompletedWorkOrdersUnavailable } from './CompletedWorkOrdersUnavailable';

// Completed Work Orders page tests (GUI_DESIGN §11.5). The completion
// workflow has no backend yet (completion = full allocation, Phase
// 10): production builds render the honest unavailable page, while
// development builds keep the approved visual preview of the history
// page — generated mock data behind the DEV-gated lazy boundary
// (verified by src/production-boundary.test.ts). These tests cover
// the preview's presentation contract (Done-range default, search
// with the all-history escape, the Due-outcome filter, Show-more
// paging, the read-only details dialog with the Done date), the
// active list's entry points, and the production placeholder.

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      // The real active Work Orders list loads from the API; one open
      // Work Order keeps these tests focused on the completed page.
      if (url === '/api/work-orders') {
        return Promise.resolve(
          new Response(
            JSON.stringify([
              {
                id: 1,
                work_order_number: '007201',
                received_date: '2026-08-01',
                due_date: null,
                status: 'OPEN',
                demand_line_count: 1,
                part_numbers: ['A-100'],
              },
            ]),
            { status: 200 },
          ),
        );
      }
      if (/\/api\/(work-orders|part-numbers|route-templates)/.test(url)) {
        return Promise.resolve(
          new Response(JSON.stringify([]), { status: 200 }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderCompleted() {
  window.history.replaceState({}, '', '/management/work-orders/completed');
  render(<App />);
  await screen.findByRole('heading', { name: 'Completed Work Orders' });
}

async function renderActiveList() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
}

function visibleRows(): HTMLTableRowElement[] {
  return Array.from(document.querySelectorAll('.cwo-list tbody tr.selrow'));
}

/* ============ The history page ============ */

test('the completed page defaults to the last 90 days ordered by Done descending', async () => {
  await renderCompleted();

  // The result summary restates the effective range — a bounded view
  // is never mistaken for the whole history.
  expect(
    screen.getByText(/completed Work Orders · last 90 days/),
  ).toBeInTheDocument();

  // Done carries the default sort (§11.5 — most recently completed
  // first); the owning th exposes it via aria-sort.
  const doneHeader = screen.getByRole('button', { name: 'Sort by Done' });
  expect(doneHeader.closest('th')).toHaveAttribute('aria-sort', 'descending');

  // The newest completion of the mock history leads the list.
  const rows = visibleRows();
  expect(rows.length).toBeGreaterThan(0);
  expect(rows[0]).toHaveTextContent('006900');

  // History outside the default window is not loaded: the oldest
  // generated Work Order completed far earlier than 90 days ago.
  expect(
    screen.queryByRole('button', { name: 'Open Work Order 006721' }),
  ).not.toBeInTheDocument();

  // Read-only history: the page offers no New Work Order entry.
  expect(
    screen.queryByRole('button', { name: '＋ New Work Order' }),
  ).not.toBeInTheDocument();
});

test('a row opens the read-only Work Order Details dialog with the Done date', async () => {
  await renderCompleted();

  const row = screen.getByRole('button', { name: 'Open Work Order 006996' });
  row.focus();
  fireEvent.click(row);

  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(dialog).toHaveTextContent('006996');
  expect(dialog).toHaveTextContent('Complete');
  // The meta line carries the Done date (§11.5).
  expect(dialog).toHaveTextContent(/Done/);
  // Read-only: no Save demand, and the read-only explanation renders.
  expect(screen.queryByRole('button', { name: 'Save demand' })).toBeNull();
  expect(dialog).toHaveTextContent('demand lines are read-only');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the Due outcome filter narrows the history to late completions', async () => {
  await renderCompleted();

  expect(
    screen.getByRole('button', { name: 'Open Work Order 006996' }),
  ).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Due outcome'), {
    target: { value: 'late' },
  });

  // 006990 completed after its due date; 006996 completed on time.
  expect(
    screen.getByRole('button', { name: 'Open Work Order 006990' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Open Work Order 006996' }),
  ).not.toBeInTheDocument();
  // Every visible row carries the explicit ✕ late outcome (never a
  // color-only signal).
  expect(visibleRows().length).toBeGreaterThan(0);
  for (const row of visibleRows()) {
    expect(row).toHaveTextContent(/✕ \d+ days? late/);
  }
});

test('a search miss inside a bounded range offers one-click Search all history', async () => {
  await renderCompleted();

  // 006721 exists — but completed far outside the default 90-day
  // window.
  fireEvent.change(screen.getByLabelText('Search completed Work Orders'), {
    target: { value: '006721' },
  });

  expect(
    screen.getByText(/No completed Work Orders match in this range/),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Search all history' }));

  // The search text is kept; only the Done range widens.
  expect(
    screen.getByRole('button', { name: 'Open Work Order 006721' }),
  ).toBeInTheDocument();
  expect(screen.getByText(/all time/)).toBeInTheDocument();
});

test('Show more pages the full history in slices of 50', async () => {
  await renderCompleted();

  fireEvent.change(screen.getByLabelText('Done date range'), {
    target: { value: 'all' },
  });

  const total = MOCK_COMPLETED_WORK_ORDERS.length;
  expect(visibleRows()).toHaveLength(50);
  expect(document.querySelector('.cwo-summary')).toHaveTextContent(
    `Showing 50 of ${total} completed Work Orders · all time`,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  expect(visibleRows()).toHaveLength(100);

  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  // Everything loaded — nothing more to page.
  expect(visibleRows()).toHaveLength(total);
  expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
});

/* ============ Entry points on the active list ============ */

test('the active list reads real server state — the mock completed history never appears in it', async () => {
  await renderActiveList();

  // The list shows exactly the stubbed server state; the mock
  // completed history must not leak into the real list.
  expect(await screen.findByText('007201')).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Open Work Order 006996' }),
  ).not.toBeInTheDocument();
  expect(document.querySelector('.wostat.complete')).toBeNull();
});

test('the production route without the DEV preview states the workflow honestly', () => {
  // Production builds compile the DEV preview away and render this
  // page instead: no mock history, no fake toolbar — the truth that
  // completion (full allocation) is not part of this release.
  window.history.replaceState({}, '', '/management/work-orders/completed');
  render(
    <RouterProvider>
      <CompletedWorkOrdersUnavailable />
    </RouterProvider>,
  );

  expect(
    screen.getByRole('heading', { name: 'Completed Work Orders' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No Work Order can be completed yet/),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '‹ Work Orders' })).toHaveAttribute(
    'href',
    '/management/work-orders',
  );
  // No mock rows and no history toolbar render here.
  expect(screen.queryByText('006996')).toBeNull();
  expect(screen.queryByLabelText('Done date range')).toBeNull();
});

test('the active toolbar links to the Completed Work Orders page', async () => {
  await renderActiveList();

  const link = screen.getByRole('link', { name: 'Completed Work Orders ›' });
  expect(link.closest('.wo-tools')).not.toBeNull();

  fireEvent.click(link);
  expect(window.location.pathname).toBe('/management/work-orders/completed');
  expect(
    await screen.findByRole('heading', { name: 'Completed Work Orders' }),
  ).toBeInTheDocument();
});

test('an active-list search miss points at the completed history', async () => {
  await renderActiveList();

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '006996' },
  });

  // The search runs on the server (GUI_DESIGN §11.1), so the miss
  // arrives with the server's answer rather than from a local filter.
  await waitFor(() =>
    expect(
      screen.getByText(/No active Work Order matches/),
    ).toBeInTheDocument(),
  );
  const cell = screen
    .getByText(/No active Work Order matches/)
    .closest('td') as HTMLTableCellElement;
  const historyLink = Array.from(cell.querySelectorAll('a')).find(
    (a) => a.textContent === 'Completed Work Orders',
  );
  expect(historyLink).toBeDefined();
});

// NOTE: the former "entering a completed WO Number in New Work Order
// opens its read-only details" scenario is unreachable in Phase 4 —
// no Work Order can complete before the allocation workflow exists,
// and the New Work Order duplicate resolution runs against the real
// server history (covered in work-orders.test.tsx). The rule itself
// (uniqueness spans the WHOLE history, completed included) lives in
// the backend's uniqueness constraint and returns with Phase 10.
