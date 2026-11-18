import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { listArchiveRuns } from './history-archive';

// The run wire contract of history archival step 2: the purge states and
// results pass through as the server answers them, and the purge's
// evidence (the pre-purge backup, the purge start, the purged counts, the
// receipt and its SHA-256) maps to the model — null until it exists.

let answer: () => Response;

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => answer()),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function runWire(extra: Record<string, unknown> = {}) {
  return {
    id: 3,
    state: 'APPROVED',
    cutoff: '2024-11-17T08:00:00Z',
    cutoff_date: '2024-11-17',
    groups: ['MOVEMENT_HISTORY'],
    reason: 'Yearly archive',
    requested_by: { user_id: 1, display_name: 'Ann' },
    requested_at: '2027-11-10T04:00:00Z',
    preview: {
      components: 120,
      flows: 310,
      movements: 4210,
      first_occurred_at: '2023-01-03T10:00:00Z',
      last_occurred_at: '2024-11-16T10:00:00Z',
      estimated_archive_bytes: 812345,
    },
    started_at: null,
    archive_name: null,
    exported_at: null,
    exported: null,
    archive_bytes: null,
    manifest_sha256: null,
    verified_at: null,
    skipped: null,
    copied_at: null,
    copy_confirmed_at: null,
    copy_confirmed_by: null,
    cancelled_by: null,
    pre_purge_backup: null,
    purge_started_at: null,
    purged: null,
    receipt_name: null,
    receipt_sha256: null,
    finished_at: null,
    result: null,
    failure_reason: null,
    ...extra,
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

test('a purged run maps its state, result, backup, purge start, counts and receipt', async () => {
  answer = () =>
    json([
      runWire({
        state: 'COMPLETED_WITH_SKIPS',
        archive_name: 'run-3-20271110T042000Z',
        skipped: { components: 2, flows: 5 },
        pre_purge_backup: {
          name: '20271110T050102Z-pre-purge-run-3-20271110T042000Z',
          dump_started_at: '2027-11-10T05:01:02Z',
        },
        purge_started_at: '2027-11-10T05:03:00Z',
        purged: { components: 118, flows: 305, movements: 4190 },
        receipt_name: 'run-3-20271110T042000Z.purged.json',
        receipt_sha256: 'c'.repeat(64),
        finished_at: '2027-11-10T05:20:00Z',
        result: 'PURGED',
      }),
    ]);
  const [run] = await listArchiveRuns();
  expect(run).toMatchObject({
    state: 'COMPLETED_WITH_SKIPS',
    result: 'PURGED',
    skipped: { components: 2, flows: 5 },
    prePurgeBackup: {
      name: '20271110T050102Z-pre-purge-run-3-20271110T042000Z',
      dumpStartedAt: '2027-11-10T05:01:02Z',
    },
    purgeStartedAt: '2027-11-10T05:03:00Z',
    purged: { components: 118, flows: 305, movements: 4190 },
    receiptName: 'run-3-20271110T042000Z.purged.json',
    receiptSha256: 'c'.repeat(64),
  });
});

test('a run before its purge — and every step-1 run — has no purge evidence', async () => {
  answer = () =>
    json([
      runWire({ state: 'PURGING', purge_started_at: null }),
      runWire({ id: 2, state: 'COMPLETED', result: 'EXPORT_ONLY' }),
    ]);
  const runs = await listArchiveRuns();
  expect(runs.map((run) => run.state)).toEqual(['PURGING', 'COMPLETED']);
  for (const run of runs) {
    expect(run).toMatchObject({
      prePurgeBackup: null,
      purgeStartedAt: null,
      purged: null,
      receiptName: null,
      receiptSha256: null,
    });
  }
  expect(runs[1].result).toBe('EXPORT_ONLY');
});
