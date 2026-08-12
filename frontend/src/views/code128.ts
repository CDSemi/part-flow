// Minimal Code 128 (subset B) encoder for PartFlow barcode labels
// (PROJECT_PROFILE §10 — `PF:` namespace values are printable ASCII,
// which subset B covers completely). Deliberately small: one code set,
// no auto-optimization, no dependency — enough to print correct,
// scannable labels for PartFlow's own barcodes.
//
// Production-safe: pure logic only, no mock data, no framework
// imports. Correctness guards live in code128.test.ts (every pattern
// is 11 modules wide; the stop pattern is 13).

/**
 * The 107 canonical Code 128 bar/space width patterns (values 0–106).
 * Each entry alternates bar,space,… widths and sums to 11 modules;
 * the final entry (106, stop) has 7 elements summing to 13.
 */
const PATTERNS: readonly string[] = [
  '212222',
  '222122',
  '222221',
  '121223',
  '121322',
  '131222',
  '122213',
  '122312',
  '132212',
  '221213',
  '221312',
  '231212',
  '112232',
  '122132',
  '122231',
  '113222',
  '123122',
  '123221',
  '223211',
  '221132',
  '221231',
  '213212',
  '223112',
  '312131',
  '311222',
  '321122',
  '321221',
  '312212',
  '322112',
  '322211',
  '212123',
  '212321',
  '232121',
  '111323',
  '131123',
  '131321',
  '112313',
  '132113',
  '132311',
  '211313',
  '231113',
  '231311',
  '112133',
  '112331',
  '132131',
  '113123',
  '113321',
  '133121',
  '313121',
  '211331',
  '231131',
  '213113',
  '213311',
  '213131',
  '311123',
  '311321',
  '331121',
  '312113',
  '312311',
  '332111',
  '314111',
  '221411',
  '431111',
  '111224',
  '111422',
  '121124',
  '121421',
  '141122',
  '141221',
  '112214',
  '112412',
  '122114',
  '122411',
  '142112',
  '142211',
  '241211',
  '221114',
  '413111',
  '241112',
  '134111',
  '111242',
  '121142',
  '121241',
  '114212',
  '124112',
  '124211',
  '411212',
  '421112',
  '421211',
  '212141',
  '214121',
  '412121',
  '111143',
  '111341',
  '131141',
  '114113',
  '114311',
  '411113',
  '411311',
  '113141',
  '114131',
  '311141',
  '411131',
  '211412',
  '211214',
  '211232',
  '2331112',
];

const START_B = 104;
const STOP = 106;

/** Exposed for the correctness tests only. */
export const CODE128_PATTERNS = PATTERNS;

/**
 * Code set B symbol value of one character (ASCII 32–127), or null
 * when the character cannot be encoded in subset B.
 */
function code128BValue(char: string): number | null {
  const code = char.charCodeAt(0);
  if (code < 32 || code > 127) return null;
  return code - 32;
}

/**
 * One barcode module run: `bar` (ink) or space, `width` in modules.
 */
export interface Code128Run {
  bar: boolean;
  width: number;
}

/**
 * Encode a printable-ASCII value as Code 128 B: start code, data,
 * checksum (weighted mod 103), stop. Returns the bar/space runs in
 * order, or null when the value is empty or contains a character
 * outside subset B — the caller decides how to present that.
 */
export function encodeCode128B(value: string): Code128Run[] | null {
  if (!value) return null;
  const values: number[] = [START_B];
  for (const char of value) {
    const symbol = code128BValue(char);
    if (symbol === null) return null;
    values.push(symbol);
  }
  let checksum = START_B;
  for (let i = 1; i < values.length; i += 1) checksum += i * values[i];
  values.push(checksum % 103);
  values.push(STOP);

  const runs: Code128Run[] = [];
  for (const symbol of values) {
    const pattern = PATTERNS[symbol];
    for (let i = 0; i < pattern.length; i += 1) {
      runs.push({ bar: i % 2 === 0, width: Number(pattern[i]) });
    }
  }
  return runs;
}

/** Total module count of an encoded run sequence (quiet zones excluded). */
export function code128ModuleCount(runs: readonly Code128Run[]): number {
  return runs.reduce((sum, run) => sum + run.width, 0);
}
