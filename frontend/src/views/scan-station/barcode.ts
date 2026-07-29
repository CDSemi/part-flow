// PartFlow barcode parsing (presentation-side mirror of the canonical
// rules in PROJECT_PROFILE §10; server-side validation remains the
// authority for every production write).
//
// The `PF:` namespace identifies PartFlow-owned barcodes and determines
// the entity type deterministically. PN values have no guaranteed
// format: after the exact `PF:PN:` prefix, the ENTIRE non-empty suffix
// is the PN — segments are never parsed, no format is validated, and
// the PN does not need to exist in a preloaded catalog (the internal
// PartNumber record is created on first valid use). PN identity is
// case-insensitive while the originally entered casing is preserved
// for display.
//
// There are no Action barcodes (the former armed action values were
// removed): action intent is chosen
// through explicit one-shot dialogs, never through armed barcode state.
// `PF:SCRAP` is a dedicated context-sensitive barcode accepted only
// inside the Scrap workflow.

export type ParsedScan =
  | { kind: 'pn'; pn: string }
  | { kind: 'machine'; id: string }
  | { kind: 'worker'; id: string }
  | { kind: 'area'; id: string }
  | { kind: 'scrap' }
  | { kind: 'empty' }
  | { kind: 'unknown'; raw: string };

/** The dedicated Scrap-workflow barcode value. */
export const SCRAP_BARCODE = 'PF:SCRAP';

/** Trim scanner terminators (CR/LF/TAB) and surrounding whitespace. */
export function normalizeScanInput(raw: string): string {
  return raw.replace(/[\r\n\t]/g, '').trim();
}

/**
 * Classify one scanned value. The `PF:` prefixes are exact (PartFlow
 * prints its own barcodes); anything else is unknown and rejected —
 * unrelated factory/vendor barcodes are never treated as PartFlow
 * entities, and raw PN text is never auto-accepted as a barcode.
 */
export function parseScan(raw: string): ParsedScan {
  const value = normalizeScanInput(raw);
  if (!value) return { kind: 'empty' };
  if (value === SCRAP_BARCODE) return { kind: 'scrap' };
  if (value.startsWith('PF:PN:')) {
    const pn = value.slice('PF:PN:'.length).trim();
    return pn ? { kind: 'pn', pn } : { kind: 'unknown', raw: value };
  }
  if (value.startsWith('PF:MACHINE:')) {
    const id = value.slice('PF:MACHINE:'.length).trim();
    return id ? { kind: 'machine', id } : { kind: 'unknown', raw: value };
  }
  if (value.startsWith('PF:WORKER:')) {
    const id = value.slice('PF:WORKER:'.length).trim();
    return id ? { kind: 'worker', id } : { kind: 'unknown', raw: value };
  }
  if (value.startsWith('PF:AREA:')) {
    const id = value.slice('PF:AREA:'.length).trim();
    return id ? { kind: 'area', id } : { kind: 'unknown', raw: value };
  }
  return { kind: 'unknown', raw: value };
}

/**
 * Case-insensitive PN identity key: `abc`, `ABC` and `Abc` resolve to
 * the same PartNumber. The stored/displayed casing is the casing of
 * first creation and is never silently changed afterwards.
 */
export function pnKey(pn: string): string {
  return pn.trim().toLowerCase();
}
