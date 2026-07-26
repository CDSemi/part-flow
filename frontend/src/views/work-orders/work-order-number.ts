// Temporary internal Work Order Number generation.
//
// The user may create demand without knowing an external Work Order
// Number, but the internal Work Order identity is never nullable: when
// the WO Number field is left blank and the save is confirmed, a
// unique, human-readable temporary internal number is generated. The
// value is auditable and searchable like any other Work Order Number;
// a UUID is never exposed as the user-facing identifier. Existing
// entered Work Order Numbers stay opaque strings and are never
// reformatted.

const pad = (n: number) => String(n).padStart(2, '0');

/** Local timestamp as `TMP-YYYYMMDD-HHMMSS`. */
export function formatTemporaryWorkOrderNumber(now: Date): string {
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `TMP-${date}-${time}`;
}

/**
 * Unique temporary internal Work Order Number: `TMP-YYYYMMDD-HHMMSS`,
 * with a deterministic `-2`, `-3`, … suffix on collision.
 */
export function generateTemporaryWorkOrderNumber(
  existing: readonly string[],
  now: Date,
): string {
  const base = formatTemporaryWorkOrderNumber(now);
  if (!existing.includes(base)) return base;
  for (let suffix = 2; ; suffix += 1) {
    const candidate = `${base}-${suffix}`;
    if (!existing.includes(candidate)) return candidate;
  }
}
