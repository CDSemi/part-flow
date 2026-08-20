import './pn-barcode.css';

/**
 * The ONE entry to the printable PN label from a demand line: a quiet
 * secondary chip. Rendered identically by New Work Order and Work Order
 * Details.
 *
 * The label is derived from the canonical PN itself, so a draft/new PN
 * carries its chip exactly like a saved one — no PartNumber master is
 * involved. Opening the label is a pure presentation action: it never
 * touches the line draft, the dirty state, or release behavior.
 */
export function PnBarcodeChip({
  pn,
  onOpen,
}: {
  /** The canonical uppercase PN — names the chip for assistive
   * technology, since the visible text is the same on every line. */
  pn: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="pnb-chip"
      aria-label={`Open barcode label for ${pn}`}
      onClick={onOpen}
    >
      Barcode label…
    </button>
  );
}
