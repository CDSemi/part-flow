// Machine Asset Tag generation (PROJECT_PROFILE §8.6, §10). Every
// Machine receives its Asset Tag automatically when it is created —
// the tag is the stable, human-readable identity of the physical
// machine and doubles as the Machine barcode value
// (`PF:MACHINE:<asset-tag>`). Asset Tags are unique, never reused
// (retired Machines keep theirs forever), and never change after
// creation; a format change applies to Machines created afterwards
// only.
//
// The format is deliberately simple — a prefix plus a zero-padded
// numeric sequence (`CD-` + 4 digits → `CD-0001`) — configured in
// Administration → Barcode configuration. No generic template engine.
//
// Production-safe: pure logic only, no mock data, no framework
// imports. The development-only sample format lives in src/mocks/.

/** Asset Tag format: prefix + zero-padded numeric sequence. */
export interface AssetTagFormat {
  /** Literal prefix, e.g. `CD-`. */
  prefix: string;
  /** Minimum digit count of the sequence part (zero-padded). */
  digits: number;
}

/** The PF barcode namespace of Machine barcodes (PROJECT_PROFILE §10). */
export const MACHINE_BARCODE_NAMESPACE = 'PF:MACHINE:';

/** Scanned/rendered Machine barcode of one Asset Tag. */
export function machineBarcode(assetTag: string): string {
  return `${MACHINE_BARCODE_NAMESPACE}${assetTag}`;
}

/**
 * Format one sequence number as an Asset Tag. The digit count is a
 * minimum width: a sequence that outgrows it renders unpadded rather
 * than truncated (`digits: 4`, seq 12045 → `CD-12045`).
 */
export function formatAssetTag(
  format: AssetTagFormat,
  sequence: number,
): string {
  return `${format.prefix}${String(sequence).padStart(format.digits, '0')}`;
}

/**
 * Sequence number of an existing Asset Tag under the given format, or
 * null when the tag does not match the format's prefix + digits shape.
 */
function assetTagSequence(format: AssetTagFormat, tag: string): number | null {
  if (!tag.startsWith(format.prefix)) return null;
  const suffix = tag.slice(format.prefix.length);
  if (!/^\d+$/.test(suffix)) return null;
  return Number.parseInt(suffix, 10);
}

/**
 * The next Asset Tag to assign: one past the highest sequence already
 * used under the current prefix — across ALL existing Machines,
 * retired included, so a tag is never reused. Existing tags in an
 * older format simply do not match the current prefix and are left
 * untouched (they are never renamed or regenerated).
 */
export function nextAssetTag(
  format: AssetTagFormat,
  existingTags: readonly string[],
): string {
  let highest = 0;
  for (const tag of existingTags) {
    const sequence = assetTagSequence(format, tag);
    if (sequence !== null && sequence > highest) highest = sequence;
  }
  return formatAssetTag(format, highest + 1);
}
