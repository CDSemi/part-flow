import './pn-barcode.css';

/**
 * The ONE entry to the printable PN label from a demand line: the PN
 * itself is the control, followed by a small label glyph. It replaces
 * the plain PN text in place, so a line costs no extra row height and
 * the affordance sits exactly where the reader already looks.
 * Rendered identically by New Work Order and Work Order Details.
 *
 * The label is derived from the canonical PN itself, so a draft/new PN
 * opens it exactly like a saved one — no PartNumber master is involved.
 * Opening the label is a pure presentation action: it never touches the
 * line draft, the dirty state, or release behavior.
 *
 * PROVISIONAL TARGET (GUI_DESIGN §11.2; IMPLEMENTATION_ROADMAP Phase 13):
 * this control opens the LABEL dialog only because PartNumber metadata
 * management is not real yet — Management → Part Numbers is still a
 * development-only mock view and the `/api/part-numbers` surface carries
 * no metadata. When Phase 13 makes that screen real, retarget this
 * control to the shared `Edit Part Number` dialog (the label stays
 * reachable from inside it) and swap the label glyph for a pencil (edit)
 * icon. Until then a pencil would promise editing that does not exist.
 */
export function PnLabelButton({
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
      className="pnb-pnbtn"
      title={pn}
      aria-label={`Open barcode label for ${pn}`}
      onClick={onOpen}
    >
      {pn}
      <span className="pnb-pnicon" aria-hidden="true">
        ▥
      </span>
    </button>
  );
}
