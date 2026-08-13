// PartFlow barcode parsing and PN normalization (presentation-side
// mirror of the canonical rules in PROJECT_PROFILE §7/§10; server-side
// validation remains the authority for every production write).
//
// The `PF:` namespace identifies PartFlow-owned barcodes and determines
// the entity type deterministically. The PN string itself is the stable
// domain identity: PN identity is case-insensitive, the canonical form
// is UPPERCASE, and a PN may contain no whitespace of any kind
// (whitespace is rejected, never silently removed). Beyond those rules
// the PN stays an opaque arbitrary string — segments are never parsed,
// no format is validated, and the PN does not need to exist in a
// preloaded catalog (the PartNumber master metadata record is created
// on first valid use).
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
 * Normalize an entered PN to its canonical form (PROJECT_PROFILE §7):
 * the value must be non-empty and contain no whitespace of any kind
 * (space, tab, newline — rejected, never silently removed); identity is
 * case-insensitive, so the canonical PN is the UPPERCASE value. Returns
 * the canonical PN, or null when the input is not a valid PN.
 */
export function normalizePartNumber(input: string): string | null {
  if (input === '' || /\s/.test(input)) return null;
  return input.toUpperCase();
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
    // The suffix is the PN candidate: it must be non-empty and
    // whitespace-free, and is canonicalized to the uppercase PN.
    const pn = normalizePartNumber(value.slice('PF:PN:'.length));
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
