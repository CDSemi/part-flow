import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { PrintBarcodesDialog } from './PrintBarcodesDialog';
import {
  checkState,
  printOrder,
  selectedCountText,
  shownKeys,
  toggleKey,
  toggleKeys,
} from './print-barcodes';
import type { BarcodeSection } from './print-barcodes';

// The ONE Print barcodes dialog (GUI_DESIGN §12.6, §14.5): card and
// checkbox toggling, group checkboxes with the mixed state, Select all,
// one selection per key across groups, and the print sheet.

afterEach(() => {
  cleanup();
});

const GROUPED: BarcodeSection[] = [
  {
    key: 'g1',
    title: 'Group one',
    titleText: 'Group one',
    itemKeys: ['A', 'B'],
  },
  {
    key: 'g2',
    title: 'Group two',
    titleText: 'Group two',
    itemKeys: ['B', 'C'],
  },
];

function renderDialog(
  sections: readonly BarcodeSection[] = GROUPED,
  onClose = vi.fn(),
) {
  const result = render(
    <PrintBarcodesDialog
      title="Print test barcodes"
      description="Select labels."
      sections={sections}
      emptyMessage="Nothing to print."
      cardName={(key) => `Card ${key}`}
      renderCard={(key) => <span>{key}</span>}
      renderLabel={(key) => <div className="test-label">{key}</div>}
      onClose={onClose}
    />,
  );
  return { ...result, dialog: screen.getByRole('dialog') };
}

const printed = () =>
  Array.from(
    document.body.querySelectorAll(':scope > .pbx-sheet .pbx-plabel'),
  ).map((label) => label.textContent);

test('the selection model: toggles, group states, shown keys and the print order', () => {
  const empty = new Set<string>();
  expect(checkState(empty, ['A', 'B'])).toBe('off');
  expect(checkState(new Set(['A']), ['A', 'B'])).toBe('mixed');
  expect(checkState(new Set(['A', 'B']), ['A', 'B'])).toBe('on');
  expect([...toggleKey(empty, 'A')]).toEqual(['A']);
  expect([...toggleKey(new Set(['A']), 'A')]).toEqual([]);
  // Mixed or off selects every key; fully on clears only those keys.
  expect([...toggleKeys(new Set(['A', 'Z']), ['A', 'B'])].sort()).toEqual([
    'A',
    'B',
    'Z',
  ]);
  expect([...toggleKeys(new Set(['A', 'B', 'Z']), ['A', 'B'])]).toEqual(['Z']);
  expect(shownKeys(GROUPED)).toEqual(['A', 'B', 'C']);
  expect(printOrder(new Set(['Z', 'C', 'A']), ['A', 'B', 'C'])).toEqual([
    'A',
    'C',
    'Z',
  ]);
  expect(selectedCountText(1)).toBe('1 label selected');
  expect(selectedCountText(0)).toBe('0 labels selected');
});

test('a card toggles by click, Space and Enter; Print Labels needs a selection', () => {
  const { dialog } = renderDialog();
  const print = within(dialog).getByRole('button', { name: 'Print Labels' });
  expect(print).toBeDisabled();
  expect(within(dialog).getByRole('status')).toHaveTextContent(
    '0 labels selected',
  );

  const a = within(dialog).getByRole('checkbox', { name: 'Card A' });
  expect(a).toHaveAttribute('aria-checked', 'false');
  expect(a).toHaveAttribute('tabindex', '0');
  // Clicking the visual checkbox inside the card toggles the card.
  fireEvent.click(a.querySelector('.pbx-box')!);
  expect(a).toHaveAttribute('aria-checked', 'true');
  expect(print).toBeEnabled();
  fireEvent.keyDown(a, { key: ' ' });
  expect(a).toHaveAttribute('aria-checked', 'false');
  fireEvent.keyDown(a, { key: 'Enter' });
  expect(a).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(a);
  expect(a).toHaveAttribute('aria-checked', 'false');
});

