import './QuantityKeypad.css';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import {
  deleteQuantityBackward,
  insertQuantityDigit,
  sanitizeQuantity,
} from './quantity-input';
import type { QuantityEdit, QuantitySelection } from './quantity-input';
import { isTouchPrimaryDevice, useSoftKeyboardOpen } from './touch-device';

/**
 * Shared quantity entry: a REAL focusable numeric text input (focused
 * on mount with its value selected, so a physical keyboard works
 * without clicking first; `inputMode="numeric"`, no native
 * number-spinner) plus the oversized on-screen keypad.
 *
 * Editing is cursor-aware and IDENTICAL for physical keys and keypad
 * buttons (shared transitions in quantity-input.ts): a digit replaces
 * the selection, inserts at the caret, or appends when the input is
 * not focused; the caret lands right after the edit. The input owns
 * digits/Backspace/Delete/Space while focused (events are consumed
 * here); the owning dialog keeps the unfocused fallback via
 * `applyQuantityKey`, plus Enter (advance) and Escape (cancel). The
 * input's own onChange covers paste/IME sanitization. Keypad buttons
 * are `type="button"`, non-focusable (tabIndex -1) and keep focus on
 * the input (mousedown prevented), so Space or Enter can never
 * re-activate a previously clicked button.
 * `max` renders a MAX shortcut (transfer/assignment flows) that also
 * selects the applied value, so a following digit — physical or
 * keypad — overrides it directly without first clearing; flows
 * without a MAX (Add More Quantity) simply omit it.
 *
 * Touch devices (GUI_DESIGN §4.8): on a touch-primary device the
 * input renders `inputMode="none"` so the native soft keyboard stays
 * closed — the custom NumPad, selection/caret behavior, and physical
 * keyboards (which fire real key events regardless of inputMode) all
 * keep working. Where a mobile browser opens the soft keyboard
 * anyway, the `window.visualViewport` fallback detects the height
 * loss and collapses the on-screen NumPad so the input, guidance,
 * validation and dialog actions stay visible; the NumPad returns with
 * the viewport (touch-device.ts — isolated, reversible detection).
 */
export function QuantityKeypad({
  value,
  onChange,
  max,
}: {
  value: string;
  onChange: (next: string) => void;
  max?: number;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pendingCaret = useRef<number | null>(null);
  // Set by the MAX shortcut instead of pendingCaret — the applied
  // value is selected in full, not just caret-placed, so it can be
  // overridden in one step.
  const pendingSelectAll = useRef(false);
  // Decided once per dialog lifecycle — pointer capabilities do not
  // change while a quantity step is open.
  const [touchPrimary] = useState(isTouchPrimaryDevice);
  const softKeyboardOpen = useSoftKeyboardOpen();
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  // Place the caret (or select-all, for MAX) after our own edit once
  // the new value has rendered. External value changes (e.g. the
  // dialog setting an initial default) leave the selection alone.
  useLayoutEffect(() => {
    const caret = pendingCaret.current;
    const selectAll = pendingSelectAll.current;
    pendingCaret.current = null;
    pendingSelectAll.current = false;
    if (caret === null && !selectAll) return;
    const input = inputRef.current;
    if (!input || document.activeElement !== input) return;
    if (selectAll) {
      input.setSelectionRange(0, input.value.length);
    } else if (caret !== null) {
      input.setSelectionRange(caret, caret);
    }
  }, [value]);

  /** Current selection, or null when the input has no usable caret. */
  const selection = (): QuantitySelection | null => {
    const input = inputRef.current;
    if (!input || document.activeElement !== input) return null;
    const { selectionStart, selectionEnd } = input;
    if (selectionStart === null || selectionEnd === null) return null;
    return { start: selectionStart, end: selectionEnd };
  };

  /** Apply one edit, keep/restore focus, and place the caret after it. */
  const applyEdit = (edit: QuantityEdit) => {
    const input = inputRef.current;
    if (edit.value !== value) {
      pendingCaret.current = edit.caret;
      onChange(edit.value);
    } else if (input && document.activeElement === input) {
      input.setSelectionRange(edit.caret, edit.caret);
    }
    input?.focus();
  };

  /**
   * MAX shortcut: apply the max value and select it in full (rather
   * than just caret-placing after it), so the worker can override with
   * a different quantity by typing straight over the selection instead
   * of clearing it first.
   */
  const applyMax = (maxValue: number) => {
    const input = inputRef.current;
    const next = String(maxValue);
    if (next !== value) {
      pendingSelectAll.current = true;
      onChange(next);
    } else if (input && document.activeElement === input) {
      input.setSelectionRange(0, next.length);
    }
    input?.focus();
  };

  // Cursor-aware physical-keyboard editing while the input is focused.
  // Enter/Escape/Tab and modifier chords (copy, paste, select-all) are
  // left to the dialog and the browser; arrows/Home/End keep their
  // native caret movement; any other printable character is ignored.
  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const { key } = event;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (key === 'Enter' || key === 'Escape' || key === 'Tab') return;
    if (/^\d$/.test(key)) {
      event.preventDefault();
      event.stopPropagation();
      applyEdit(insertQuantityDigit(value, key, selection()));
      return;
    }
    if (key === 'Backspace') {
      event.preventDefault();
      event.stopPropagation();
      applyEdit(deleteQuantityBackward(value, selection()));
      return;
    }
    if (key === 'Delete' || key === 'Clear') {
      event.preventDefault();
      event.stopPropagation();
      applyEdit({ value: '', caret: 0 });
      return;
    }
    if (key.length === 1) {
      // Space and every other printable character: consumed, ignored.
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const keep = (event: React.MouseEvent) => event.preventDefault();
  return (
    <>
      <input
        ref={inputRef}
        className="qtydisplay"
        inputMode={touchPrimary ? 'none' : 'numeric'}
        autoComplete="off"
        aria-label={`Quantity: ${value || 'none'}`}
        value={value}
        onKeyDown={handleKeyDown}
        onChange={(e) => onChange(sanitizeQuantity(e.target.value))}
      />
      {softKeyboardOpen ? null : (
        <div className="keypad">
          {['7', '8', '9', '4', '5', '6', '1', '2', '3'].map((k) => (
            <button
              key={k}
              type="button"
              tabIndex={-1}
              onMouseDown={keep}
              onClick={() =>
                applyEdit(insertQuantityDigit(value, k, selection()))
              }
            >
              {k}
            </button>
          ))}
          <button
            type="button"
            tabIndex={-1}
            className="act"
            onMouseDown={keep}
            onClick={() => applyEdit({ value: '', caret: 0 })}
          >
            CLEAR
          </button>
          <button
            type="button"
            tabIndex={-1}
            onMouseDown={keep}
            onClick={() =>
              applyEdit(insertQuantityDigit(value, '0', selection()))
            }
          >
            0
          </button>
          <button
            type="button"
            tabIndex={-1}
            className="act"
            onMouseDown={keep}
            onClick={() =>
              applyEdit(deleteQuantityBackward(value, selection()))
            }
            aria-label="Backspace"
          >
            ⌫
          </button>
          {max !== undefined ? (
            <button
              type="button"
              tabIndex={-1}
              className="act keypad-max"
              onMouseDown={keep}
              onClick={() => applyMax(max)}
            >
              MAX {max}
            </button>
          ) : null}
        </div>
      )}
    </>
  );
}
