// Shared value editing for every Quantity NumPad: the physical keyboard
// and the on-screen keypad apply the SAME cursor-aware transitions, so
// a Scan Station worker gets identical results from either one.
//
// Editing model (GUI_DESIGN §4.8):
// - a digit REPLACES the selected range at its exact position,
//   INSERTS at a collapsed caret, and APPENDS to the end when the
//   quantity input is not focused / no usable caret exists;
// - Backspace removes the selection, or the digit before the caret
//   (the last digit in the unfocused fallback);
// - Delete clears · Space is handled-but-ignored · everything else is
//   left to the caller (Enter = advance, Escape = Cancel live at the
//   dialog level).

export const QUANTITY_MAX_DIGITS = 4;

/** Selection inside the quantity input — `[start, end)`; collapsed = caret. */
export interface QuantitySelection {
  start: number;
  end: number;
}

/** Result of one edit: the next value and the caret position after it. */
export interface QuantityEdit {
  value: string;
  caret: number;
}

/**
 * Insert one digit honoring the selection: a selected range is replaced
 * at its exact position, a collapsed caret inserts, and a null
 * selection (input unfocused / no usable caret) appends to the end.
 * The maximum digit count is enforced — an insertion that would exceed
 * it leaves the value unchanged.
 */
export function insertQuantityDigit(
  value: string,
  digit: string,
  selection: QuantitySelection | null,
): QuantityEdit {
  const start = selection?.start ?? value.length;
  const end = selection?.end ?? value.length;
  if (!/^\d$/.test(digit)) return { value, caret: end };
  const next = value.slice(0, start) + digit + value.slice(end);
  if (next.length > QUANTITY_MAX_DIGITS) return { value, caret: end };
  return { value: next, caret: start + 1 };
}

/**
 * Backspace honoring the selection: removes the selected range, or the
 * digit before the caret. A null selection falls back to removing the
 * last digit (a caret at the end).
 */
export function deleteQuantityBackward(
  value: string,
  selection: QuantitySelection | null,
): QuantityEdit {
  const start = selection?.start ?? value.length;
  const end = selection?.end ?? value.length;
  if (start !== end) {
    return { value: value.slice(0, start) + value.slice(end), caret: start };
  }
  if (start === 0) return { value, caret: 0 };
  return {
    value: value.slice(0, start - 1) + value.slice(start),
    caret: start - 1,
  };
}

/**
 * Apply one physical key in the UNFOCUSED fallback path (dialog-level
 * handling while the quantity input does not own the event). Returns
 * the next value (possibly unchanged, e.g. Space is handled but
 * ignored), or null when the key is not a quantity-editing key (so the
 * caller leaves the event alone — Enter/Escape stay separate). Delegates
 * to the same shared transitions as the focused cursor-aware path.
 */
export function applyQuantityKey(value: string, key: string): string | null {
  if (/^\d$/.test(key)) return insertQuantityDigit(value, key, null).value;
  if (key === 'Backspace') return deleteQuantityBackward(value, null).value;
  if (key === 'Delete' || key === 'Clear') return '';
  if (key === ' ') return value; // ignored, but consumed
  return null;
}

/** Digits-only sanitizer for the focusable quantity text input. */
export function sanitizeQuantity(raw: string): string {
  return raw.replace(/\D/g, '').slice(0, QUANTITY_MAX_DIGITS);
}
