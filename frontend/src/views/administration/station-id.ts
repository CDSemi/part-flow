// Administration → Scan Stations: the Station ID proposed for a new
// station — its Area's name as a capital-letter prefix, `-ST-` and the
// next two-digit number (`CUT-ST-01`, then `CUT-ST-02`, …). The
// proposal is a convenience only: the field stays editable and the
// server judges the entered Station ID (one URL path segment of
// letters, digits, '.', '_' and '-'; unique).

/** The highest station number still read as a sequence; longer digit
 * runs are someone's own naming, never the base of a proposal. */
const MAX_SEQUENCE_DIGITS = 9;

/**
 * The Station ID prefix an Area name yields: letters with their
 * accents removed (`Đ`/`đ` → `D`), in capitals, digits kept, and every
 * other run of characters joined into one `-`. Empty when the name has
 * no usable character.
 */
export function stationIdPrefix(areaName: string): string {
  return areaName
    .normalize('NFD')
    .replace(/\p{M}+/gu, '')
    .replace(/[đĐ]/g, 'D')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * The Station ID proposed for a new station of `area`:
 * `<prefix>-ST-<number>`, numbered one above the highest number that
 * any existing station already uses under the same prefix — every
 * station counts, inactive or rebound to another Area, because a
 * Station ID is never renamed or reused — compared without regard to
 * letter case, and written with at least two digits. An Area name with
 * no usable character falls back to `AREA-<id>`.
 */
export function suggestStationId(
  area: { id: number; name: string },
  existingStationIds: readonly string[],
): string {
  const prefix = `${stationIdPrefix(area.name) || `AREA-${area.id}`}-ST-`;
  // The prefix holds only A–Z, 0–9 and '-': nothing to escape.
  const numbered = new RegExp(
    `^${prefix}(\\d{1,${MAX_SEQUENCE_DIGITS}})$`,
    'i',
  );
  let highest = 0;
  for (const stationId of existingStationIds) {
    const match = numbered.exec(stationId);
    if (match) highest = Math.max(highest, Number(match[1]));
  }
  return `${prefix}${String(highest + 1).padStart(2, '0')}`;
}
