import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  listPartNumberPage,
  listRunningWorkOrders,
} from '../../api/part-numbers';
import type {
  PartNumberListRow,
  RunningWorkOrder,
} from '../../api/part-numbers';
import { SEARCH_DEBOUNCE_MS } from './part-number-list';
import { PrintPartNumberBarcodesDialog } from './PrintPartNumberBarcodesDialog';

// Print barcodes on Management → Part Numbers (GUI_DESIGN §14.5): the
// grouping toggle, the running Work Order groups (one selection per
// PN), and the flat list's bounded search and sort wiring.

vi.mock('../../api/part-numbers', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../api/part-numbers')>();
  return {
    ...actual,
    listRunningWorkOrders: vi.fn(),
    listPartNumberPage: vi.fn(),
  };
});

const running = vi.mocked(listRunningWorkOrders);
const page = vi.mocked(listPartNumberPage);

function row(
  pn: string,
  name: string | null,
  lastUsedAt: string | null = null,
): PartNumberListRow {
  return {
    partNumber: pn,
    barcodeValue: `PF:PN:${pn}`,
    name,
    currentRevision: null,
    erpId: null,
    imageUpdatedAt: null,
    createdAt: '2026-10-01T08:00:00Z',
    lastUsedAt,
  };
}

const WORK_ORDERS: RunningWorkOrder[] = [
  {
    id: 2,
    workOrderNumber: 'WO-2',
    receivedDate: '2026-11-02',
    dueDate: '2026-11-20',
    partNumbers: [
      { partNumber: 'PN-A', name: 'PLATE' },
      { partNumber: 'PN-B', name: null },
    ],
  },
  {
    id: 1,
    workOrderNumber: null,
    receivedDate: '2026-11-01',
    dueDate: null,
    partNumbers: [{ partNumber: 'PN-B', name: null }],
  },
];

