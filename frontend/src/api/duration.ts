// ISO 8601 duration handling for the API wire format (Phase 3.5).
//
// Operation `default_expected_duration` travels as Pydantic's
// canonical timedelta JSON form — an ISO 8601 duration such as `PT30M`
// or `P1DT2H`. The UI edits the value as whole minutes (the expected
// duration of one Operation is shop-floor guidance, never a
// sub-minute measurement), so only the day/hour/minute/second subset
// is supported — no year/month components, whose length is undefined.
//
// Production-safe: pure logic only, no mock data, no framework imports.

const DURATION_PATTERN =
  /^(-)?P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$/;

/**
 * Total minutes of an ISO 8601 duration (day/hour/minute/second
 * subset), rounded to the nearest whole minute. Returns null for an
 * unsupported or malformed value instead of guessing.
 */
export function isoDurationToMinutes(value: string): number | null {
  const match = DURATION_PATTERN.exec(value.trim());
  if (!match) return null;
  const [, sign, days, hours, minutes, seconds] = match;
  if (!days && !hours && !minutes && !seconds) return null;
  const total =
    Number(days ?? 0) * 24 * 60 +
    Number(hours ?? 0) * 60 +
    Number(minutes ?? 0) +
    Number(seconds ?? 0) / 60;
  return Math.round(sign ? -total : total);
}

/** ISO 8601 duration of a whole-minute count (`45` → `PT45M`). */
export function minutesToIsoDuration(minutes: number): string {
  return `PT${minutes}M`;
}
