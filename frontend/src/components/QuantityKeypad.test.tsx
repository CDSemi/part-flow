import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, test, vi } from 'vitest';

import { QuantityKeypad } from './QuantityKeypad';

// Component-level regressions for the shared cursor-aware editing
// model: the physical keyboard and the on-screen keypad must produce
// IDENTICAL value and caret behavior (selection replacement, caret
// insertion, unfocused append fallback), with focus kept or restored
// on the quantity input after every on-screen edit.

afterEach(cleanup);

function Harness({ initial, max }: { initial: string; max?: number }) {
  const [value, setValue] = useState(initial);
  return (
    <div>
      <QuantityKeypad value={value} onChange={setValue} max={max} />
      <button type="button">elsewhere</button>
    </div>
  );
}

function qtyInput(): HTMLInputElement {
  return document.querySelector('.qtydisplay') as HTMLInputElement;
}

function keypadDigit(digit: string): HTMLButtonElement {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>('.keypad button'),
  ).find((b) => b.textContent === digit)!;
}

/** Click a keypad button the way a pointer does: mousedown (prevented,
 *  focus stays put) then click. */
function pressKeypad(button: HTMLButtonElement) {
  fireEvent.mouseDown(button);
  fireEvent.click(button);
}

test('the input mounts focused with its value selected', () => {
  render(<Harness initial="4" />);
  const input = qtyInput();
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe(0);
  expect(input.selectionEnd).toBe(1);
});

test('a physical digit replaces the fully selected default value', () => {
  render(<Harness initial="4" max={4} />);
  const input = qtyInput();
  // Mount behavior already selected the whole value.
  fireEvent.keyDown(input, { key: '2' });
  expect(input.value).toBe('2');
  expect(input.selectionStart).toBe(1);
  expect(input.selectionEnd).toBe(1);
});

test('an on-screen digit replaces the fully selected default value', () => {
  render(<Harness initial="4" max={4} />);
  const input = qtyInput();
  pressKeypad(keypadDigit('2'));
  expect(input.value).toBe('2');
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe(1);
  expect(input.selectionEnd).toBe(1);
});

test('a physical digit inserts at a middle caret position', () => {
  render(<Harness initial="23" />);
  const input = qtyInput();
  input.setSelectionRange(1, 1); // caret between 2 and 3
  fireEvent.keyDown(input, { key: '4' });
  expect(input.value).toBe('243');
  expect(input.selectionStart).toBe(2);
  expect(input.selectionEnd).toBe(2);
});

test('an on-screen digit inserts at a middle caret position', () => {
  render(<Harness initial="23" />);
  const input = qtyInput();
  input.setSelectionRange(1, 1);
  pressKeypad(keypadDigit('4'));
  expect(input.value).toBe('243');
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe(2);
  expect(input.selectionEnd).toBe(2);
});

test('a partial selection is replaced at its exact position', () => {
  render(<Harness initial="1234" />);
  const input = qtyInput();
  input.setSelectionRange(1, 3); // "23" selected
  fireEvent.keyDown(input, { key: '9' });
  expect(input.value).toBe('194');
  expect(input.selectionStart).toBe(2);

  // The on-screen keypad applies the identical transition.
  input.setSelectionRange(0, 2); // "19" selected
  pressKeypad(keypadDigit('5'));
  expect(input.value).toBe('54');
  expect(input.selectionStart).toBe(1);
});

test('an unfocused input falls back to appending at the end', () => {
  render(<Harness initial="23" />);
  const input = qtyInput();
  const elsewhere = screen.getByRole('button', { name: 'elsewhere' });
  elsewhere.focus();
  expect(input).not.toHaveFocus();
  // On-screen keypad: appends, then restores focus with the caret at
  // the end (the fallback append is the only end-of-value jump).
  fireEvent.click(keypadDigit('4'));
  expect(input.value).toBe('234');
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe(3);
});

test('the maximum length still holds after replacing a selection', () => {
  render(<Harness initial="1234" />);
  const input = qtyInput();
  input.setSelectionRange(0, 4);
  fireEvent.keyDown(input, { key: '9' }); // replace all → 9
  expect(input.value).toBe('9');
  fireEvent.keyDown(input, { key: '8' });
  fireEvent.keyDown(input, { key: '7' });
  fireEvent.keyDown(input, { key: '6' });
  expect(input.value).toBe('9876');
  // Full again: a collapsed-caret insertion is rejected, value intact.
  input.setSelectionRange(2, 2);
  fireEvent.keyDown(input, { key: '5' });
  expect(input.value).toBe('9876');
  pressKeypad(keypadDigit('5'));
  expect(input.value).toBe('9876');
});

test('Backspace is cursor-aware and identical for both keyboards', () => {
  render(<Harness initial="1234" />);
  const input = qtyInput();
  input.setSelectionRange(2, 2);
  fireEvent.keyDown(input, { key: 'Backspace' }); // removes the "2"
  expect(input.value).toBe('134');
  expect(input.selectionStart).toBe(1);
  const backspace = screen.getByRole('button', { name: 'Backspace' });
  fireEvent.mouseDown(backspace);
  fireEvent.click(backspace); // caret 1 → removes the "1"
  expect(input.value).toBe('34');
  expect(input.selectionStart).toBe(0);
});

