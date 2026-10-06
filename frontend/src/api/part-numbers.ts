// Part Number details API (Phase 4 lookup — Add Part, GUI_DESIGN
// §11.2/§11.3; Phase 13 management — Management → Part Numbers and the
// shared `Edit Part Number` dialog, GUI_DESIGN §14).
//
// The PN string itself is the identity: it travels ONLY in query
// parameters (never a URL path segment), always through
// `encodeURIComponent` — an unencoded `+` would arrive as a space.
// `number` resolves one exact canonical PN, `search` is a bounded
// contains-match over the PN and the saved Name / Description. The
// barcode value is fully derived (`PF:PN:<canonical-PN>`) and only
// ever read.
//
// Saved details are created on first valid use by the Work Order save
// and the Scan Station receipt, or explicitly from Management → Part
// Numbers and the demand-line `Edit Part Number` dialog (create-only:
// the server answers 409 when details already exist). The client-side
// mirror of the canonical normalization (`normalizePartNumber`,
// `views/scan-station/barcode.ts`) only drives feedback; server
// validation remains the authority.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, apiUpload } from './client';

export interface PartNumberMaster {
  /** The canonical uppercase PN — the identity and natural key. */
  partNumber: string;
  /** Derived label data: `PF:PN:<canonical-part-number>`. */
  barcodeValue: string;
  /** Saved Name / Description, or null. */
  name: string | null;
  /** Informational current revision, or null. */
  currentRevision: string | null;
  /** ERP mapping (display only), or null. */
  erpId: string | null;
  /** Image cache version; null = the default Part Number image. */
  imageUpdatedAt: string | null;
}

/** One bounded page of the Management → Part Numbers list. */
export interface PartNumberPage {
  rows: PartNumberMaster[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
}

/** The editable details of one Part Number (null = not saved). */
export interface PartNumberDetails {
  name: string | null;
  currentRevision: string | null;
  erpId: string | null;
}

interface PartNumberWire {
  part_number: string;
  barcode_value: string;
  name: string | null;
  current_revision: string | null;
  erp_id: string | null;
  image_updated_at: string | null;
}

interface PartNumberPageWire {
  rows: PartNumberWire[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

function toMaster(wire: PartNumberWire): PartNumberMaster {
  return {
    partNumber: wire.part_number,
    barcodeValue: wire.barcode_value,
    name: wire.name,
    currentRevision: wire.current_revision,
    erpId: wire.erp_id,
    imageUpdatedAt: wire.image_updated_at,
  };
}

/** The query string that addresses one PN. */
function numberQuery(partNumber: string): string {
  return `?number=${encodeURIComponent(partNumber)}`;
}

/**
 * Contains-search over the PN and the saved Name / Description. The
 * result is bounded by the SERVER (`part_numbers.SEARCH_RESULT_LIMIT`)
 * — for a blank query too, so an unfiltered lookup never streams the
 * whole catalog.
 */
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
 * Exact canonical resolution of one PN, or null when no saved details
 * exist — the Add Part flow then offers explicit creation on first use,
 * and the `Edit Part Number` dialog opens as `New Part Number`.
 */
export async function resolvePartNumber(
  partNumber: string,
): Promise<PartNumberMaster | null> {
  const wires = await apiRequest<PartNumberWire[]>(
    `/api/part-numbers${numberQuery(partNumber)}`,
  );
  return wires.length > 0 ? toMaster(wires[0]) : null;
}

/**
 * One bounded page of saved Part Number details for Management → Part
 * Numbers: ordered by PN, `search` a server-side contains-match over
 * the PN, name, revision and ERP ID.
 */
export async function listPartNumberPage(
  search: string,
  limit: number,
): Promise<PartNumberPage> {
  const wire = await apiRequest<PartNumberPageWire>(
    `/api/part-numbers/page?search=${encodeURIComponent(search.trim())}&limit=${limit}`,
  );
  return {
    rows: wire.rows.map(toMaster),
    total: wire.total,
    offset: wire.offset,
    limit: wire.limit,
    hasMore: wire.has_more,
  };
}

/**
 * Create saved details for a PN (create-only: 409 when they already
 * exist). The PN is sent as entered (trimmed) — the server
 * canonicalizes and validates it.
 */
export async function createPartNumber(
  partNumber: string,
  details: PartNumberDetails,
): Promise<PartNumberMaster> {
  const wire = await apiRequest<PartNumberWire>('/api/part-numbers', {
    method: 'POST',
    body: {
      part_number: partNumber,
      name: details.name,
      current_revision: details.currentRevision,
      erp_id: details.erpId,
    },
  });
  return toMaster(wire);
}

/**
 * Change saved details. Only the keys present in `patch` are sent
 * (`null` clears a field; an omitted key stays unchanged on the
 * server), so an edit never reverts fields it did not touch; `{}` is a
 * valid no-op that still proves the record exists.
 */
export async function updatePartNumber(
  partNumber: string,
  patch: Partial<PartNumberDetails>,
): Promise<PartNumberMaster> {
  const wire = await apiRequest<PartNumberWire>(
    `/api/part-numbers${numberQuery(partNumber)}`,
    {
      method: 'PATCH',
      body: {
        ...(patch.name !== undefined ? { name: patch.name } : {}),
        ...(patch.currentRevision !== undefined
          ? { current_revision: patch.currentRevision }
          : {}),
        ...(patch.erpId !== undefined ? { erp_id: patch.erpId } : {}),
      },
    },
  );
  return toMaster(wire);
}

/**
 * Hard-delete the saved details (image, name, revision, ERP ID). The
 * PN and its production history are untouched.
 */
export async function deletePartNumber(partNumber: string): Promise<void> {
  await apiRequest<void>(`/api/part-numbers${numberQuery(partNumber)}`, {
    method: 'DELETE',
  });
}

/** Replace the image with one PNG, JPEG or WebP image (raw body). */
export async function uploadPartNumberImage(
  partNumber: string,
  image: Blob,
): Promise<PartNumberMaster> {
  const wire = await apiUpload<PartNumberWire>(
    `/api/part-numbers/image${numberQuery(partNumber)}`,
    image,
  );
  return toMaster(wire);
}

/** Remove the image; answered with the record (also when none existed). */
export async function removePartNumberImage(
  partNumber: string,
): Promise<PartNumberMaster> {
  const wire = await apiRequest<PartNumberWire>(
    `/api/part-numbers/image${numberQuery(partNumber)}`,
    { method: 'DELETE' },
  );
  return toMaster(wire);
}

/**
 * Display URL of a Part Number image, or null when none is saved (the
 * default image is shown). The `v` parameter carries the image version
 * so a replaced image is never served from a stale cache (the server
 * ignores it).
 */
export function partNumberImageUrl(
  master: Pick<PartNumberMaster, 'partNumber' | 'imageUpdatedAt'>,
): string | null {
  if (master.imageUpdatedAt === null) return null;
  return `/api/part-numbers/image${numberQuery(
    master.partNumber,
  )}&v=${encodeURIComponent(master.imageUpdatedAt)}`;
}

/**
 * The secondary PN line `{name} · rev {revision}` from the saved
 * details, or undefined when neither is saved.
 */
export function partNumberSecondaryLine(
  name: string | null,
  revision: string | null,
): string | undefined {
  return (
    [name, revision ? `rev ${revision}` : null].filter(Boolean).join(' · ') ||
    undefined
  );
}
