import './QuantityKeypad.css';

import { useEffect, useRef } from 'react';

import { sanitizeQuantity } from './quantity-input';

/**
 * Shared quantity entry: a REAL focusable numeric text input (focused
 * on mount so a physical keyboard works without clicking first;
 * `inputMode="numeric"`, no native number-spinner) plus the oversized
 * on-screen keypad. Physical keys are handled centrally by the owning
 * dialog with `applyQuantityKey`; the input's own onChange covers
 * paste/IME. Keypad buttons are `type="button"`, non-focusable
 * (tabIndex -1) and keep focus on the input (mousedown prevented), so
 * Space or Enter can never re-activate a previously clicked button.
 * `max` renders a MAX shortcut (transfer/assignment flows); flows
 * without a MAX (Add More Quantity) simply omit it.
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
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  const append = (digit: string) => onChange(sanitizeQuantity(value + digit));
  const keep = (event: React.MouseEvent) => event.preventDefault();
  return (
    <>
      <input
        ref={inputRef}
        className="qtydisplay"
        inputMode="numeric"
        autoComplete="off"
        aria-label={`Quantity: ${value || 'none'}`}
        value={value}
        onChange={(e) => onChange(sanitizeQuantity(e.target.value))}
      />
      <div className="keypad">
        {['7', '8', '9', '4', '5', '6', '1', '2', '3'].map((k) => (
          <button
            key={k}
            type="button"
            tabIndex={-1}
            onMouseDown={keep}
            onClick={() => append(k)}
          >
            {k}
          </button>
        ))}
        <button
          type="button"
          tabIndex={-1}
          className="act"
          onMouseDown={keep}
          onClick={() => onChange('')}
        >
          CLEAR
        </button>
        <button
          type="button"
          tabIndex={-1}
          onMouseDown={keep}
          onClick={() => append('0')}
        >
          0
        </button>
        <button
          type="button"
          tabIndex={-1}
          className="act"
          onMouseDown={keep}
          onClick={() => onChange(value.slice(0, -1))}
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
            onClick={() => onChange(String(max))}
          >
            MAX {max}
          </button>
        ) : null}
      </div>
    </>
  );
}
