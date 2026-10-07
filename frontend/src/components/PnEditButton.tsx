import './pn-barcode.css';

/**
 * The PN control of a demand line (GUI_DESIGN §11.2, retargeted in
 * Phase 13): the PN itself is the control, followed by a small pencil
 * (edit) glyph, and it opens the shared `Edit Part Number` dialog — a
 * PN without saved details opens it as `New Part Number` with the PN
 * fixed. The printable barcode label stays reachable from inside that
 * dialog (`Barcode label…`). It replaces the plain PN text in place,
 * so a line costs no extra row height and the affordance sits exactly
 * where the reader already looks. Rendered identically by New Work
 * Order and Work Order Details.
 *
 * Opening the dialog never touches the line draft, the dirty state, or
 * release behavior — the dialog's own writes concern only the Part
 * Number details, never the demand.
 *
 * For a user who may not manage Part Numbers (`readOnly`) the PN stays
 * the control without the glyph and its host opens the dialog
 * read-only — the barcode label stays reachable from every demand line.
 */
export function PnEditButton({
  pn,
  readOnly = false,
  onOpen,
}: {
  /** The canonical uppercase PN. */
  pn: string;
  /** The host opens the read-only `Part Number details`. */
  readOnly?: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="pnb-pnbtn"
      title={pn}
      aria-label={
        readOnly ? `Part Number ${pn} details` : `Edit Part Number ${pn}`
      }
      onClick={onOpen}
    >
      {pn}
      {readOnly ? null : (
        <span className="pnb-pnicon" aria-hidden="true">
          ✎
        </span>
      )}
    </button>
  );
}