test('CLEAR keeps focus on the input; MAX keeps focus and selects the applied value', () => {
  render(<Harness initial="12" max={34} />);
  const input = qtyInput();
  const clear = screen.getByRole('button', { name: 'CLEAR' });
  fireEvent.mouseDown(clear);
  fireEvent.click(clear);
  expect(input.value).toBe('');
  expect(input).toHaveFocus();
  const maxButton = screen.getByRole('button', { name: 'MAX 34' });
  fireEvent.mouseDown(maxButton);
  fireEvent.click(maxButton);
  expect(input.value).toBe('34');
  expect(input).toHaveFocus();
  // The applied value is selected in full — not just caret-placed
  // after it — so a following digit overrides it directly.
  expect(input.selectionStart).toBe(0);
  expect(input.selectionEnd).toBe(2);
});

test('a digit typed right after MAX overrides the selected value instead of appending to it', () => {
  render(<Harness initial="12" max={34} />);
  const input = qtyInput();
  const maxButton = screen.getByRole('button', { name: 'MAX 34' });
  fireEvent.mouseDown(maxButton);
  fireEvent.click(maxButton);
  expect(input.value).toBe('34');
  fireEvent.keyDown(input, { key: '5' });
  expect(input.value).toBe('5');
});

test('clicking MAX again while already at the max value re-selects it', () => {
  render(<Harness initial="34" max={34} />);
  const input = qtyInput();
  input.setSelectionRange(2, 2);
  const maxButton = screen.getByRole('button', { name: 'MAX 34' });
  fireEvent.mouseDown(maxButton);
  fireEvent.click(maxButton);
  expect(input.value).toBe('34');
  expect(input.selectionStart).toBe(0);
  expect(input.selectionEnd).toBe(2);
});

test('paste is sanitized to digits within the maximum length', () => {
  render(<Harness initial="" />);
  const input = qtyInput();
  fireEvent.change(input, { target: { value: '12a34 56' } });
  expect(input.value).toBe('1234');
});

test('Space and other printable characters are consumed and ignored', () => {
  render(<Harness initial="42" />);
  const input = qtyInput();
  input.setSelectionRange(1, 1);
  fireEvent.keyDown(input, { key: ' ' });
  fireEvent.keyDown(input, { key: 'x' });
  expect(input.value).toBe('42');
});

/* ============ Touch-primary devices and the soft-keyboard fallback ============ */

test('a non-touch device keeps inputMode="numeric" and the full NumPad', () => {
  render(<Harness initial="4" max={4} />);
  expect(qtyInput().getAttribute('inputmode')).toBe('numeric');
  expect(document.querySelector('.keypad')).not.toBeNull();
});

test('a touch-primary device renders inputMode="none" while keeping the NumPad and editing', () => {
  const original = window.matchMedia;
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: query === '(pointer: coarse)' || query === '(hover: none)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
  Object.defineProperty(window.navigator, 'maxTouchPoints', {
    value: 5,
    configurable: true,
  });
  try {
    render(<Harness initial="4" max={4} />);
    const input = qtyInput();
    // The native soft keyboard is suppressed…
    expect(input.getAttribute('inputmode')).toBe('none');
    // …while the custom NumPad, MAX, and the shared cursor-aware
    // editing (physical keyboards still fire key events) keep working.
    expect(document.querySelector('.keypad')).not.toBeNull();
    pressKeypad(keypadDigit('2'));
    expect(input.value).toBe('2');
    fireEvent.keyDown(input, { key: '3' });
    expect(input.value).toBe('23');
    const maxButton = screen.getByRole('button', { name: 'MAX 4' });
    fireEvent.mouseDown(maxButton);
    fireEvent.click(maxButton);
    expect(input.value).toBe('4');
  } finally {
    vi.unstubAllGlobals();
    window.matchMedia = original;
    Object.defineProperty(window.navigator, 'maxTouchPoints', {
      value: 0,
      configurable: true,
    });
  }
});

test('the visualViewport fallback collapses the NumPad while a soft keyboard is open', () => {
  // jsdom has no visualViewport — install a minimal stub whose height
  // can shrink and grow, dispatching its resize listener.
  let height = 800;
  const listeners = new Set<() => void>();
  const viewport = {
    get height() {
      return height;
    },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  Object.defineProperty(window, 'visualViewport', {
    value: viewport,
    configurable: true,
  });
  try {
    render(<Harness initial="4" max={4} />);
    const input = qtyInput();
    expect(document.querySelector('.keypad')).not.toBeNull();

    // A meaningful height loss (soft keyboard) collapses the NumPad —
    // the input itself stays for editing and validation.
    height = 420;
    act(() => listeners.forEach((fn) => fn()));
    expect(document.querySelector('.keypad')).toBeNull();
    expect(qtyInput()).not.toBeNull();
    fireEvent.keyDown(input, { key: '2' });
    expect(input.value).toBe('2');

    // The viewport returns → the NumPad is restored.
    height = 800;
    act(() => listeners.forEach((fn) => fn()));
    expect(document.querySelector('.keypad')).not.toBeNull();

    // A small height change (browser chrome) never collapses it.
    height = 720;
    act(() => listeners.forEach((fn) => fn()));
    expect(document.querySelector('.keypad')).not.toBeNull();
  } finally {
    Object.defineProperty(window, 'visualViewport', {
      value: undefined,
      configurable: true,
    });
  }
});
