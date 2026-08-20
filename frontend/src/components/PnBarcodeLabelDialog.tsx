import './pn-barcode.css';

import { pnBarcode } from '../views/scan-station/barcode';
import { Code128Svg } from './Code128Svg';
import { ModalDialog } from './ModalDialog';

/**
 * The ONE printable PN barcode label: the Code 128 barcode of the
 * scanned value (`PF:PN:<canonical-PN>`), the canonical PN beneath it,
 * and the full value as the quiet verification line. Every surface that
 * offers a PN label renders THIS dialog (Management → Part Numbers, New
 * Work Order, Work Order Details, Add Part) — no per-surface copy and
 * no second barcode renderer exists; the bars come from the shared
 * `Code128Svg`, which owns the ONE encoder.
 *
 * The label is fully derived from the PN identity itself, so it works
 * for an existing PN master and for a new canonical PN alike — no
 * master metadata is involved. Print Label prints exactly the label
 * area (print styles hide the rest of the page).
 */
export function PnBarcodeLabelDialog({
  pn,
  onClose,
}: {
  /** The canonical uppercase PN. */
  pn: string;
  onClose: () => void;
}) {
  const value = pnBarcode(pn);
  return (
    <ModalDialog label="Part Number barcode label" onClose={onClose}>
      <h3>Part Number barcode label</h3>
      <div className="sub">
        Scan this label to identify Part Number <b>{pn}</b>.
      </div>
      <div className="pnb-label pnb-labelprint">
        <Code128Svg className="lbarcode" value={value} />
        <div className="lpn">{pn}</div>
        <div className="lvalue">{value}</div>
      </div>
      <div className="row">
        <button type="button" className="bigbtn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <button
          type="button"
          className="bigbtn primary"
          onClick={() => window.print()}
        >
          Print Label
        </button>
      </div>
    </ModalDialog>
  );
}
