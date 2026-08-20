// PartNumber master API (Phase 4 — Add Part lookup, GUI_DESIGN §11.2/§11.3).
//
// The PN string itself is the identity: lookup travels as query
// parameters (never a URL path segment), `number` resolves one exact
// canonical PN and `search` is a contains-match. The barcode value is
// fully derived (`PF:PN:<canonical-PN>`) and only ever read.
//
// Creation is owned by the Work Order save transaction
// (create-on-first-valid-use) — the Add Part flow only needs lookup
// plus the client-side mirror of the canonical normalization
// (`normalizePartNumber`, `views/scan-station/barcode.ts`); server
// validation remains the authority.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

export interface PartNumberMaster {
  /** The canonical uppercase PN — the identity and natural key. */
  partNumber: string;
  /** Derived label data: `PF:PN:<canonical-part-number>`. */
  barcodeValue: string;
}

interface PartNumberWire {
  part_number: string;
  barcode_value: string;
}

function toMaster(wire: PartNumberWire): PartNumberMaster {
  return { partNumber: wire.part_number, barcodeValue: wire.barcode_value };
}

/** Contains-search over the PN masters (all of them for a blank query). */
export async function searchPartNumbers(
  search: string,
): Promise<PartNumberMaster[]> {
  const query = search.trim()
    ? `?search=${encodeURIComponent(search.trim())}`
    : '';
  const wires = await apiRequest<PartNumberWire[]>(`/api/part-numbers${query}`);
  return wires.map(toMaster);
}

/**
 * Exact canonical resolution of one PN, or null when no master exists
 * yet — the Add Part flow then offers explicit creation on first use.
 */
export async function resolvePartNumber(
  partNumber: string,
): Promise<PartNumberMaster | null> {
  const wires = await apiRequest<PartNumberWire[]>(
    `/api/part-numbers?number=${encodeURIComponent(partNumber)}`,
  );
  return wires.length > 0 ? toMaster(wires[0]) : null;
}
