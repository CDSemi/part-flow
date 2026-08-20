import { Code128Svg } from '../../components/Code128Svg';
import { ModalDialog } from '../../components/ModalDialog';
import { pnBarcode } from '../scan-station/barcode';

/**
 * Printable PN barcode label (Phase 4 intake capability): the
 * canonical PN and the Code 128 barcode of the scanned value
 * (`PF:PN:<canonical-PN>`). The barcode is fully derived from the PN
 * identity itself, so the label works for an existing PN master and
 * for a new canonical PN alike — no master metadata is involved.
 * Print Label prints exactly the label area (print styles hide the
 * rest of the page). Rendering reuses the ONE shared Code 128
 * component — the encoder is never duplicated.
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
    <ModalDialog label="PN barcode label" onClose={onClose}>
      <h3>PN barcode label</h3>
      <div className="sub">
        Scan this label to identify Part Number <b className="mono">{pn}</b>.
      </div>
      <div className="pn-label pn-labelprint">
        <div className="lname mono">{pn}</div>
        <Code128Svg className="lbarcode" value={value} />
        {/* Verification line: the PN and the full scanned value. */}
        <div className="lvalue">
          {pn} · {value}
        </div>
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={() => window.print()}>
          Print Label
        </button>
      </div>
    </ModalDialog>
  );
}
