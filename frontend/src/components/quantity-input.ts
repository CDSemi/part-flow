// Physical-keyboard editing for the quantity-entry dialogs: the same
// value transitions as the on-screen keypad, so a Scan Station worker
// can type quantities without touching the screen.
//
// Required mapping: 0–9 append · Backspace removes the last digit ·
// Delete clears · Space is handled-but-ignored · everything else is
// left to the caller (Enter = Confirm, Escape = Cancel live at the
// dialog level).

export const QUANTITY_MAX_DIGITS = 4;

/**
 * Apply one physical key to the quantity value. Returns the next value
 * (possibly unchanged, e.g. Space is handled but ignored), or null when
 * the key is not a quantity-editing key (so the caller leaves the event
 * alone — Enter/Escape confirmation stays separate).
 */
export function applyQuantityKey(value: string, key: string): string | null {
  if (/^\d$/.test(key)) {
    return value.length < QUANTITY_MAX_DIGITS ? value + key : value;
  }
  if (key === 'Backspace') return value.slice(0, -1);
  if (key === 'Delete' || key === 'Clear') return '';
  if (key === ' ') return value; // ignored, but consumed
  return null;
}

/** Digits-only sanitizer for the focusable quantity text input. */
export function sanitizeQuantity(raw: string): string {
  return raw.replace(/\D/g, '').slice(0, QUANTITY_MAX_DIGITS);
}