test('a key in several groups is one selection, shown in every group, printed once', () => {
  const { dialog } = renderDialog();
  const [inOne, inTwo] = within(dialog).getAllByRole('checkbox', {
    name: 'Card B',
  });
  fireEvent.click(inOne);
  expect(inOne).toHaveAttribute('aria-checked', 'true');
  expect(inTwo).toHaveAttribute('aria-checked', 'true');
  const one = within(dialog).getByRole('checkbox', {
    name: 'Select all in Group one',
  });
  const two = within(dialog).getByRole('checkbox', {
    name: 'Select all in Group two',
  });
  expect(one).toHaveAttribute('aria-checked', 'mixed');
  expect(two).toHaveAttribute('aria-checked', 'mixed');

  // A group checkbox selects its whole group…
  fireEvent.click(two);
  expect(two).toHaveAttribute('aria-checked', 'true');
  expect(one).toHaveAttribute('aria-checked', 'mixed');
  expect(within(dialog).getByRole('status')).toHaveTextContent(
    '2 labels selected',
  );
  expect(printed()).toEqual(['B', 'C']);

  // …and clears it again, the shared key included.
  fireEvent.click(two);
  expect(two).toHaveAttribute('aria-checked', 'false');
  expect(one).toHaveAttribute('aria-checked', 'false');
  expect(printed()).toEqual([]);
});

test('Select all covers every card shown, with the mixed state in between', () => {
  const { dialog } = renderDialog();
  const all = within(dialog).getByRole('checkbox', { name: 'Select all' });
  expect(all).toHaveAttribute('aria-checked', 'false');
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Card C' }));
  expect(all).toHaveAttribute('aria-checked', 'mixed');
  fireEvent.click(all);
  expect(all).toHaveAttribute('aria-checked', 'true');
  expect(within(dialog).getByRole('status')).toHaveTextContent(
    '3 labels selected',
  );
  // Printed once each, in the order shown.
  expect(printed()).toEqual(['A', 'B', 'C']);
  fireEvent.click(all);
  expect(all).toHaveAttribute('aria-checked', 'false');
});

test('the selection outlives the shown cards and still prints what is not shown', () => {
  const onClose = vi.fn();
  const { dialog, rerender } = renderDialog(
    [{ key: 'all', itemKeys: ['A', 'B'] }],
    onClose,
  );
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Card A' }));
  const props = {
    title: 'Print test barcodes',
    description: 'Select labels.',
    emptyMessage: 'Nothing to print.',
    cardName: (key: string) => `Card ${key}`,
    renderCard: (key: string) => <span>{key}</span>,
    renderLabel: (key: string) => <div className="test-label">{key}</div>,
    onClose,
  };
  rerender(
    <PrintBarcodesDialog
      {...props}
      sections={[{ key: 'all', itemKeys: ['C'] }]}
    />,
  );
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Card C' }));
  expect(within(dialog).getByRole('status')).toHaveTextContent(
    '2 labels selected · 1 not shown',
  );
  expect(printed()).toEqual(['C', 'A']);

  rerender(<PrintBarcodesDialog {...props} sections={[]} />);
  expect(within(dialog).getByText('Nothing to print.')).toBeInTheDocument();
  expect(
    within(dialog).getByRole('checkbox', { name: 'Select all' }),
  ).toBeDisabled();
  expect(
    within(dialog).getByRole('button', { name: 'Print Labels' }),
  ).toBeEnabled();
});

test('Print Labels prints the sheet; Escape and Cancel (Esc) close the dialog', () => {
  const onClose = vi.fn();
  const { dialog } = renderDialog(GROUPED, onClose);
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Card A' }));
  const original = window.print;
  const print = vi.fn();
  window.print = print;
  try {
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Print Labels' }),
    );
  } finally {
    window.print = original;
  }
  expect(print).toHaveBeenCalledTimes(1);
  // The sheet is a direct child of <body>, hidden from assistive tech.
  const sheet = document.body.querySelector(':scope > .pbx-sheet');
  expect(sheet).toHaveAttribute('aria-hidden', 'true');
  expect(sheet?.querySelector('.test-label')?.textContent).toBe('A');

  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(onClose).toHaveBeenCalledTimes(2);
});
