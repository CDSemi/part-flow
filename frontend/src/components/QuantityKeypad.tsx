import { QUANTITY_MAX_DIGITS } from './quantity-input';

/**
 * Shared on-screen quantity keypad + value display used by the Scan
 * Station quantity dialog and the Work Orders Add Part flow. Physical
 * keyboard editing is handled by the owning dialog with
 * `applyQuantityKey` so both input paths produce identical values.
 */
export function QuantityKeypad({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const append = (digit: string) =>
    onChange(value.length < QUANTITY_MAX_DIGITS ? value + digit : value);
  return (
    <>
      <div
        className="qtydisplay"
        role="status"
        aria-label={`Quantity: ${value || 'none'}`}
      >
        {value || ' '}
      </div>
      <div className="keypad">
        {['7', '8', '9', '4', '5', '6', '1', '2', '3'].map((k) => (
          <button key={k} onClick={() => append(k)}>
            {k}
          </button>
        ))}
        <button className="act" onClick={() => onChange('')}>
          CLEAR
        </button>
        <button onClick={() => append('0')}>0</button>
        <button
          className="act"
          onClick={() => onChange(value.slice(0, -1))}
          aria-label="Backspace"
        >
          ⌫
        </button>
      </div>
    </>
  );
}
