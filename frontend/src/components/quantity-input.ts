// Physical-keyboard editing for the quantity-entry dialogs: the same
// value transitions as the on-screen keypad, so a Scan Station worker
// can type quantities without touching the screen.

export const QUANTITY_MAX_DIGITS = 4;

/**
 * Apply one physical key to the quantity value. Returns the next value,
 * or null when the key is not a quantity-editing key (so the caller
 * leaves the event alone — Enter/Escape confirmation stays separate).
 */
export function applyQuantityKey(value: string, key: string): string | null {
  if (/^\d$/.test(key)) {
    return value.length < QUANTITY_MAX_DIGITS ? value + key : value;
  }
  if (key === 'Backspace') return value.slice(0, -1);
  if (key === 'Delete' || key === 'Clear') return '';
  return null;
}