beforeEach(() => {
  running.mockResolvedValue(WORK_ORDERS);
  page.mockImplementation(async (search: string) => {
    const rows = [row('PN-A', 'PLATE'), row('PN-C', 'COVER')].filter(
      (r) => !search || r.partNumber.includes(search.toUpperCase()),
    );
    return { rows, total: rows.length, offset: 0, limit: 100, hasMore: false };
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

async function renderDialog() {
  render(<PrintPartNumberBarcodesDialog onClose={vi.fn()} />);
  const dialog = screen.getByRole('dialog', {
    name: 'Print Part Number barcodes',
  });
  await within(dialog).findByRole('checkbox', { name: 'PN-A · PLATE' });
  return dialog;
}

/** The selected-count line of the selection bar. */
const status = (dialog: HTMLElement) =>
  dialog.querySelector('.pbx-count') as HTMLElement;

test('grouped by running Work Order: a PN on two Work Orders is one selection and prints once', async () => {
  const dialog = await renderDialog();
  expect(page).not.toHaveBeenCalled();
  expect(
    Array.from(dialog.querySelectorAll('.pbx-group')).map((g) =>
      g.getAttribute('aria-label'),
    ),
  ).toEqual(['WO WO-2', 'WO —']);
  expect(within(dialog).getByText('Due 2026-11-20')).toBeInTheDocument();

  const [first, second] = within(dialog).getAllByRole('checkbox', {
    name: 'PN-B',
  });
  fireEvent.click(second);
  expect(first).toHaveAttribute('aria-checked', 'true');
  expect(
    within(dialog).getByRole('checkbox', { name: 'Select all in WO WO-2' }),
  ).toHaveAttribute('aria-checked', 'mixed');
  expect(
    within(dialog).getByRole('checkbox', { name: 'Select all in WO —' }),
  ).toHaveAttribute('aria-checked', 'true');

  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Select all' }));
  expect(status(dialog)).toHaveTextContent('2 labels selected');
  const sheet = document.body.querySelector(':scope > .pbx-sheet')!;
  expect(
    Array.from(sheet.querySelectorAll('.pnb-label .lpn')).map(
      (el) => el.textContent,
    ),
  ).toEqual(['PN-A', 'PN-B']);
  expect(sheet.querySelector('.pnb-labelprint')).toBeNull();
});

test('ungrouped: the bounded search and the sort reach the server and the selection survives them', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const dialog = await renderDialog();
  fireEvent.click(within(dialog).getAllByRole('checkbox', { name: 'PN-B' })[0]);

  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Group by running Work Order' }),
  );
  await within(dialog).findByRole('checkbox', { name: 'PN-C · COVER' });
  expect(page).toHaveBeenLastCalledWith('', 100, 'part_number');
  // PN-B is selected but not in this list.
  expect(status(dialog)).toHaveTextContent('1 label selected · 1 not shown');

  const sort = within(dialog).getByRole('combobox', {
    name: 'Sort Part Numbers',
  });
  expect(
    Array.from((sort as HTMLSelectElement).options).map((o) => o.text),
  ).toEqual([
    'Part Number (A–Z)',
    'Name (A–Z)',
    'Date added (newest first)',
    'Last used in a Work Order (newest first)',
  ]);
  fireEvent.change(sort, { target: { value: 'last_used' } });
  expect(
    await within(dialog).findAllByText('Not used in a Work Order yet'),
  ).toHaveLength(2);
  expect(page).toHaveBeenLastCalledWith('', 100, 'last_used');

  fireEvent.change(
    within(dialog).getByRole('searchbox', { name: 'Search Part Numbers' }),
    { target: { value: 'c' } },
  );
  await act(async () => {
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
  });
  expect(page).toHaveBeenLastCalledWith('c', 100, 'last_used');
  const cover = await within(dialog).findByRole('checkbox', {
    name: 'PN-C · COVER',
  });
  expect(
    within(dialog).queryByRole('checkbox', { name: 'PN-A · PLATE' }),
  ).toBeNull();

  // Select all takes every PN shown and keeps the hidden selection.
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Select all' }));
  expect(cover).toHaveAttribute('aria-checked', 'true');
  expect(status(dialog)).toHaveTextContent('2 labels selected · 1 not shown');
  const sheet = document.body.querySelector(':scope > .pbx-sheet')!;
  expect(
    Array.from(sheet.querySelectorAll('.pnb-label .lpn')).map(
      (el) => el.textContent,
    ),
  ).toEqual(['PN-C', 'PN-B']);

  // Back to the groups: the selection is unchanged.
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Group by running Work Order' }),
  );
  expect(
    within(dialog).getAllByRole('checkbox', { name: 'PN-B' })[0],
  ).toHaveAttribute('aria-checked', 'true');
  expect(status(dialog)).toHaveTextContent('2 labels selected · 1 not shown');
});

test('the date sorts show the date on each card', async () => {
  page.mockResolvedValue({
    rows: [row('PN-A', 'PLATE', '2026-11-03T10:00:00Z'), row('PN-C', null)],
    total: 2,
    offset: 0,
    limit: 100,
    hasMore: false,
  });
  const dialog = await renderDialog();
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Group by running Work Order' }),
  );
  await within(dialog).findByRole('checkbox', { name: 'PN-C' });
  fireEvent.change(
    within(dialog).getByRole('combobox', { name: 'Sort Part Numbers' }),
    { target: { value: 'last_used' } },
  );
  expect(
    await within(dialog).findByText('Not used in a Work Order yet'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText(/^Last used 2026-11-0[34]$/),
  ).toBeInTheDocument();
  fireEvent.change(
    within(dialog).getByRole('combobox', { name: 'Sort Part Numbers' }),
    { target: { value: 'added' } },
  );
  expect(await within(dialog).findAllByText(/^Added 2026-10-01$/)).toHaveLength(
    2,
  );
});

test('no running Work Order and a failed load show their states', async () => {
  running.mockResolvedValueOnce([]);
  render(<PrintPartNumberBarcodesDialog onClose={vi.fn()} />);
  expect(
    await screen.findByText('No Work Order is running right now.'),
  ).toBeInTheDocument();
  cleanup();

  running.mockRejectedValueOnce(new Error('offline'));
  render(<PrintPartNumberBarcodesDialog onClose={vi.fn()} />);
  expect(
    await screen.findByText('Running Work Orders could not be loaded.'),
  ).toBeInTheDocument();
});
