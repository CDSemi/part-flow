import './pn-barcode.css';

import { pnBarcode } from '../views/scan-station/barcode';

/**
 * The ONE entry to the printable PN label from a demand line: a quiet
 * secondary chip carrying the line's scanned value (`PF:PN:<PN>`).
 * Rendered identically by New Work Order and Work Order Details.
 *
 * The value is derived from the canonical PN itself, so a draft/new PN
 * carries its chip exactly like a saved one — no PartNumber master is
 * involved. Opening the label is a pure presentation action: it never
 * touches the line draft, the dirty state, or release behavior.
 */
export function PnBarcodeChip({
  pn,
  onOpen,
}: {
  /** The canonical uppercase PN. */
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
      <span className="pnb-chipicon" aria-hidden="true">
        ▥
      </span>
      {pnBarcode(pn)}
    </button>
  );
}
