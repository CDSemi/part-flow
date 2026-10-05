// Workers registry API (Administration → Workers): the Scan Station
// audit identity of the people operating the stations — name, company
// badge barcode, optional avatar image and active status. Workers are
// separate from application Users and are deactivated, never deleted.
//
// Wire responses are the backend's snake_case schema; this module maps
// them to the camelCase application type. The server canonicalizes the
// badge barcode (trim, uppercase) and stays authoritative over every
// rule. The avatar travels as a raw image body on its own endpoint and
// is displayed through a cache-versioned URL; no response ever carries
// image bytes.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, apiUpload } from './client';

export interface Worker {
  id: number;
  name: string;
  /** Stored canonical badge barcode (trimmed, uppercase). */
  badgeBarcode: string;
  isActive: boolean;
  /** Avatar cache version (ISO 8601); null when there is no avatar. */
  avatarUpdatedAt: string | null;
}

/** The identity a production read names: who, and the avatar version. */
export type WorkerRef = Pick<Worker, 'id' | 'name' | 'avatarUpdatedAt'>;

/** Wire shape of a Worker reference embedded in another response. */
export interface WorkerRefWire {
  id: number;
  name: string;
  avatar_updated_at: string | null;
}

export function toWorkerRef(wire: WorkerRefWire): WorkerRef {
  return {
    id: wire.id,
    name: wire.name,
    avatarUpdatedAt: wire.avatar_updated_at,
  };
}

interface WorkerWire {
  id: number;
  name: string;
  badge_barcode: string;
  is_active: boolean;
  avatar_updated_at: string | null;
  created_at: string;
  updated_at: string;
}

function toWorker(wire: WorkerWire): Worker {
  return {
    id: wire.id,
    name: wire.name,
    badgeBarcode: wire.badge_barcode,
    isActive: wire.is_active,
    avatarUpdatedAt: wire.avatar_updated_at,
  };
}

/** Every Worker, active and inactive, ordered by name. */
export async function listWorkers(): Promise<Worker[]> {
  const wires = await apiRequest<WorkerWire[]>('/api/workers');
  return wires.map(toWorker);
}

export async function createWorker(input: {
  name: string;
  badgeBarcode: string;
}): Promise<Worker> {
  const wire = await apiRequest<WorkerWire>('/api/workers', {
    method: 'POST',
    body: { name: input.name, badge_barcode: input.badgeBarcode },
  });
  return toWorker(wire);
}

/** Send the provided profile fields; the server answers a no-op with
 * the unchanged record. */
export async function updateWorker(
  id: number,
  patch: { name?: string; badgeBarcode?: string; isActive?: boolean },
): Promise<Worker> {
  const wire = await apiRequest<WorkerWire>(`/api/workers/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.badgeBarcode !== undefined
        ? { badge_barcode: patch.badgeBarcode }
        : {}),
      ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
    },
  });
  return toWorker(wire);
}

/** Replace the avatar with one PNG, JPEG or WebP image (raw body). */
export async function uploadWorkerAvatar(
  id: number,
  image: Blob,
): Promise<Worker> {
  const wire = await apiUpload<WorkerWire>(`/api/workers/${id}/avatar`, image);
  return toWorker(wire);
}

/** Remove the avatar; answered with the record (also when none existed). */
export async function removeWorkerAvatar(id: number): Promise<Worker> {
  const wire = await apiRequest<WorkerWire>(`/api/workers/${id}/avatar`, {
    method: 'DELETE',
  });
  return toWorker(wire);
}

/**
 * Display URL of a Worker's avatar, or null when there is none. The
 * `v` parameter carries the avatar version so a replaced image is
 * never served from a stale cache (the server ignores it).
 */
export function workerAvatarUrl(
  worker: Pick<Worker, 'id' | 'avatarUpdatedAt'>,
): string | null {
  if (worker.avatarUpdatedAt === null) return null;
  return `/api/workers/${worker.id}/avatar?v=${encodeURIComponent(
    worker.avatarUpdatedAt,
  )}`;
}
