import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { Permission } from '../../api/roles';
import type { SessionUser } from '../../api/session';
import { ConnectivityContext } from '../../app/connectivity-context';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import { RouterContext } from '../../app/router-context';
import type { RouterValue } from '../../app/router-context';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { formatInstant } from './archive-runs';
import { HistoryArchivalSection } from './HistoryArchivalSection';

// Administration → History archival & purge (Phase 16, archival design B
// steps 1, 2 and 4): the settings card and its partial-PUT dialog (with the
// purge batch limits), the preview with its progress and the server's
// refusals, the request of a run (and a request whose answer was lost),
// what a run purges, and the runs table with the purge's states, results,
// counts and evidence, Cancel and Confirm off-host copy; (step 3, D17) the
// Permanent deletion requests card, shown only to holders of its key, with
// Cancel; and (step 4, D9–D11) the Automatic runs card with its dialog —
// the measure with its own limit, the approval step switched off, renewed
// and on again with the standing authorization and its validity — and the
// automatic runs in the table: Proposed and Dismissed, Automatic with the
// approval or the standing authorization, Approve… and Dismiss…; and
// (step 5, D16) the viewing settings, Open for viewing… with what loading
// does, each run's newest view with its state, View, Cancel… and Close
// viewing…, the refusals as given, all hidden from a User without the
// key. The /api surface is faked in memory with the contracts' routes and
// shapes; the server's messages are shown as given.

const T0 = '2026-11-17T08:00:00Z';
/** When the fake server approves or dismisses a proposed run. */
const T1 = '2026-11-18T09:12:00Z';
const SHA = 'a'.repeat(64);
const RECEIPT_SHA = 'b'.repeat(64);
/** The pre-purge backup disclosure, its hyphen non-breaking (U+2011). */
const PRE_PURGE = 'Pre‑purge backup';
/** The state pill of a run waiting for its off-host copy, its hyphen
 * non-breaking (U+2011). */
const WAITING_FOR_COPY = 'Waiting for the off‑host copy';

interface UserWire {
  user_id: number;
  display_name: string;
}

interface RunWire {
  id: number;
  state: string;
  trigger: string;
  cutoff: string;
  cutoff_date: string;
  groups: string[];
  reason: string;
  requested_by: UserWire | null;
  requested_at: string;
  approval: {
    approved_by: UserWire;
    approved_at: string;
    reason: string;
  } | null;
  dismissed_by: UserWire | null;
  authorization: {
    audit_event_id: number;
    authorized_by: UserWire | null;
    authorized_at: string | null;
  } | null;
  authorization_failed: boolean;
  preview: {
    components: number;
    flows: number;
    movements: number;
    first_occurred_at: string | null;
    last_occurred_at: string | null;
    estimated_archive_bytes: number;
  };
  started_at: string | null;
  archive_name: string | null;
  exported_at: string | null;
  exported: {
    components: number;
    flows: number;
    movements: number;
    lineage_edges: number;
    assigned_routes: number;
  } | null;
  archive_bytes: number | null;
  manifest_sha256: string | null;
  verified_at: string | null;
  skipped: { components: number; flows: number } | null;
  copied_at: string | null;
  copy_confirmed_at: string | null;
  copy_confirmed_by: { user_id: number; display_name: string } | null;
  cancelled_by: { user_id: number; display_name: string } | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  purge_started_at: string | null;
  purged: { components: number; flows: number; movements: number } | null;
  receipt_name: string | null;
  receipt_sha256: string | null;
  finished_at: string | null;
  result: string | null;
  failure_reason: string | null;
  viewable: boolean;
  view: { id: number; state: string } | null;
}

function run(id: number, overrides: Partial<RunWire> = {}): RunWire {
  return {
    id,
    state: 'APPROVED',
    trigger: 'MANUAL',
    cutoff: '2024-11-17T08:00:00Z',
    cutoff_date: '2024-11-17',
    groups: ['MOVEMENT_HISTORY'],
    reason: 'First archive',
    requested_by: { user_id: 1, display_name: 'Ann' },
    requested_at: '2026-11-17T09:00:00Z',
    approval: null,
    dismissed_by: null,
    authorization: null,
    authorization_failed: false,
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
    viewable: false,
    view: null,
    ...overrides,
  };
}

const WAITING = (id: number) =>
  run(id, {
    state: 'WAITING_FOR_COPY',
    started_at: '2026-11-17T09:20:00Z',
    archive_name: `run-${id}-20261117T092000Z`,
    exported_at: '2026-11-17T09:21:00Z',
    exported: {
      components: 120,
      flows: 310,
      movements: 4210,
      lineage_edges: 40,
      assigned_routes: 12,
    },
    archive_bytes: 790001,
    manifest_sha256: SHA,
    verified_at: '2026-11-17T09:22:00Z',
    skipped: { components: 0, flows: 0 },
  });

/** A run the job copied, backed up and is purging (step 2). */
const PURGING = (id: number) =>
  run(id, {
    ...WAITING(id),
    state: 'PURGING',
    copied_at: '2026-11-17T09:23:00Z',
    pre_purge_backup: {
      name: `20261117T092400Z-pre-purge-run-${id}-20261117T092000Z`,
      dump_started_at: '2026-11-17T09:24:00Z',
    },
    purge_started_at: '2026-11-17T09:26:00Z',
    purged: { components: 40, flows: 100, movements: 1400 },
  });

/** A run that purged every component it archived. */
const PURGED = (id: number) =>
  run(id, {
    ...PURGING(id),
    state: 'COMPLETED',
    purged: { components: 120, flows: 310, movements: 4210 },
    receipt_name: `run-${id}-20261117T092000Z.purged.json`,
    receipt_sha256: RECEIPT_SHA,
    finished_at: '2026-11-17T09:40:00Z',
    result: 'PURGED',
  });

/** An automatic run the archival job proposed (archival step 4). */
const AUTO_REASON =
  'Live Movement rows 5,120,334 above the limit 5,000,000; retention 24 months';

function proposed(id: number, overrides: Partial<RunWire> = {}): RunWire {
  return run(id, {
    state: 'PROPOSED',
    trigger: 'AUTOMATIC',
    requested_by: null,
    reason: AUTO_REASON,
    requested_at: '2026-11-18T08:20:00Z',
    ...overrides,
  });
}

interface StandingAuthorizationWire {
  audit_event_id: number;
  authorized_by: UserWire | null;
  authorized_at: string | null;
  reason: string | null;
  valid: boolean;
  problem: string | null;
}

interface Policy {
  retention_period_months: number | null;
  shortest_retention_months: number;
  archive_start_minute: number;
  archive_preview_seconds: number;
  archive_max_wait_days: number;
  archive_purge_batch_movements: number;
  archive_purge_batch_part_numbers: number;
  archive_automatic_runs: boolean;
  archive_measure: string;
  archive_limit_movement_rows: number;
  archive_limit_history_months: number;
  archive_limit_database_gib: number;
  archive_approval_required: boolean;
  archive_interval_days: number;
  archive_view_days: number;
  archive_view_max_loaded: number;
  standing_authorization: StandingAuthorizationWire | null;
}

/** The server's refusals of the approval step (B4 contract §5.1). */
const CONFIRMATION_REFUSAL =
  'Type RUN WITHOUT APPROVAL to confirm that automatic runs archive and purge history without anyone approving them first.';
const AUTHORIZATION_REASON_REFUSAL =
  'Enter the reason for letting automatic runs start without approval: 1 to 500 characters of plain text.';
const CONFIRMATION_UNNEEDED =
  'Type the confirmation only to switch off the approval step of automatic runs.';
/** The standing authorization's problem once its User lost the key. */
const KEY_LOST =
  'The User who switched off the approval step of automatic runs, Ann Admin (user 4), no longer holds Run history archival and purge. No automatic run is created until a holder of the key saves the approval setting again.';

/** A permanent deletion request (archival step 3, D17) as the API
 * answers it. */
interface DeletionWire {
  id: number;
  state: string;
  scope: { work_order_numbers: string[]; part_numbers: string[] };
  reason: string;
  requested_by: { user_id: number; display_name: string } | null;
  requested_by_operator: string | null;
  requested_at: string;
  work_orders: {
    id: number;
    work_order_number: string | null;
    internal: boolean;
    named: boolean;
    part_numbers: string[];
    demand_lines: number;
  }[];
  part_numbers: { part_number: string; master: boolean }[];
  rows: Record<string, number>;
  started_at: string | null;
  backup_label: string | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  finished_at: string | null;
  cancelled_by: { user_id: number; display_name: string } | null;
  failure_reason: string | null;
}

function deletion(
  id: number,
  overrides: Partial<DeletionWire> = {},
): DeletionWire {
  return {
    id,
    state: 'APPROVED',
    scope: { work_order_numbers: ['007201'], part_numbers: [] },
    reason: 'Test data entered by mistake',
    requested_by: { user_id: 9, display_name: 'Sam Super' },
    requested_by_operator: null,
    requested_at: '2026-11-18T10:00:00Z',
    work_orders: [
      {
        id: 1,
        work_order_number: '007201',
        internal: false,
        named: true,
        part_numbers: ['A-100'],
        demand_lines: 1,
      },
    ],
    part_numbers: [],
    rows: {
      audit_events: 4,
      part_movements: 12,
      quantity_flow_lineage: 0,
      quantity_flows: 2,
      assigned_route_steps: 6,
      assigned_routes: 2,
      work_order_allocations: 0,
      work_order_demands: 1,
      work_orders: 1,
      part_numbers: 0,
    },
    started_at: null,
    backup_label: null,
    pre_purge_backup: null,
    finished_at: null,
    cancelled_by: null,
    failure_reason: null,
    ...overrides,
  };
}

/** A view of a run's archive (archival step 5, D16) as the API answers
 * it. */
interface ViewWire {
  id: number;
  run_id: number;
  state: string;
  requested_by: UserWire;
  requested_at: string;
  to_load: {
    archive_name: string | null;
    movements: number;
    flows: number;
    lineage_edges: number;
    assigned_routes: number;
    archive_bytes: number;
  };
  started_at: string | null;
  opened_at: string | null;
  open_until: string | null;
  loaded: {
    movements: number;
    flows: number;
    lineage_edges: number;
    assigned_routes: number;
    names: number;
  } | null;
  closing_started_at: string | null;
  closed_by: UserWire | null;
  cancelled_by: UserWire | null;
  finished_at: string | null;
  failure_reason: string | null;
}

/** When the views opened, and until when they stay open (7 days). */
const OPENED = '2026-11-18T10:05:33.000000Z';
const OPEN_UNTIL = '2026-11-25T10:05:33.000000Z';

function view(
  id: number,
  runId: number,
  overrides: Partial<ViewWire> = {},
): ViewWire {
  return {
    id,
    run_id: runId,
    state: 'REQUESTED',
    requested_by: { user_id: 90, display_name: 'Ada Admin' },
    requested_at: '2026-11-18T10:00:00.000000Z',
    to_load: {
      archive_name: `run-${runId}-20261117T092000Z`,
      movements: 4210,
      flows: 310,
      lineage_edges: 40,
      assigned_routes: 12,
      archive_bytes: 790001,
    },
    started_at: null,
    opened_at: null,
    open_until: null,
    loaded: null,
    closing_started_at: null,
    closed_by: null,
    cancelled_by: null,
    finished_at: null,
    failure_reason: null,
    ...overrides,
  };
}

/** An open view of run `runId`. */
const OPEN_VIEW = (id: number, runId: number) =>
  view(id, runId, {
    state: 'OPEN',
    started_at: '2026-11-18T10:05:00.000000Z',
    opened_at: OPENED,
    open_until: OPEN_UNTIL,
    loaded: {
      movements: 4210,
      flows: 310,
      lineage_edges: 40,
      assigned_routes: 12,
      names: 95,
    },
  });

/** A finished run whose verified archive can be opened for viewing. */
const VIEWABLE = (id: number, overrides: Partial<RunWire> = {}) =>
  run(id, { ...PURGED(id), viewable: true, ...overrides });

let policy: Policy;
let runs: RunWire[];
let views: ViewWire[];
let deletions: DeletionWire[];
let writes: { method: string; url: string; body: unknown }[];
/** A refusal or lost answer per route key, if set (`PUT policy`,
 * `GET preview`, `POST runs`, `POST cancel`, `POST copy`, `GET
 * deletions`, `POST deletion cancel`, `GET views`, `POST views`, `POST
 * view cancel`, `POST view close`). */
let failures: Record<string, { status: number; detail: string } | 'network'>;
/** While set, a preview answers only once it resolves. */
let previewHold: Promise<void> | null;
let session: SessionValue;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body: unknown = init?.body ? JSON.parse(String(init.body)) : undefined;
  if (method !== 'GET') writes.push({ method, url, body });
  const fail = (key: string): Response | null => {
    const failure = failures[key];
    if (failure === undefined) return null;
    if (failure === 'network') throw new TypeError('Failed to fetch');
    return json({ detail: failure.detail }, failure.status);
  };
  if (url === '/api/policies/data-retention') {
    if (method === 'PUT') {
      const refused = fail('PUT policy');
      if (refused) return refused;
      const changes = { ...(body as Record<string, unknown>) };
      const { reason, confirmation } = changes;
      delete changes.reason;
      delete changes.confirmation;
      const after = { ...policy, ...changes } as Policy;
      // The approval step as the server checks it (B4 contract §2.2,
      // §5.1): switching it off, or renewing it while it stays off, needs
      // the typed confirmation and a reason; the audited change is the
      // standing authorization.
      const given = confirmation !== undefined && confirmation !== null;
      let granted = false;
      if (after.archive_approval_required) {
        if (given) return json({ detail: CONFIRMATION_UNNEEDED }, 422);
        after.standing_authorization = null;
      } else if (!given) {
        if (policy.archive_approval_required) {
          return json({ detail: CONFIRMATION_REFUSAL }, 422);
        }
      } else if (
        String(confirmation)
          .split(/\s+/)
          .filter(Boolean)
          .join(' ')
          .toUpperCase() !== 'RUN WITHOUT APPROVAL'
      ) {
        return json({ detail: CONFIRMATION_REFUSAL }, 422);
      } else if (typeof reason !== 'string' || reason.trim() === '') {
        return json({ detail: AUTHORIZATION_REASON_REFUSAL }, 422);
      } else {
        granted = true;
      }
      if (granted) {
        after.standing_authorization = {
          audit_event_id:
            (policy.standing_authorization?.audit_event_id ?? 40) + 1,
          authorized_by: { user_id: 90, display_name: 'Ada Admin' },
          authorized_at: T0,
          reason: (reason as string).trim(),
          valid: true,
          problem: null,
        };
      }
      policy = after;
    }
    return json({ ...policy, updated_at: T0 });
  }
  if (url.startsWith('/api/history-archive/preview')) {
    if (previewHold) await previewHold;
    const refused = fail('GET preview');
    if (refused) return refused;
    const cutoff = new URL(url, 'http://x').searchParams.get('cutoff_date');
    return json({
      cutoff_date: cutoff ?? '2024-11-17',
      cutoff: '2024-11-17T08:00:00Z',
      latest_cutoff_date: '2024-11-17',
      site_timezone: 'America/Los_Angeles',
      components: cutoff === '2020-01-01' ? 0 : 120,
      flows: cutoff === '2020-01-01' ? 0 : 310,
      movements: cutoff === '2020-01-01' ? 0 : 4210,
      first_occurred_at:
        cutoff === '2020-01-01' ? null : '2023-01-03T10:00:00Z',
      last_occurred_at: cutoff === '2020-01-01' ? null : '2024-11-16T10:00:00Z',
      estimated_archive_bytes: cutoff === '2020-01-01' ? 0 : 812345,
      duration_ms: 3412,
      time_limit_seconds: policy.archive_preview_seconds,
    });
  }
  if (url === '/api/history-archive/runs' && method === 'GET') {
    return json(runs);
  }
  if (url === '/api/history-archive/runs' && method === 'POST') {
    const failure = failures['POST runs'];
    if (
      failure !== undefined &&
      failure !== 'network' &&
      failure.status < 500
    ) {
      return json({ detail: failure.detail }, failure.status);
    }
    const request = body as { reason: string; cutoff_date: string | null };
    const created = run(runs.length + 1, {
      reason: request.reason,
      cutoff_date: request.cutoff_date ?? '2024-11-17',
    });
    runs = [created, ...runs];
    // The run is created even when its answer is lost.
    const refused = fail('POST runs');
    if (refused) return refused;
    return json(created, 201);
  }
  const action =
    /^\/api\/history-archive\/runs\/(\d+)\/(cancel|copy-confirmation|approve|dismiss)$/.exec(
      url,
    );
  if (action && method === 'POST') {
    const kind = action[2];
    // Approve checks its reason before the run (B4 contract §5.3).
    if (kind === 'approve') {
      const reason = (body as { reason?: unknown } | undefined)?.reason;
      if (typeof reason !== 'string' || reason.trim() === '') {
        return json(
          {
            detail:
              'Enter the reason for approving this run: 1 to 500 characters of plain text.',
          },
          422,
        );
      }
    }
    const target = runs.find((r) => r.id === Number(action[1]));
    if (!target)
      return json({ detail: `There is no archival run ${action[1]}.` }, 404);
    const refused = fail(
      {
        cancel: 'POST cancel',
        'copy-confirmation': 'POST copy',
        approve: 'POST approve',
        dismiss: 'POST dismiss',
      }[kind] as string,
    );
    if (refused) return refused;
    const me = { user_id: 90, display_name: 'Ada Admin' };
    if (kind === 'approve') {
      if (target.state !== 'PROPOSED') {
        return json(
          {
            detail: `Run ${target.id} is ${target.state}; only a run waiting for approval (PROPOSED) can be approved.`,
          },
          409,
        );
      }
      target.state = 'APPROVED';
      target.approval = {
        approved_by: me,
        approved_at: T1,
        reason: (body as { reason: string }).reason.trim(),
      };
    } else if (kind === 'dismiss') {
      if (['APPROVED', 'WAITING_FOR_COPY'].includes(target.state)) {
        return json(
          {
            detail: `Run ${target.id} is ${target.state}; only a run waiting for approval (PROPOSED) can be dismissed. Cancel it instead.`,
          },
          409,
        );
      }
      if (['RUNNING', 'PURGING'].includes(target.state)) {
        return json(
          {
            detail: `Run ${target.id} is running on the server now and cannot be dismissed. Wait until it stops.`,
          },
          409,
        );
      }
      if (target.state !== 'PROPOSED' && target.state !== 'DISMISSED') {
        return json(
          {
            detail: `Run ${target.id} is finished (${target.state}) and cannot be dismissed.`,
          },
          409,
        );
      }
      if (target.state === 'PROPOSED') {
        target.state = 'DISMISSED';
        target.dismissed_by = me;
        target.finished_at = T1;
      }
    } else if (kind === 'cancel') {
      if (target.state === 'PROPOSED') {
        return json(
          {
            detail: `Run ${target.id} waits for approval (PROPOSED): dismiss it instead of cancelling it.`,
          },
          409,
        );
      }
      target.state = 'CANCELLED';
      target.cancelled_by = me;
      target.finished_at = T0;
    } else {
      target.copy_confirmed_at = T0;
      target.copy_confirmed_by = me;
    }
    return json(target);
  }
  if (url.startsWith('/api/history-archive/views') && method === 'GET') {
    const refused = fail('GET views');
    if (refused) return refused;
    return json(views);
  }
  if (url === '/api/history-archive/views' && method === 'POST') {
    // The server's order (B5 contract §5.3): the run, its state, its
    // verification, an active view of it, the runs loaded at once.
    const runId = (body as { run_id: number }).run_id;
    const target = runs.find((r) => r.id === runId);
    if (!target)
      return json({ detail: `There is no archival run ${runId}.` }, 404);
    if (!target.viewable) {
      return json(
        {
          detail: `Run ${runId} ended ${target.state} before its archive was verified, so it has no archive to view.`,
        },
        409,
      );
    }
    const active = views.filter((v) =>
      ['REQUESTED', 'LOADING', 'OPEN'].includes(v.state),
    );
    const same = active.find((v) => v.run_id === runId);
    if (same) {
      return json(
        {
          detail: `Run ${runId} is already open for viewing, or waiting to be loaded (view ${same.id}, ${same.state}).`,
        },
        409,
      );
    }
    const failure = failures['POST views'];
    if (
      failure !== undefined &&
      failure !== 'network' &&
      failure.status < 500
    ) {
      return json({ detail: failure.detail }, failure.status);
    }
    const created = view(Math.max(0, ...views.map((v) => v.id)) + 1, runId);
    views = [created, ...views];
    target.view = { id: created.id, state: created.state };
    // The view is requested even when its answer is lost.
    const refused = fail('POST views');
    if (refused) return refused;
    return json(created, 201);
  }
  const viewAction =
    /^\/api\/history-archive\/views\/(\d+)\/(cancel|close)$/.exec(url);
  if (viewAction && method === 'POST') {
    const target = views.find((v) => v.id === Number(viewAction[1]));
    if (!target) {
      return json(
        { detail: `There is no archive view ${viewAction[1]}.` },
        404,
      );
    }
    const refused = fail(
      viewAction[2] === 'cancel' ? 'POST view cancel' : 'POST view close',
    );
    if (refused) return refused;
    const me = { user_id: 90, display_name: 'Ada Admin' };
    const name = `View ${target.id} of run ${target.run_id}`;
    if (viewAction[2] === 'cancel') {
      if (target.state === 'OPEN') {
        return json(
          {
            detail: `${name} is open (OPEN): close it instead of cancelling it.`,
          },
          409,
        );
      }
      if (target.state === 'REQUESTED') {
        target.state = 'CANCELLED';
        target.cancelled_by = me;
        target.finished_at = T1;
      }
    } else {
      if (target.state === 'REQUESTED') {
        return json(
          {
            detail: `${name} waits to be loaded (REQUESTED): cancel it instead of closing it.`,
          },
          409,
        );
      }
      if (target.state === 'OPEN') {
        target.state = 'CLOSING';
        target.closing_started_at = T1;
        target.closed_by = me;
      }
    }
    const owner = runs.find((r) => r.id === target.run_id);
    if (owner?.view?.id === target.id) {
      owner.view = { id: target.id, state: target.state };
    }
    return json(target);
  }
  if (url === '/api/permanent-deletion/requests' && method === 'GET') {
    const refused = fail('GET deletions');
    if (refused) return refused;
    return json(deletions);
  }
  const deletionCancel =
    /^\/api\/permanent-deletion\/requests\/(\d+)\/cancel$/.exec(url);
  if (deletionCancel && method === 'POST') {
    const target = deletions.find((d) => d.id === Number(deletionCancel[1]));
    if (!target) {
      return json(
        { detail: `There is no deletion request ${deletionCancel[1]}.` },
        404,
      );
    }
    const refused = fail('POST deletion cancel');
    if (refused) return refused;
    target.state = 'CANCELLED';
    target.cancelled_by = { user_id: 90, display_name: 'Ada Admin' };
    target.finished_at = T0;
    return json(target);
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
}

function sessionWith(permissions: Permission[]): SessionValue {
  const user: SessionUser = {
    id: 90,
    loginName: 'admin',
    displayName: 'Ada Admin',
    roleId: 1,
    roleName: 'Administrator',
    avatarUpdatedAt: null,
    permissions,
    mustChangePassword: false,
    sessionExpiresAt: null,
    themePreference: null,
  };
  return {
    status: 'signed-in',
    user,
    setupOpen: false,
    checking: false,
    endedBy: null,
    can: (permission) => hasPermission(user, permission),
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    openTwoStepSignIn: vi.fn(),
    openEmailAddress: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
}

/** Where View… navigates (the archive viewer has its own suite). */
let navigated: string[];

function renderSection(status: ConnectivityStatus = 'connected') {
  const router: RouterValue = {
    route: { view: 'administration' },
    path: '/administration',
    navigate: (to) => navigated.push(to),
    setNavigationGuard: () => undefined,
    setManagementReadable: () => undefined,
  };
  return render(
    <RouterContext.Provider value={router}>
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <SessionContext.Provider value={session}>
          <HistoryArchivalSection />
        </SessionContext.Provider>
      </ConnectivityContext.Provider>
    </RouterContext.Provider>,
  );
}

beforeEach(() => {
  policy = {
    retention_period_months: 24,
    shortest_retention_months: 12,
    archive_start_minute: 20,
    archive_preview_seconds: 90,
    archive_max_wait_days: 7,
    archive_purge_batch_movements: 50000,
    archive_purge_batch_part_numbers: 20,
    archive_automatic_runs: false,
    archive_measure: 'LIVE_MOVEMENT_ROWS',
    archive_limit_movement_rows: 5000000,
    archive_limit_history_months: 1,
    archive_limit_database_gib: 20,
    archive_approval_required: true,
    archive_interval_days: 7,
    archive_view_days: 7,
    archive_view_max_loaded: 1,
    standing_authorization: null,
  };
  runs = [];
  views = [];
  navigated = [];
  deletions = [];
  writes = [];
  failures = {};
  previewHold = null;
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE']);
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function card(title: string): HTMLElement {
  return screen
    .getByRole('heading', { name: title })
    .closest('section') as HTMLElement;
}

function rows(container: ParentNode): Record<string, string> {
  return Object.fromEntries(
    Array.from(container.querySelectorAll('.prow'), (row) => [
      row.querySelector('.k')?.textContent ?? '',
      row.querySelector('.v')?.textContent ?? '',
    ]),
  );
}

async function loaded() {
  await screen.findByText('24 months (2 years)');
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: 'Loading runs' })).toBeNull(),
  );
}

/** The row of run `id` in the runs table. */
function runRowElement(id: number): HTMLElement {
  const row = within(within(card('Runs')).getByRole('table'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id));
  if (!row) throw new Error(`No row for run ${id}`);
  return row;
}

/** The cells of the runs table's row of run `id`, by column: the State is
 * its pill, and `Actions` the buttons under it. */
function runRow(id: number): Record<string, string> {
  const table = within(card('Runs')).getByRole('table');
  const headers = within(table)
    .getAllByRole('columnheader')
    .map((th) => th.textContent ?? '');
  const row = runRowElement(id);
  const cells = Array.from(row.querySelectorAll('td'), (td) => td.textContent);
  const state = row.querySelector('td[data-label="State"]') as HTMLElement;
  return {
    ...Object.fromEntries(headers.map((h, i) => [h, cells[i] ?? ''])),
    State: state.querySelector('.pillnav')?.textContent ?? '',
    Actions: Array.from(
      state.querySelectorAll('button'),
      (button) => button.textContent,
    ).join(' / '),
  };
}

/** The lines a cell of run `id` shows outside its folded disclosures. */
function runLines(id: number, label: string): string[] {
  const cell = runRowElement(id).querySelector(
    `td[data-label="${label}"]`,
  ) as HTMLElement;
  return Array.from(
    cell.querySelectorAll(':scope > .admin-runlines > :not(details)'),
    (line) => line.textContent ?? '',
  );
}

test('HA-1: the section shows the settings, the preview and the runs, and says what a run purges', async () => {
  runs = [
    run(3, { state: 'RUNNING', started_at: '2026-11-17T09:20:00Z' }),
    run(2, {
      ...WAITING(2),
      state: 'COMPLETED',
      result: 'EXPORT_ONLY',
      copied_at: '2026-11-17T09:23:00Z',
      finished_at: '2026-11-17T09:23:00Z',
    }),
    run(1, {
      state: 'FAILED',
      failure_reason: 'The archive folder is not usable.',
      finished_at: '2026-11-16T09:20:00Z',
    }),
  ];
  renderSection();
  await loaded();

  expect(rows(card('Retention and schedule'))).toEqual({
    'Retention period': '24 months (2 years)',
    'Shortest allowed period': '12 months (1 year)',
    'Start minute': '20 minutes past each hour',
    'Preview time limit': '90 seconds',
    'Report a waiting run after': '7 days',
    'Movements per purge batch': '50,000',
    'Part Numbers per purge batch': '20',
    'A loaded archive stays open': '7 days',
    'Runs loaded for viewing at once': '1',
  });
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeEnabled();
  expect(card('Preview').textContent).toContain(
    'After the off-host copy and a pre-purge backup, a run deletes the archived Movement history from the database. Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.',
  );
  expect(card('Runs').textContent).toContain(
    'Each run exports the Movement history before its cutoff to archive files on the server and, after the off-host copy and a pre-purge backup, purges it from the database.',
  );
  // Said once in the runs card, never per run.
  expect(
    within(card('Runs')).getAllByText(
      'Archived commands can no longer be undone.',
    ),
  ).toHaveLength(1);
  expect(card('Runs').textContent).not.toContain('nothing is deleted');
  expect(screen.queryByText(/View only/)).toBeNull();

  // Six columns keep the table inside its card at a 1024 px window.
  expect(
    within(card('Runs'))
      .getAllByRole('columnheader')
      .map((th) => th.textContent),
  ).toEqual([
    'Run',
    'State',
    'Cutoff and counts',
    'Requested by',
    'Times',
    'Result',
  ]);
  expect(runRow(3)).toMatchObject({
    State: 'Running',
    Result: '—',
    Actions: '',
  });
  // The cutoff date tops the counts; no export yet: the preview's numbers,
  // Movements first, marked as such.
  expect(runLines(3, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,210 Movements',
    '120 components · 310 flows',
    'From the preview',
  ]);
  // Requested and the latest step only; nothing more to fold away.
  expect(runRow(3).Times).toMatch(/^Requested .+Started .+$/);
  expect(runRow(3).Times).not.toContain('All times');
  expect(runLines(3, 'Requested by')).toEqual(['Ann', 'First archive']);
  expect(runRow(2).State).toBe('Completed');
  // The result, then the archive folder and its size.
  expect(runLines(2, 'Result')).toEqual([
    'Exported to files; nothing deleted.',
    'run-2-20261117T092000Z',
    '771.5 KB',
  ]);
  // The export equals the preview: one set of numbers, no skipped line.
  expect(runLines(2, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,210 Movements',
    '120 components · 310 flows',
  ]);
  // The latest step is Finished; the whole timeline is under All times.
  const times = within(card('Runs'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === '2')!
    .querySelector('td[data-label="Times"]') as HTMLElement;
  expect(
    Array.from(
      times.querySelectorAll(':scope > .admin-runlines > .admin-runtime'),
      (line) => line.textContent?.split(' ')[0],
    ),
  ).toEqual(['Requested', 'Finished']);
  const details = times.querySelector('details') as HTMLDetailsElement;
  expect(within(details).getByText('All times').tagName).toBe('SUMMARY');
  expect(
    Array.from(
      details.querySelectorAll('.admin-runtime'),
      (line) => line.textContent?.split(' ')[0],
    ),
  ).toEqual([
    'Requested',
    'Started',
    'Exported',
    'Verified',
    'Copied',
    'Finished',
  ]);
  expect(runRow(1)).toMatchObject({
    State: 'Failed',
    Result: 'The archive folder is not usable.',
  });
  // A requested run has no approval and no standing authorization.
  expect(runLines(1, 'Requested by')).toEqual(['Ann', 'First archive']);
  expect(writes).toEqual([]);
});

test('HA-1b: a run whose export differs from its preview shows both, and skipped components when there are any', async () => {
  runs = [
    {
      ...WAITING(6),
      exported: {
        components: 118,
        flows: 305,
        movements: 4150,
        lineage_edges: 40,
        assigned_routes: 12,
      },
      skipped: { components: 2, flows: 5 },
    },
  ];
  renderSection();
  await loaded();
  expect(runLines(6, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,150 Movements',
    '118 components · 305 flows',
    'Preview: 4,210 Movements · 120 components · 310 flows',
    'Skipped (changed during the run): 2 components · 5 flows',
  ]);
  expect(runRow(6).Times).toMatch(/^Requested .+Verified .+All times/);
});

test('HA-2: without the archival permission the section is view-only: values, runs, no preview and no action', async () => {
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  runs = [WAITING(4)];
  renderSection();
  await loaded();

  expect(
    screen.getByText(
      'View only — changing this needs the Run history archival and purge permission.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Preview' })).toBeNull();
  // No action under any state: the only button is the runs' Reload.
  expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
    'Reload',
  ]);
  expect(runRow(4)).toMatchObject({ State: WAITING_FOR_COPY, Actions: '' });
  // Without the Preview, Automatic runs stands beside Retention and
  // schedule.
  const columns = document.querySelectorAll('.admin-cardcolumn');
  expect(
    Array.from(columns, (column) =>
      Array.from(column.querySelectorAll('h2'), (h2) => h2.textContent),
    ),
  ).toEqual([['Retention and schedule'], ['Automatic runs']]);
  // The preview needs the permission: it is never asked for.
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => String(input).includes('/preview')),
  ).toBe(false);
});

test('HA-3: Edit sends only the changed settings and shows the server refusal as given', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(
    within(dialog).getByLabelText('Retention period (months)'),
  ).toHaveValue(24);
  fireEvent.change(within(dialog).getByLabelText('Start minute (0–59)'), {
    target: { value: '45' },
  });
  fireEvent.change(
    within(dialog).getByLabelText('Report a waiting run after (days)'),
    {
      target: { value: '14' },
    },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_start_minute: 45, archive_max_wait_days: 14 },
    },
  ]);
  expect(
    await screen.findByText('45 minutes past each hour'),
  ).toBeInTheDocument();
  expect(screen.getByText('14 days')).toBeInTheDocument();

  // A refusal keeps the dialog and its entries, with the server's text.
  writes = [];
  const REFUSAL =
    'The preview time limit must be a whole number of seconds from 10 to 600.';
  failures['PUT policy'] = { status: 422, detail: REFUSAL };
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const again = screen.getByRole('dialog', { name: 'Retention and schedule' });
  const seconds = within(again).getByLabelText('Preview time limit (seconds)');
  fireEvent.change(seconds, { target: { value: '5' } });
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(REFUSAL);
  expect(seconds).toHaveValue(5);
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_preview_seconds: 5 },
    },
  ]);
});

test('HA-4: a change of the shortest allowed period asks for its reason and sends it; no retention period sends null', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(within(dialog).queryByLabelText(/Reason for changing/)).toBeNull();
  fireEvent.change(
    within(dialog).getByLabelText('Shortest allowed period (months)'),
    { target: { value: '18' } },
  );
  fireEvent.change(within(dialog).getByLabelText(/Reason for changing/), {
    target: { value: 'Audit asks for 18 months' },
  });
  fireEvent.click(
    within(dialog).getByRole('radio', { name: 'No retention period' }),
  );
  expect(
    within(dialog).queryByLabelText('Retention period (months)'),
  ).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toEqual({
    retention_period_months: null,
    shortest_retention_months: 18,
    reason: 'Audit asks for 18 months',
  });
  expect(await screen.findByText('No retention period')).toBeInTheDocument();

  // A field that is not a whole number blocks the save.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const next = screen.getByRole('dialog', { name: 'Retention and schedule' });
  fireEvent.change(within(next).getByLabelText('Start minute (0–59)'), {
    target: { value: '2.5' },
  });
  expect(within(next).getByRole('alert')).toHaveTextContent(
    'Enter a whole number.',
  );
  expect(
    within(next).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

test('HA-5: the preview asks for the latest allowed cutoff, shows its progress and then its numbers', async () => {
  let release!: () => void;
  previewHold = new Promise((resolve) => (release = resolve));
  renderSection();
  await loaded();
  const preview = card('Preview');
  const request = within(preview).getByRole('button', {
    name: 'Request a run…',
  });
  expect(request).toBeDisabled();
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));

  expect(
    await within(preview).findByText(
      /^Running the preview… \d+ s \(time limit 90 s\)$/,
    ),
  ).toBeInTheDocument();
  expect(
    within(preview).getByRole('button', { name: 'Running preview…' }),
  ).toBeDisabled();
  // The rest of the page stays usable meanwhile.
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeEnabled();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.map(([input]) => String(input))
      .filter((url) => url.includes('/preview')),
  ).toEqual(['/api/history-archive/preview']);

  release();
  await within(preview).findByText('Preview took');
  expect(rows(preview)).toEqual({
    'Cutoff date': 'Nov 17, 2024',
    Components: '120',
    Flows: '310',
    Movements: '4,210',
    'First and last Movement': 'Jan 03, 2023 – Nov 16, 2024',
    'Estimated archive size': '793.3 KB',
    'Preview took': '3.4 s',
  });
  expect(within(preview).getByLabelText('Cutoff date')).toHaveValue(
    '2024-11-17',
  );
  expect(preview.textContent).toContain(
    'The latest date the retention period allows is Nov 17, 2024.',
  );
  expect(request).toBeEnabled();

  // Another date drops the preview until it is run for that date.
  fireEvent.change(within(preview).getByLabelText('Cutoff date'), {
    target: { value: '2020-01-01' },
  });
  expect(within(preview).queryByText('Preview took')).toBeNull();
  expect(request).toBeDisabled();
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  await within(preview).findByText('Preview took');
  expect(preview.textContent).toContain(
    'Nothing is eligible at this cutoff, so there is nothing to request.',
  );
  expect(request).toBeDisabled();
  expect(writes).toEqual([]);
});

test('HA-6: a refused preview shows the server message as given', async () => {
  const NO_PERIOD =
    'No retention period is set, so no history is eligible for archival. Set a retention period first.';
  failures['GET preview'] = { status: 409, detail: NO_PERIOD };
  renderSection();
  await loaded();
  const preview = card('Preview');
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  expect(await within(preview).findByRole('alert')).toHaveTextContent(
    NO_PERIOD,
  );
  expect(
    within(preview).getByRole('button', { name: 'Request a run…' }),
  ).toBeDisabled();
});

async function previewed(): Promise<HTMLElement> {
  const preview = card('Preview');
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  await within(preview).findByText('Preview took');
  fireEvent.click(
    within(preview).getByRole('button', { name: 'Request a run…' }),
  );
  return screen.getByRole('dialog', { name: 'Request an archival run' });
}

test('HA-7: a request shows the preview numbers, posts the reason and the cutoff, and reloads the runs', async () => {
  renderSection();
  await loaded();
  const dialog = await previewed();
  expect(dialog.textContent).toContain(
    '120 components · 310 flows · 4,210 Movements',
  );
  // What the run does, before it is requested: the purge and what follows.
  expect(dialog.textContent).toContain(
    'The run exports the Movement history before Nov 17, 2024 to archive files on the server and, after the off-host copy and a pre-purge backup, deletes it from the database. Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.',
  );
  expect(dialog.textContent).not.toContain('deletes nothing');

  // A definite refusal keeps the dialog and the reason.
  const OPEN =
    'Run 9 is still open (APPROVED). Cancel it or wait until it finishes before requesting a new run.';
  failures['POST runs'] = { status: 409, detail: OPEN };
  const reason = within(dialog).getByLabelText(/Reason/);
  fireEvent.change(reason, { target: { value: 'Yearly archive' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(OPEN);
  expect(reason).toHaveValue('Yearly archive');
  expect(runs).toEqual([]);

  delete failures['POST runs'];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Request an archival run' }),
    ).toBeNull(),
  );
  expect(writes[writes.length - 1]).toEqual({
    method: 'POST',
    url: '/api/history-archive/runs',
    body: { reason: 'Yearly archive', cutoff_date: '2024-11-17' },
  });
  expect(
    await within(card('Preview')).findByText(
      '✓ Run 1 is requested. The archival job on the server starts it at its next Start minute.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(runRow(1).State).toBe('Approved — waiting for the job'),
  );
});

test('HA-8: a request whose answer was lost (524) reloads the runs and asks to check them', async () => {
  renderSection();
  await loaded();
  const dialog = await previewed();
  failures['POST runs'] = { status: 524, detail: '' };
  fireEvent.change(within(dialog).getByLabelText(/Reason/), {
    target: { value: 'Yearly archive' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));

  expect(await within(card('Preview')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so the run may or may not have been requested. Check the runs table before requesting again.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Request an archival run' }),
  ).toBeNull();
  // The run the server created shows after the reload.
  await waitFor(() =>
    expect(runRow(1).State).toBe('Approved — waiting for the job'),
  );
  expect(writes.filter((w) => w.method === 'POST')).toHaveLength(1);
});

test('HA-9: Cancel asks first, is offered only for a requested or waiting run, and posts once', async () => {
  runs = [WAITING(2), run(1, { state: 'CANCELLED', finished_at: T0 })];
  renderSection();
  await loaded();
  expect(screen.queryByRole('button', { name: 'Cancel run 1…' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 2…' }));
  const confirm = screen.getByRole('dialog', { name: 'Cancel run 2?' });
  expect(confirm.textContent).toContain(
    'Its archive folder stays on the server',
  );
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep run (Esc)' }),
  );
  expect(writes).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 2…' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Cancel run 2?' })).getByRole(
      'button',
      { name: 'Cancel run' },
    ),
  );
  expect(
    await within(card('Runs')).findByText('✓ Run 2 is cancelled.'),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/2/cancel',
      body: undefined,
    },
  ]);
  await waitFor(() => expect(runRow(2).State).toBe('Cancelled'));
  // The waiting run's archive folder stays on the server.
  expect(runLines(2, 'Result')).toEqual([
    'Cancelled by Ada Admin.',
    'run-2-20261117T092000Z',
    '771.5 KB',
  ]);
  expect(screen.queryByRole('button', { name: 'Cancel run 2…' })).toBeNull();
});

test('HA-10: a refused cancel shows the server text; a lost answer reloads the runs', async () => {
  runs = [run(5)];
  renderSection();
  await loaded();
  const RUNNING =
    'Run 5 is running on the server now and cannot be cancelled. Wait until it stops.';
  failures['POST cancel'] = { status: 409, detail: RUNNING };
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 5…' }));
  const confirm = screen.getByRole('dialog', { name: 'Cancel run 5?' });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel run' }));
  expect(await within(confirm).findByRole('alert')).toHaveTextContent(RUNNING);

  failures['POST cancel'] = { status: 502, detail: 'Bad gateway.' };
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel run' }));
  expect(await within(card('Runs')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so run 5 may or may not have been cancelled.',
  );
  expect(screen.queryByRole('dialog', { name: 'Cancel run 5?' })).toBeNull();
});

test('HA-11: Confirm off-host copy shows the archive and its SHA-256, needs the copy ticked and sends the SHA-256', async () => {
  runs = [WAITING(3)];
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm the off-host copy of run 3…' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm the off-host copy of run 3',
  });
  expect(dialog.textContent).toContain('run-3-20261117T092000Z');
  expect(dialog.textContent).toContain(SHA);
  expect(dialog.textContent).toContain('Hyper Backup task');
  expect(dialog.textContent).toContain(
    'At its next start the archival job on the server then takes the pre-purge backup and purges the archived history from the database.',
  );
  const confirm = within(dialog).getByRole('button', { name: 'Confirm copy' });
  expect(confirm).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('checkbox', {
      name: 'The archive folder is copied off the server',
    }),
  );
  fireEvent.click(confirm);

  expect(
    await within(card('Runs')).findByText(
      '✓ The off-host copy of run 3 is confirmed. At its next start the archival job on the server takes the pre-purge backup and purges the archived history.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/3/copy-confirmation',
      body: { manifest_sha256: SHA },
    },
  ]);
  await waitFor(() =>
    expect(runRow(3).State).toBe('Copy confirmed — waiting for the job'),
  );
  expect(
    screen.queryByRole('button', {
      name: 'Confirm the off-host copy of run 3…',
    }),
  ).toBeNull();
  // Still cancellable until the job finishes it.
  expect(screen.getByRole('button', { name: 'Cancel run 3…' })).toBeEnabled();
});

test('HA-12: offline every write is blocked while the values stay readable', async () => {
  runs = [WAITING(3)];
  renderSection('unavailable');
  await loaded();
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Run preview' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cancel run 3…' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Confirm the off-host copy of run 3…' }),
  ).toBeDisabled();
  expect(runLines(3, 'Result')).toEqual(['run-3-20261117T092000Z', '771.5 KB']);
});

test('HA-13: a failed load of the runs offers Retry', async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input) === '/api/history-archive/runs'
      ? json({ detail: 'Database unavailable.' }, 500)
      : original(input, init),
  );
  renderSection();
  expect(
    await screen.findByText('The archival runs could not be loaded.'),
  ).toBeInTheDocument();
  vi.mocked(fetch).mockImplementation(original);
  runs = [run(1)];
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(runRow(1).State).toBe('Approved — waiting for the job'),
  );
});

/** The labels of a run's full timeline, under All times. */
function allTimes(id: number): string[] {
  const row = within(card('Runs'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id))!;
  const details = row.querySelector(
    'td[data-label="Times"] details',
  ) as HTMLElement;
  return Array.from(
    details.querySelectorAll('.admin-runtime'),
    (line) => line.firstChild?.textContent?.trim() ?? '',
  );
}

/** The result cell of a run's row: the result, then the archive with
 * the purge's evidence. */
function resultCell(id: number): HTMLElement {
  return runRowElement(id).querySelector(
    'td[data-label="Result"]',
  ) as HTMLElement;
}

test('HA-14: the runs show the purge — its states and results, the purged and skipped counts, the purge start, the backup and the receipt', async () => {
  runs = [
    PURGING(9),
    PURGED(8),
    {
      ...PURGED(7),
      state: 'COMPLETED_WITH_SKIPS',
      purged: { components: 118, flows: 305, movements: 4190 },
      // The verification's and the purge's skips together.
      skipped: { components: 2, flows: 5 },
    },
    // Every component changed after the export: the verification finished
    // the run, with a receipt and without a copy or a backup.
    {
      ...WAITING(6),
      state: 'COMPLETED_WITH_SKIPS',
      skipped: { components: 120, flows: 310 },
      receipt_name: 'run-6-20261117T092000Z.purged.json',
      receipt_sha256: RECEIPT_SHA,
      finished_at: '2026-11-17T09:23:00Z',
      result: 'NOTHING_PURGED',
    },
    {
      ...PURGING(5),
      state: 'FAILED',
      receipt_name: 'run-5-20261117T092000Z.purged.json',
      receipt_sha256: RECEIPT_SHA,
      failure_reason:
        'A purge batch deleted 9 Movements where its records count 10.',
      finished_at: '2026-11-17T09:30:00Z',
    },
    {
      ...WAITING(4),
      state: 'COMPLETED',
      copied_at: '2026-11-17T09:23:00Z',
      finished_at: '2026-11-17T09:23:00Z',
      result: 'EXPORT_ONLY',
    },
  ];
  renderSection();
  await loaded();

  // Purging: open, its progress so far, never cancellable; no result yet,
  // its archive under the Result heading.
  expect(runRow(9)).toMatchObject({ State: 'Purging', Actions: '' });
  expect(runLines(9, 'Result')).toEqual(['run-9-20261117T092000Z', '771.5 KB']);
  expect(runLines(9, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,210 Movements',
    '120 components · 310 flows',
    'Purged so far: 1,400 Movements · 40 components · 100 flows',
  ]);
  expect(runRow(9).Times).toMatch(/^Requested .+Purge started .+All times/);
  expect(allTimes(9)).toEqual([
    'Requested',
    'Started',
    'Exported',
    'Verified',
    'Copied',
    'Backup started',
    'Purge started',
  ]);
  const purging = resultCell(9);
  // A non-breaking hyphen keeps `Pre-purge` whole in a narrow column.
  expect(within(purging).getByText(PRE_PURGE).tagName).toBe('SUMMARY');
  expect(purging.textContent).toContain(
    '20261117T092400Z-pre-purge-run-9-20261117T092000Z',
  );
  expect(within(purging).queryByText('Purge receipt')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Cancel run 9…' })).toBeNull();

  // Completed: the result in plain words, the purged counts, the backup
  // and the receipt with its SHA-256.
  expect(runRow(8).State).toBe('Completed');
  expect(runLines(8, 'Result')).toEqual([
    'Archived and purged.',
    'run-8-20261117T092000Z',
    '771.5 KB',
  ]);
  expect(runLines(8, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,210 Movements',
    '120 components · 310 flows',
    'Purged: 4,210 Movements · 120 components · 310 flows',
  ]);
  expect(runRow(8).Times).toMatch(/^Requested .+Finished .+All times/);
  expect(allTimes(8).slice(-3)).toEqual([
    'Backup started',
    'Purge started',
    'Finished',
  ]);
  const purged = resultCell(8);
  expect(within(purged).getByText('Purge receipt').tagName).toBe('SUMMARY');
  expect(purged.textContent).toContain('run-8-20261117T092000Z.purged.json');
  expect(purged.textContent).toContain(`SHA-256 ${RECEIPT_SHA}`);
  expect(purged.textContent).toContain(
    '20261117T092400Z-pre-purge-run-8-20261117T092000Z',
  );

  expect(runRow(7).State).toBe('Completed with skips');
  expect(runLines(7, 'Result')[0]).toBe('Archived and purged.');
  expect(runLines(7, 'Cutoff and counts')).toEqual([
    'Nov 17, 2024',
    '4,210 Movements',
    '120 components · 310 flows',
    'Purged: 4,190 Movements · 118 components · 305 flows',
    'Skipped (changed during the run): 2 components · 5 flows',
  ]);

  expect(runRow(6).State).toBe('Completed with skips');
  expect(runLines(6, 'Result')[0]).toBe(
    'Nothing purged (every component changed).',
  );
  expect(runRow(6)['Cutoff and counts']).not.toContain('Purged');
  expect(within(resultCell(6)).getByText('Purge receipt')).toBeTruthy();
  expect(within(resultCell(6)).queryByText(PRE_PURGE)).toBeNull();

  // A run that failed while purging keeps the whole components it purged.
  expect(runRow(5).State).toBe('Failed');
  expect(runLines(5, 'Result')[0]).toBe(
    'A purge batch deleted 9 Movements where its records count 10.',
  );
  expect(runLines(5, 'Cutoff and counts')).toContain(
    'Purged: 1,400 Movements · 40 components · 100 flows',
  );

  // A run that step 1 finished keeps its result.
  expect(runLines(4, 'Result')[0]).toBe('Exported to files; nothing deleted.');
  expect(runRow(4)['Cutoff and counts']).not.toContain('Purged');
  expect(writes).toEqual([]);
});

test('HA-15: the purge batch limits are edited in the dialog, sent only when changed, and a refusal is shown as given', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(
    within(dialog).getByLabelText('Movements per purge batch'),
  ).toHaveValue(50000);
  expect(
    within(dialog).getByLabelText('Part Numbers per purge batch'),
  ).toHaveValue(20);
  fireEvent.change(
    within(dialog).getByLabelText('Part Numbers per purge batch'),
    { target: { value: '5' } },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_purge_batch_part_numbers: 5 },
    },
  ]);
  await waitFor(() =>
    expect(
      rows(card('Retention and schedule'))['Part Numbers per purge batch'],
    ).toBe('5'),
  );

  // The server checks the range; its message stays in the dialog.
  writes = [];
  const REFUSAL =
    'The Movements per purge batch must be a whole number from 100 to 1000000.';
  failures['PUT policy'] = { status: 422, detail: REFUSAL };
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const again = screen.getByRole('dialog', { name: 'Retention and schedule' });
  const movements = within(again).getByLabelText('Movements per purge batch');
  fireEvent.change(movements, { target: { value: '50' } });
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(REFUSAL);
  expect(movements).toHaveValue(50);
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_purge_batch_movements: 50 },
    },
  ]);

  // Only a value that is not a whole number is refused in the dialog.
  fireEvent.change(movements, { target: { value: '2.5' } });
  expect(
    within(again)
      .getAllByRole('alert')
      .map((alert) => alert.textContent),
  ).toContain('Enter a whole number.');
  expect(
    within(again).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

/* ============ Permanent deletion requests (archival step 3, D17) ============ */

const DELETION_KEY = 'DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY';
const DELETION_BACKUP =
  '20261118T090501Z-pre-purge-deletion-4-20261118T090500Z';
const CHANGED =
  'The data changed after the request; nothing was deleted. Request it again.';

/** One cell of the requests table's row of request `id`. */
function deletionCell(id: number, label: string): HTMLElement {
  const row = within(card('Permanent deletion requests'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id));
  if (!row) throw new Error(`No row for deletion request ${id}`);
  return row.querySelector(`td[data-label="${label}"]`) as HTMLElement;
}

/** What the row of request `id` shows at a glance, by column: the state
 * pill, the lines a cell shows outside its folded disclosures, and the
 * result's text. */
function deletionRow(id: number): Record<string, string> {
  const shown = (label: string) =>
    Array.from(
      deletionCell(id, label).querySelectorAll(
        ':scope > .admin-runlines > :not(details)',
      ),
      (line) => line.textContent,
    ).join(' / ');
  return {
    State:
      deletionCell(id, 'State').querySelector('.pillnav')?.textContent ?? '',
    Deletes: shown('Deletes'),
    'Requested by': shown('Requested by'),
    Times: shown('Times'),
    Result:
      deletionCell(id, 'Result').querySelector('.admin-runresult')
        ?.textContent ?? '',
  };
}

/** The pre-purge backup folded in the result of request `id`, if any. */
function deletionBackup(id: number): string | null {
  const details = deletionCell(id, 'Result').querySelector('details');
  if (details === null) return null;
  expect(details.querySelector('summary')?.textContent).toBe(PRE_PURGE);
  return details.querySelector('.admin-runarchive')?.textContent ?? '';
}

async function deletionsLoaded() {
  await loaded();
  await waitFor(() =>
    expect(
      screen.queryByRole('status', {
        name: 'Loading permanent deletion requests',
      }),
    ).toBeNull(),
  );
}

const deletionReads = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([input]) =>
      String(input).startsWith('/api/permanent-deletion'),
    ).length;

test('HA-16: a holder of the deletion key sees the requests below the runs: state, records, rows, requester, times, backup and result', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [
    deletion(5, {
      state: 'RUNNING',
      started_at: '2026-11-18T10:05:00Z',
      backup_label: 'deletion-5-20261118T100500Z',
    }),
    deletion(4, {
      state: 'COMPLETED',
      scope: { work_order_numbers: [], part_numbers: ['PN-A'] },
      work_orders: [
        {
          id: 2,
          work_order_number: null,
          internal: true,
          named: false,
          part_numbers: ['PN-A'],
          demand_lines: 1,
        },
      ],
      part_numbers: [{ part_number: 'PN-A', master: true }],
      requested_by: null,
      requested_by_operator: 'ops-anna',
      requested_at: '2026-11-18T09:00:00Z',
      started_at: '2026-11-18T09:05:00Z',
      backup_label: 'deletion-4-20261118T090500Z',
      pre_purge_backup: {
        name: DELETION_BACKUP,
        dump_started_at: '2026-11-18T09:05:01Z',
      },
      finished_at: '2026-11-18T09:06:00Z',
    }),
    deletion(3, {
      state: 'FAILED',
      started_at: '2026-11-18T08:05:00Z',
      backup_label: 'deletion-3-20261118T080500Z',
      finished_at: '2026-11-18T08:06:00Z',
      failure_reason: CHANGED,
    }),
    deletion(2, {
      state: 'CANCELLED',
      finished_at: '2026-11-18T07:01:00Z',
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();

  const requestsCard = card('Permanent deletion requests');
  expect(
    card('Runs').compareDocumentPosition(requestsCard) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(requestsCard.textContent).toContain(
    'Each request deletes Work Orders or a Part Number with all their data from the database, as if they had never been entered; the server carries it out after a pre-purge backup.',
  );
  expect(requestsCard.textContent).toContain(
    'Delete permanently… in Work Order Details and in the Part Number dialog of Management → Part Numbers requests a deletion.',
  );
  expect(
    within(requestsCard)
      .getAllByRole('columnheader')
      .map((th) => th.textContent),
  ).toEqual(['Request', 'State', 'Deletes', 'Requested by', 'Times', 'Result']);

  // The open request: Deleting…, nothing to cancel; its rows per table
  // are folded under its records.
  expect(deletionRow(5)).toMatchObject({
    State: 'Deleting…',
    Deletes: 'Work Order 007201 / 28 rows',
    'Requested by': 'Sam Super / Test data entered by mistake',
    Result: '—',
  });
  expect(deletionBackup(5)).toBeNull();
  expect(deletionCell(5, 'State').querySelector('button')).toBeNull();
  // The Work Order Number stays whole: it never wraps inside.
  expect(
    deletionCell(5, 'Deletes').querySelector('.admin-runname')?.textContent,
  ).toBe('007201');
  expect(deletionCell(5, 'Deletes').querySelector('summary')?.textContent).toBe(
    'Rows per table',
  );
  expect(
    Array.from(
      deletionCell(5, 'Deletes').querySelectorAll('details .admin-runsub'),
      (line) => line.textContent,
    ),
  ).toEqual([
    'Work Orders: 1',
    'Demand lines: 1',
    'Allocations: 0',
    'Quantity Flows: 2',
    'Lineage links: 0',
    'Movements: 12',
    'Assigned Routes: 2',
    'Assigned Route steps: 6',
    'Part Number details: 0',
    'Audit rows: 4',
  ]);
  expect(deletionRow(5).Times).toContain(
    `Requested ${formatInstant('2026-11-18T10:00:00Z')}`,
  );
  expect(deletionRow(5).Times).toContain(
    `Started ${formatInstant('2026-11-18T10:05:00Z')}`,
  );

  // An operator's Part Number request that deleted an internal Work
  // Order too, after its pre-purge backup (folded under the result).
  expect(deletionRow(4)).toMatchObject({
    State: 'Deleted',
    Deletes: 'Part Number PN-A / Also 1 internal Work Order / 28 rows',
    Result: 'Deleted from the database.',
  });
  expect(deletionBackup(4)).toBe(DELETION_BACKUP);
  expect(deletionRow(4)['Requested by']).toContain('ops-anna (operator)');
  expect(
    Array.from(
      deletionCell(4, 'Times').querySelectorAll('details .admin-runtime'),
      (line) => line.firstChild?.textContent?.trim(),
    ),
  ).toEqual(['Requested', 'Started', 'Backup started', 'Finished']);

  // A failed request shows the reason as the server gives it.
  expect(deletionRow(3)).toMatchObject({
    State: 'Failed — nothing was deleted',
    Result: CHANGED,
  });
  expect(deletionRow(2)).toMatchObject({
    State: 'Cancelled',
    Result: 'Cancelled by Ada Admin.',
  });
  expect(
    within(requestsCard).queryByRole('button', { name: /^Cancel deletion/ }),
  ).toBeNull();
  expect(writes).toEqual([]);
});

test('HA-17: Cancel asks first, is offered only for a request waiting for the server, and posts once', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [
    deletion(6),
    deletion(2, {
      state: 'CANCELLED',
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();
  expect(deletionRow(6).State).toBe('Waiting for the server');
  // Cancel… sits under the state it applies to.
  expect(deletionCell(6, 'State').querySelector('button')).toHaveAccessibleName(
    'Cancel deletion request 6…',
  );
  expect(
    screen.queryByRole('button', { name: 'Cancel deletion request 2…' }),
  ).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 6…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Cancel deletion request 6?',
  });
  expect(confirm.textContent).toContain(
    'Nothing is deleted, and the records stay in the database. To delete them later, request the deletion again.',
  );
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep request (Esc)' }),
  );
  expect(writes).toEqual([]);

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 6…' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Cancel deletion request 6?' }),
    ).getByRole('button', { name: 'Cancel request' }),
  );
  expect(
    await within(card('Permanent deletion requests')).findByText(
      '✓ Deletion request 6 is cancelled. Nothing was deleted.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/permanent-deletion/requests/6/cancel',
      body: undefined,
    },
  ]);
  await waitFor(() => expect(deletionRow(6).State).toBe('Cancelled'));
  expect(deletionRow(6).Result).toBe('Cancelled by Ada Admin.');
  expect(
    screen.queryByRole('button', { name: 'Cancel deletion request 6…' }),
  ).toBeNull();
});

test('HA-18: a refused cancel shows the server text; a lost answer reloads the requests; offline blocks Cancel', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [deletion(7)];
  renderSection();
  await deletionsLoaded();
  const RUNNING =
    'Deletion request 7 is being carried out on the server now and cannot be cancelled. Wait until it finishes.';
  failures['POST deletion cancel'] = { status: 409, detail: RUNNING };
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 7…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Cancel deletion request 7?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel request' }),
  );
  expect(await within(confirm).findByRole('alert')).toHaveTextContent(RUNNING);

  const reads = deletionReads();
  failures['POST deletion cancel'] = { status: 502, detail: 'Bad gateway.' };
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel request' }),
  );
  expect(
    await within(card('Permanent deletion requests')).findByRole('alert'),
  ).toHaveTextContent(
    'The server did not answer, so deletion request 7 may or may not have been cancelled. Check its state in the table; cancelling it again is safe.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Cancel deletion request 7?' }),
  ).toBeNull();
  // The lost answer reloads the requests.
  await waitFor(() => expect(deletionReads()).toBeGreaterThan(reads + 1));
  cleanup();

  renderSection('unavailable');
  await deletionsLoaded();
  expect(
    screen.getByRole('button', { name: 'Cancel deletion request 7…' }),
  ).toBeDisabled();
});

test('HA-19: only holders of the deletion key see the card and read the requests; it has its empty and error states', async () => {
  // The archival key alone: no card, the requests never read.
  deletions = [deletion(1)];
  renderSection();
  await loaded();
  expect(
    screen.queryByRole('heading', { name: 'Permanent deletion requests' }),
  ).toBeNull();
  expect(deletionReads()).toBe(0);
  cleanup();

  // No key of the section at all: view-only, no card either.
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  renderSection();
  await loaded();
  expect(
    screen.queryByRole('heading', { name: 'Permanent deletion requests' }),
  ).toBeNull();
  expect(deletionReads()).toBe(0);
  cleanup();

  // The deletion key alone: the card with Cancel, the rest view-only.
  session = sessionWith([DELETION_KEY]);
  renderSection();
  await deletionsLoaded();
  expect(
    screen.getByText(
      'View only — changing this needs the Run history archival and purge permission.',
    ),
  ).toBeInTheDocument();
  expect(deletionRow(1).State).toBe('Waiting for the server');
  expect(
    screen.getByRole('button', { name: 'Cancel deletion request 1…' }),
  ).toBeEnabled();
  cleanup();

  // Empty, then a failed load with Retry.
  deletions = [];
  renderSection();
  await deletionsLoaded();
  expect(
    within(card('Permanent deletion requests')).getByText(
      'No permanent deletion has been requested yet.',
    ),
  ).toBeInTheDocument();
  cleanup();

  failures['GET deletions'] = { status: 500, detail: 'Database unavailable.' };
  renderSection();
  expect(
    await screen.findByText(
      'The permanent deletion requests could not be loaded.',
    ),
  ).toBeInTheDocument();
  delete failures['GET deletions'];
  deletions = [deletion(8)];
  fireEvent.click(
    within(card('Permanent deletion requests')).getByRole('button', {
      name: 'Retry',
    }),
  );
  await waitFor(() =>
    expect(deletionRow(8).State).toBe('Waiting for the server'),
  );
});

test('HA-20: a Work Order Number or Part Number stays whole unless it is longer than 16 characters', async () => {
  session = sessionWith([DELETION_KEY]);
  deletions = [
    deletion(11, {
      state: 'CANCELLED',
      scope: {
        work_order_numbers: ['007099-SUPPLEMENTAL-AMENDMENT'],
        part_numbers: [],
      },
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
    deletion(10, {
      state: 'CANCELLED',
      scope: { work_order_numbers: [], part_numbers: ['0118-40-0022-07X'] },
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();
  const name = (id: number) =>
    deletionCell(id, 'Deletes').querySelector('.admin-runname') as HTMLElement;
  // 16 characters: kept on one line.
  expect(name(10)).toHaveTextContent('0118-40-0022-07X');
  expect(name(10).className).toBe('admin-runname');
  // 29 characters: may wrap anywhere rather than widen the table.
  expect(name(11)).toHaveTextContent('007099-SUPPLEMENTAL-AMENDMENT');
  expect(name(11).className).toBe('admin-runname long');
  expect(deletionRow(11).Deletes).toBe(
    'Work Order 007099-SUPPLEMENTAL-AMENDMENT / 28 rows',
  );
});

/* ============ Automatic runs (archival step 4, D9–D11) ============ */

const RENEWAL_WARNING =
  'Without the approval step, automatic runs archive and purge Movement history without anyone approving them first.';
/** What each change that stops a run started without approval says. */
const STEP_ON_STOPS =
  'Switching the approval step on withdraws the standing authorization: a run started without approval stops before it purges any history, and each later automatic run waits for approval.';
const AUTOMATIC_OFF_STOPS =
  'Switching automatic runs off also stops a run started without approval before it purges any history.';
const PERIOD_STOPS =
  'A run started without approval stops before it purges any history when the retention period no longer allows its cutoff.';
const CONFIRMATION_FIELD = 'Type RUN WITHOUT APPROVAL to confirm';
const AUTHORIZATION_REASON_FIELD =
  /^Reason for letting automatic runs start without approval/;

function editAutomaticRuns(): HTMLElement {
  fireEvent.click(screen.getByRole('button', { name: 'Edit automatic runs…' }));
  return screen.getByRole('dialog', { name: 'Automatic runs' });
}

async function automaticRunsSaved() {
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Automatic runs' })).toBeNull(),
  );
}

/** The cards of each column of the settings, by title. */
function cardColumns(): (string | null)[][] {
  return Array.from(document.querySelectorAll('.admin-cardcolumn'), (column) =>
    Array.from(column.querySelectorAll('h2'), (h2) => h2.textContent),
  );
}

test('HA-21: the Automatic runs card shows its settings; Edit sends only the changed ones, each measure with its own limit, and shows the server refusal as given', async () => {
  renderSection();
  await loaded();
  expect(rows(card('Automatic runs'))).toEqual({
    'Automatic runs': 'Off',
    Measure: 'Live Movement rows',
    Limit: '5,000,000 rows',
    'Days between automatic runs': '7 days',
    'Approval step': 'On',
  });
  // No standing authorization while the approval step is on.
  expect(card('Automatic runs').textContent).not.toContain(
    'Standing authorization',
  );
  // The settings stacked in the first column, the Preview beside them.
  expect(cardColumns()).toEqual([
    ['Retention and schedule', 'Automatic runs'],
    ['Preview'],
  ]);

  const dialog = editAutomaticRuns();
  expect(
    within(dialog).getByRole('switch', { name: 'Automatic runs' }),
  ).toHaveAttribute('aria-checked', 'false');
  expect(within(dialog).getByLabelText('Measure')).toHaveValue(
    'LIVE_MOVEMENT_ROWS',
  );
  expect(within(dialog).getByLabelText('Live Movement rows above')).toHaveValue(
    5000000,
  );
  // Each measure keeps its own limit: the dialog shows the chosen one's.
  fireEvent.change(within(dialog).getByLabelText('Measure'), {
    target: { value: 'DATABASE_SIZE' },
  });
  expect(
    within(dialog).queryByLabelText('Live Movement rows above'),
  ).toBeNull();
  const gib = within(dialog).getByLabelText('Database size above (GiB)');
  expect(gib).toHaveValue(20);
  fireEvent.change(gib, { target: { value: '30' } });
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Automatic runs' }),
  );
  fireEvent.change(
    within(dialog).getByLabelText('Days between automatic runs'),
    {
      target: { value: '14' },
    },
  );
  // The approval step is untouched: no reason and no confirmation.
  expect(within(dialog).queryByLabelText(CONFIRMATION_FIELD)).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await automaticRunsSaved();
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: {
        archive_automatic_runs: true,
        archive_measure: 'DATABASE_SIZE',
        archive_limit_database_gib: 30,
        archive_interval_days: 14,
      },
    },
  ]);
  await waitFor(() =>
    expect(rows(card('Automatic runs'))).toEqual({
      'Automatic runs': 'On',
      Measure: 'Database size',
      Limit: '30 GiB',
      'Days between automatic runs': '14 days',
      'Approval step': 'On',
    }),
  );

  // The server checks every range; its message stays in the dialog.
  writes = [];
  const REFUSAL =
    'The limit of the age of retained history must be a whole number of months from 0 to 1200.';
  failures['PUT policy'] = { status: 422, detail: REFUSAL };
  const again = editAutomaticRuns();
  fireEvent.change(within(again).getByLabelText('Measure'), {
    target: { value: 'RETAINED_HISTORY_AGE' },
  });
  const months = within(again).getByLabelText(
    'Retained history older than the cutoff by more than (months)',
  );
  expect(months).toHaveValue(1);
  fireEvent.change(months, { target: { value: '1201' } });
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(REFUSAL);
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: {
        archive_measure: 'RETAINED_HISTORY_AGE',
        archive_limit_history_months: 1201,
      },
    },
  ]);
  // Only a value that is not a whole number is refused in the dialog.
  fireEvent.change(months, { target: { value: '1.5' } });
  expect(
    within(again)
      .getAllByRole('alert')
      .map((alert) => alert.textContent),
  ).toContain('Enter a whole number.');
  expect(
    within(again).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

test('HA-22: switching the approval step off says plainly what it means and asks for a reason and the typed confirmation; the server refusals are shown as given', async () => {
  renderSection();
  await loaded();
  const dialog = editAutomaticRuns();
  expect(within(dialog).queryByText(RENEWAL_WARNING)).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Approval step' }),
  );
  expect(within(dialog).getByText(RENEWAL_WARNING)).toBeInTheDocument();
  const reason = within(dialog).getByLabelText(AUTHORIZATION_REASON_FIELD);
  const confirmation = within(dialog).getByLabelText(CONFIRMATION_FIELD);

  // Without the confirmation the server refuses, in its own words.
  fireEvent.change(reason, {
    target: { value: 'Weekly archival without approval' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    CONFIRMATION_REFUSAL,
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: {
        archive_approval_required: false,
        reason: 'Weekly archival without approval',
        confirmation: '',
      },
    },
  ]);
  expect(policy.archive_approval_required).toBe(true);

  // The confirmation as typed (the server ignores letter case and
  // spacing), without a reason: the server's reason text.
  fireEvent.change(confirmation, {
    target: { value: ' run  without approval ' },
  });
  fireEvent.change(reason, { target: { value: '' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() =>
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      AUTHORIZATION_REASON_REFUSAL,
    ),
  );

  // Both: the step is off, and the card shows the standing authorization.
  writes = [];
  fireEvent.change(reason, {
    target: { value: 'Weekly archival without approval' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await automaticRunsSaved();
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: {
        archive_approval_required: false,
        reason: 'Weekly archival without approval',
        confirmation: ' run  without approval ',
      },
    },
  ]);
  await waitFor(() =>
    expect(rows(card('Automatic runs'))).toMatchObject({
      'Approval step': 'Off',
      'Standing authorization': 'Valid',
    }),
  );
  expect(card('Automatic runs').textContent).toContain(
    `Given by Ada Admin on ${formatInstant(T0)}. Reason: Weekly archival without approval`,
  );
});

test("HA-23: the standing authorization shows who gave it, when and why, and the server's problem; the same form renews it, and switching the step on withdraws it", async () => {
  policy = {
    ...policy,
    archive_automatic_runs: true,
    archive_approval_required: false,
    standing_authorization: {
      audit_event_id: 12,
      authorized_by: { user_id: 4, display_name: 'Ann Admin' },
      authorized_at: '2026-11-10T12:00:00Z',
      reason: 'Weekly archival',
      valid: false,
      problem: KEY_LOST,
    },
  };
  renderSection();
  await loaded();
  expect(rows(card('Automatic runs'))).toMatchObject({
    'Approval step': 'Off',
    'Standing authorization': 'Not valid',
  });
  expect(card('Automatic runs').textContent).toContain(
    `Given by Ann Admin on ${formatInstant('2026-11-10T12:00:00Z')}. Reason: Weekly archival`,
  );
  // The problem exactly as the server words it.
  expect(
    within(card('Automatic runs')).getByText(KEY_LOST),
  ).toBeInTheDocument();

  // While the step stays off, the same form renews the authorization.
  const dialog = editAutomaticRuns();
  expect(within(dialog).getByText(KEY_LOST)).toBeInTheDocument();
  expect(within(dialog).queryByLabelText(CONFIRMATION_FIELD)).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('checkbox', {
      name: 'Renew the standing authorization under my name',
    }),
  );
  expect(within(dialog).getByText(RENEWAL_WARNING)).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText(AUTHORIZATION_REASON_FIELD), {
    target: { value: 'Ann moved to another team' },
  });
  fireEvent.change(within(dialog).getByLabelText(CONFIRMATION_FIELD), {
    target: { value: 'RUN WITHOUT APPROVAL' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await automaticRunsSaved();
  // Nothing else changed: the reason and the confirmation alone.
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: {
        reason: 'Ann moved to another team',
        confirmation: 'RUN WITHOUT APPROVAL',
      },
    },
  ]);
  await waitFor(() =>
    expect(rows(card('Automatic runs'))['Standing authorization']).toBe(
      'Valid',
    ),
  );
  expect(card('Automatic runs').textContent).toContain(
    `Given by Ada Admin on ${formatInstant(T0)}. Reason: Ann moved to another team`,
  );
  expect(within(card('Automatic runs')).queryByText(KEY_LOST)).toBeNull();

  // Switching the step on withdraws it, which stops a run started without
  // approval; no confirmation travels.
  writes = [];
  const again = editAutomaticRuns();
  fireEvent.click(within(again).getByRole('switch', { name: 'Approval step' }));
  expect(within(again).getByText(STEP_ON_STOPS)).toBeInTheDocument();
  expect(within(again).queryByRole('checkbox', { name: /^Renew/ })).toBeNull();
  expect(within(again).queryByLabelText(CONFIRMATION_FIELD)).toBeNull();
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  await automaticRunsSaved();
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_approval_required: true },
    },
  ]);
  await waitFor(() =>
    expect(rows(card('Automatic runs'))['Approval step']).toBe('On'),
  );
  expect(card('Automatic runs').textContent).not.toContain(
    'Standing authorization',
  );
});

test('HA-24: automatic runs read Automatic with their approval or standing authorization; Proposed and Dismissed have their pills, and a failed authority its reason', async () => {
  const AUTHORIZATION = {
    audit_event_id: 12,
    authorized_by: { user_id: 4, display_name: 'Ann Admin' },
    authorized_at: '2026-11-10T12:00:00Z',
  };
  const AUTHORITY_FAILED =
    'The User who switched off the approval step of automatic runs, Ann Admin (user 4), no longer holds Run history archival and purge, so the history of run 11 was not purged. Nothing was deleted; automatic runs wait until a holder of the key saves the approval setting again.';
  runs = [
    proposed(14),
    proposed(13, {
      state: 'APPROVED',
      approval: {
        approved_by: { user_id: 4, display_name: 'Ann Admin' },
        approved_at: T1,
        reason: 'Checked the preview',
      },
    }),
    proposed(12, {
      state: 'RUNNING',
      started_at: '2026-11-18T09:20:00Z',
      authorization: AUTHORIZATION,
    }),
    proposed(11, {
      ...WAITING(11),
      trigger: 'AUTOMATIC',
      requested_by: null,
      reason: AUTO_REASON,
      state: 'FAILED',
      authorization: AUTHORIZATION,
      authorization_failed: true,
      failure_reason: AUTHORITY_FAILED,
      finished_at: '2026-11-18T09:30:00Z',
    }),
    proposed(10, {
      state: 'DISMISSED',
      dismissed_by: { user_id: 4, display_name: 'Ann Admin' },
      finished_at: T1,
    }),
  ];
  renderSection();
  await loaded();

  // Proposed: open, its own pill, Approve… and Dismiss… — never Cancel….
  expect(runRow(14)).toMatchObject({
    State: 'Proposed — waiting for approval',
    Actions: 'Approve… / Dismiss…',
    Result: '—',
  });
  expect(runRowElement(14).querySelector('.pillnav')?.className).toBe(
    'pillnav term',
  );
  expect(runLines(14, 'Requested by')).toEqual(['Automatic', AUTO_REASON]);
  expect(screen.getByRole('button', { name: 'Approve run 14…' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Dismiss run 14…' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Cancel run 14…' })).toBeNull();

  // Approved: the approver with the time and their own reason; the
  // approval is its latest time.
  expect(runRow(13)).toMatchObject({
    State: 'Approved — waiting for the job',
    Actions: 'Cancel…',
  });
  expect(runLines(13, 'Requested by')).toEqual([
    'Automatic',
    AUTO_REASON,
    `Approved by Ann Admin, ${formatInstant(T1)}`,
    'Checked the preview',
  ]);
  expect(runLines(13, 'Times')).toEqual([
    `Requested ${formatInstant('2026-11-18T08:20:00Z')}`,
    `Approved ${formatInstant(T1)}`,
  ]);

  // Started under the standing authorization, without approval.
  expect(runLines(12, 'Requested by')).toEqual([
    'Automatic',
    AUTO_REASON,
    'Started without approval (authorization of Ann Admin, Nov 10, 2026)',
  ]);
  expect(runRow(12)).toMatchObject({ State: 'Running', Actions: '' });

  // Failed because the authority's User lost the key: the reason as given.
  expect(runRow(11).State).toBe('Failed');
  expect(runLines(11, 'Result')[0]).toBe(AUTHORITY_FAILED);

  // Dismissed: final, by whom.
  expect(runRow(10)).toMatchObject({
    State: 'Dismissed',
    Actions: '',
    Result: 'Dismissed by Ann Admin.',
  });
  expect(runRowElement(10).querySelector('.pillnav')?.className).toBe(
    'pillnav off',
  );
  expect(writes).toEqual([]);
});

test("HA-25: Approve… shows what the run does and asks for a reason of the approver's own; the server texts stay in the dialog; a lost answer reloads the runs", async () => {
  runs = [proposed(14)];
  renderSection();
  await loaded();
  fireEvent.click(screen.getByRole('button', { name: 'Approve run 14…' }));
  const dialog = screen.getByRole('dialog', { name: 'Approve run 14' });
  expect(dialog.textContent).toContain(
    'The run exports the Movement history before Nov 17, 2024 to archive files on the server and, after the off-host copy and a pre-purge backup, deletes it from the database.',
  );
  expect(dialog.textContent).toContain(AUTO_REASON);
  expect(dialog.textContent).toContain(
    '120 components · 310 flows · 4,210 Movements',
  );

  // A blank reason: the server's 422, as given.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Approve run' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'Enter the reason for approving this run: 1 to 500 characters of plain text.',
  );
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/14/approve',
      body: { reason: '' },
    },
  ]);

  // Another User approved it first: the server's 409, the reason kept.
  const reason = within(dialog).getByLabelText(/^Reason for approving/);
  fireEvent.change(reason, { target: { value: 'Checked the preview' } });
  runs[0].state = 'APPROVED';
  fireEvent.click(within(dialog).getByRole('button', { name: 'Approve run' }));
  await waitFor(() =>
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Run 14 is APPROVED; only a run waiting for approval (PROPOSED) can be approved.',
    ),
  );
  expect(reason).toHaveValue('Checked the preview');

  // A lost answer: the runs reload and the card says to check them.
  runs[0].state = 'PROPOSED';
  failures['POST approve'] = { status: 504, detail: 'Gateway timeout.' };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Approve run' }));
  expect(await within(card('Runs')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so run 14 may or may not have been approved. Check its state in the table; approving it again is safe.',
  );
  expect(screen.queryByRole('dialog', { name: 'Approve run 14' })).toBeNull();

  // Approved: the notice, and the run reloads with its approval.
  delete failures['POST approve'];
  writes = [];
  fireEvent.click(screen.getByRole('button', { name: 'Approve run 14…' }));
  const again = screen.getByRole('dialog', { name: 'Approve run 14' });
  fireEvent.change(within(again).getByLabelText(/^Reason for approving/), {
    target: { value: 'Checked the preview' },
  });
  fireEvent.click(within(again).getByRole('button', { name: 'Approve run' }));
  expect(
    await within(card('Runs')).findByText(
      '✓ Run 14 is approved. The archival job on the server starts it at its next Start minute.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/14/approve',
      body: { reason: 'Checked the preview' },
    },
  ]);
  await waitFor(() =>
    expect(runRow(14).State).toBe('Approved — waiting for the job'),
  );
  expect(runLines(14, 'Requested by')).toEqual([
    'Automatic',
    AUTO_REASON,
    `Approved by Ada Admin, ${formatInstant(T1)}`,
    'Checked the preview',
  ]);
  expect(screen.queryByRole('button', { name: 'Approve run 14…' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Cancel run 14…' })).toBeEnabled();
});

test("HA-26: Dismiss… asks first and sends no reason; the server's 409 texts are shown as given; offline blocks Approve… and Dismiss…", async () => {
  runs = [proposed(15)];
  renderSection();
  await loaded();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss run 15…' }));
  const confirm = screen.getByRole('dialog', { name: 'Dismiss run 15?' });
  expect(confirm.textContent).toContain(
    'The run will not start, and the Movement history stays in the database.',
  );
  expect(within(confirm).queryByRole('textbox')).toBeNull();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep run (Esc)' }),
  );
  expect(writes).toEqual([]);

  // Approved meanwhile, then started: the server's texts, as given.
  runs[0].state = 'APPROVED';
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss run 15…' }));
  const again = screen.getByRole('dialog', { name: 'Dismiss run 15?' });
  fireEvent.click(within(again).getByRole('button', { name: 'Dismiss run' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(
    'Run 15 is APPROVED; only a run waiting for approval (PROPOSED) can be dismissed. Cancel it instead.',
  );
  runs[0].state = 'RUNNING';
  fireEvent.click(within(again).getByRole('button', { name: 'Dismiss run' }));
  await waitFor(() =>
    expect(within(again).getByRole('alert')).toHaveTextContent(
      'Run 15 is running on the server now and cannot be dismissed. Wait until it stops.',
    ),
  );

  runs[0].state = 'PROPOSED';
  writes = [];
  fireEvent.click(within(again).getByRole('button', { name: 'Dismiss run' }));
  expect(
    await within(card('Runs')).findByText('✓ Run 15 is dismissed.'),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/15/dismiss',
      body: undefined,
    },
  ]);
  await waitFor(() => expect(runRow(15).State).toBe('Dismissed'));
  expect(runRow(15)).toMatchObject({
    Result: 'Dismissed by Ada Admin.',
    Actions: '',
  });
  cleanup();

  // Offline, every write is blocked.
  runs = [proposed(16)];
  renderSection('unavailable');
  await loaded();
  expect(
    screen.getByRole('button', { name: 'Approve run 16…' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Dismiss run 16…' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Edit automatic runs…' }),
  ).toBeDisabled();
});

test('HA-27: while the approval step is off, switching automatic runs off and lengthening or clearing the retention period say that they stop a run started without approval', async () => {
  policy = {
    ...policy,
    archive_automatic_runs: true,
    archive_approval_required: false,
    standing_authorization: {
      audit_event_id: 12,
      authorized_by: { user_id: 4, display_name: 'Ann Admin' },
      authorized_at: '2026-11-10T12:00:00Z',
      reason: 'Weekly archival',
      valid: true,
      problem: null,
    },
  };
  renderSection();
  await loaded();

  // Switching automatic runs off says so; switching them on again does not.
  const dialog = editAutomaticRuns();
  expect(within(dialog).queryByText(AUTOMATIC_OFF_STOPS)).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Automatic runs' }),
  );
  expect(within(dialog).getByText(AUTOMATIC_OFF_STOPS)).toBeInTheDocument();
  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'Automatic runs' }),
  );
  expect(within(dialog).queryByText(AUTOMATIC_OFF_STOPS)).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  await automaticRunsSaved();

  // A longer retention period says so; a shorter one does not.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const retention = screen.getByRole('dialog', {
    name: 'Retention and schedule',
  });
  const months = within(retention).getByLabelText('Retention period (months)');
  expect(within(retention).queryByText(PERIOD_STOPS)).toBeNull();
  fireEvent.change(months, { target: { value: '36' } });
  expect(within(retention).getByText(PERIOD_STOPS)).toBeInTheDocument();
  fireEvent.change(months, { target: { value: '18' } });
  expect(within(retention).queryByText(PERIOD_STOPS)).toBeNull();
  // No retention period says so too; keeping a period again does not.
  fireEvent.click(
    within(retention).getByRole('radio', { name: 'No retention period' }),
  );
  expect(within(retention).getByText(PERIOD_STOPS)).toBeInTheDocument();
  fireEvent.click(
    within(retention).getByRole('radio', {
      name: 'Keep a set period of history',
    }),
  );
  expect(within(retention).queryByText(PERIOD_STOPS)).toBeNull();
  expect(writes).toEqual([]);
  cleanup();

  // With the approval step on, no run started without approval is
  // pending: neither change says anything about one.
  policy = {
    ...policy,
    archive_approval_required: true,
    standing_authorization: null,
  };
  renderSection();
  await loaded();
  const onDialog = editAutomaticRuns();
  fireEvent.click(
    within(onDialog).getByRole('switch', { name: 'Automatic runs' }),
  );
  expect(within(onDialog).queryByText(AUTOMATIC_OFF_STOPS)).toBeNull();
  fireEvent.click(
    within(onDialog).getByRole('switch', { name: 'Automatic runs' }),
  );
  fireEvent.click(
    within(onDialog).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  await automaticRunsSaved();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const onRetention = screen.getByRole('dialog', {
    name: 'Retention and schedule',
  });
  fireEvent.change(
    within(onRetention).getByLabelText('Retention period (months)'),
    { target: { value: '36' } },
  );
  expect(within(onRetention).queryByText(PERIOD_STOPS)).toBeNull();
  fireEvent.click(
    within(onRetention).getByRole('radio', { name: 'No retention period' }),
  );
  expect(within(onRetention).queryByText(PERIOD_STOPS)).toBeNull();
});

// ---------------------------------------------------------------------------
// Viewing an archive (archival step 5, D16)
// ---------------------------------------------------------------------------

/** The viewing of run `id` under its state: its lines (the newest view's
 * pill and its line) and its actions (buttons and links, by accessible
 * name); empty when the run shows no viewing. */
function runViewing(id: number): { lines: string[]; actions: string[] } {
  const block = runRowElement(id).querySelector('.admin-runviewing');
  if (!block) return { lines: [], actions: [] };
  return {
    lines: Array.from(
      block.querySelectorAll(':scope > span'),
      (line) => line.textContent ?? '',
    ),
    actions: Array.from(
      block.querySelectorAll(':scope > a, :scope > button'),
      (control) =>
        control.getAttribute('aria-label') ?? control.textContent ?? '',
    ),
  };
}

const LIMIT_REFUSAL =
  'At most 1 run may be loaded for viewing at once (History archival & purge), and 1 is loaded or waiting: view 3 of run 1 (OPEN). Close a view, or wait until it is closed, before opening run 4.';
const LOAD_FAILED =
  'The archive folder run-4-20261117T092000Z of run 4 is missing. Nothing was loaded; restore the archive folder from its off-host copy, then open the run for viewing again.';

test('HA-28: the viewing settings are edited in the Retention and schedule dialog, sent only when changed, and the server texts are shown as given', async () => {
  renderSection();
  await loaded();
  expect(card('Retention and schedule').textContent).toContain(
    "How long Movement history stays in the database, when the server's archival job does its work, how much it purges at a time, and how archives are loaded for viewing.",
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  const days = within(dialog).getByLabelText(
    'A loaded archive stays open (days, 1–365)',
  );
  expect(days).toHaveValue(7);
  expect(
    within(dialog).getByLabelText('Runs loaded for viewing at once (1–10)'),
  ).toHaveValue(1);
  expect(dialog.textContent).toContain(
    'A run opened for viewing closes this many days after it was loaded, and the archival job removes its copy from the database. A change applies to the runs open now as well.',
  );
  expect(dialog.textContent).toContain(
    'Each loaded run takes database space until it is closed, and a run waiting to be loaded counts too. A lower number leaves the runs loaded now open.',
  );
  // Only a whole number is refused in the dialog.
  fireEvent.change(days, { target: { value: '2.5' } });
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Enter a whole number.',
  );
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  fireEvent.change(days, { target: { value: '30' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_view_days: 30 },
    },
  ]);
  expect(await screen.findByText('30 days')).toBeInTheDocument();

  // The server checks each range, and its text is shown as given.
  for (const [label, value, detail, sent] of [
    [
      'A loaded archive stays open (days, 1–365)',
      '400',
      'The days a loaded archive stays open must be a whole number from 1 to 365.',
      { archive_view_days: 400 },
    ],
    [
      'Runs loaded for viewing at once (1–10)',
      '11',
      'The runs that may be loaded for viewing at once must be a whole number from 1 to 10.',
      { archive_view_max_loaded: 11 },
    ],
  ] as const) {
    writes = [];
    failures['PUT policy'] = { status: 422, detail };
    fireEvent.click(
      screen.getByRole('button', { name: 'Edit retention and schedule…' }),
    );
    const again = screen.getByRole('dialog', {
      name: 'Retention and schedule',
    });
    fireEvent.change(within(again).getByLabelText(label), {
      target: { value },
    });
    fireEvent.click(
      within(again).getByRole('button', { name: 'Save changes' }),
    );
    expect(await within(again).findByRole('alert')).toHaveTextContent(detail);
    expect(writes).toEqual([
      { method: 'PUT', url: '/api/policies/data-retention', body: sent },
    ]);
    fireEvent.click(
      within(again).getByRole('button', { name: 'Cancel (Esc)' }),
    );
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Unsaved changes' })).getByRole(
        'button',
        { name: 'Discard changes' },
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: 'Retention and schedule' }),
      ).toBeNull(),
    );
  }
});

test('HA-29: Open for viewing… is offered on a viewable run, says what loading does, posts the run, and the run then waits to be loaded with Cancel…', async () => {
  runs = [
    VIEWABLE(4),
    run(3, { state: 'NOTHING_ELIGIBLE', finished_at: T0 }),
    WAITING(2),
  ];
  renderSection();
  await loaded();
  // Only the run the server marks viewable offers it.
  expect(runViewing(4)).toEqual({
    lines: [],
    actions: ['Open run 4 for viewing…'],
  });
  expect(runViewing(3)).toEqual({ lines: [], actions: [] });
  expect(runViewing(2)).toEqual({ lines: [], actions: [] });
  // The run's own pill stays first.
  expect(runRow(4).State).toBe('Completed');

  fireEvent.click(
    screen.getByRole('button', { name: 'Open run 4 for viewing…' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Open run 4 for viewing?',
  });
  expect(
    Array.from(dialog.querySelectorAll('p'), (p) => p.textContent),
  ).toEqual([
    "The archival job on the server loads a copy of this run's archive within minutes, for reading on a page of its own. The copy is read-only and separate from the live history: nothing is added to the live history, and Tracking and every figure stay as they are.",
    "Until it is closed, the copy takes space in the database: roughly 7 times the archive's size, about 5.3 MB for this run (4,210 Movements in 310 flows, an archive of 771.5 KB).",
    'It closes by itself 7 days after it opens, or earlier with Close viewing.',
  ]);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(writes).toEqual([]);

  fireEvent.click(
    screen.getByRole('button', { name: 'Open run 4 for viewing…' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Open run 4 for viewing?' }),
    ).getByRole('button', { name: 'Open for viewing' }),
  );
  expect(
    await within(card('Runs')).findByText(
      '✓ Run 4 is waiting to be loaded for viewing. The archival job on the server loads it within minutes: Reload shows when it is open, and View then opens it.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    { method: 'POST', url: '/api/history-archive/views', body: { run_id: 4 } },
  ]);
  await waitFor(() =>
    expect(runViewing(4)).toEqual({
      lines: ['Waiting to be loaded for viewing'],
      actions: ['Cancel viewing of run 4…'],
    }),
  );
});

test('HA-30: a refusal of Open for viewing — the runs loaded at once, a run already open — is shown as given; a lost answer reloads the runs', async () => {
  runs = [VIEWABLE(4)];
  renderSection();
  await loaded();
  failures['POST views'] = { status: 409, detail: LIMIT_REFUSAL };
  fireEvent.click(
    screen.getByRole('button', { name: 'Open run 4 for viewing…' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Open run 4 for viewing?',
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Open for viewing' }),
  );
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    LIMIT_REFUSAL,
  );
  expect(views).toEqual([]);

  // The answer is lost: the view was requested meanwhile.
  failures['POST views'] = 'network';
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Open for viewing' }),
  );
  expect(await within(card('Runs')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so run 4 may or may not have been opened for viewing. Check its state in the table; opening it again is safe.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Open run 4 for viewing?' }),
  ).toBeNull();
  await waitFor(() =>
    expect(runViewing(4).lines).toEqual(['Waiting to be loaded for viewing']),
  );
  cleanup();

  // A table read before someone else opened the run: the server's text.
  delete failures['POST views'];
  runs = [VIEWABLE(4)];
  views = [OPEN_VIEW(7, 4)];
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Open run 4 for viewing…' }),
  );
  const stale = screen.getByRole('dialog', { name: 'Open run 4 for viewing?' });
  fireEvent.click(
    within(stale).getByRole('button', { name: 'Open for viewing' }),
  );
  expect(await within(stale).findByRole('alert')).toHaveTextContent(
    'Run 4 is already open for viewing, or waiting to be loaded (view 7, OPEN).',
  );
});

test("HA-31: each run shows its newest view's state and details, View on an open view, and Open for viewing… again once the view is no longer active", async () => {
  runs = [
    VIEWABLE(6, { view: { id: 9, state: 'OPEN' } }),
    VIEWABLE(5, { view: { id: 8, state: 'LOADING' } }),
    VIEWABLE(4, { view: { id: 7, state: 'FAILED' } }),
    VIEWABLE(3, { view: { id: 6, state: 'CLOSED' } }),
    VIEWABLE(2, { view: { id: 5, state: 'CANCELLED' } }),
    VIEWABLE(1, { view: { id: 4, state: 'CLOSING' } }),
  ];
  views = [
    OPEN_VIEW(9, 6),
    view(8, 5, { state: 'LOADING', started_at: '2026-11-18T10:05:00Z' }),
    view(7, 4, {
      state: 'FAILED',
      failure_reason: LOAD_FAILED,
      finished_at: '2026-11-18T10:05:01Z',
    }),
    {
      ...OPEN_VIEW(6, 3),
      state: 'CLOSED',
      closing_started_at: OPEN_UNTIL,
      finished_at: OPEN_UNTIL,
    },
    view(5, 2, {
      state: 'CANCELLED',
      cancelled_by: { user_id: 5, display_name: 'Bo' },
      finished_at: '2026-11-18T10:01:00Z',
    }),
    {
      ...OPEN_VIEW(4, 1),
      state: 'CLOSING',
      closing_started_at: T1,
      closed_by: { user_id: 90, display_name: 'Ada Admin' },
    },
  ];
  renderSection();
  await loaded();
  await waitFor(() =>
    expect(runViewing(6)).toEqual({
      lines: ['Open for viewing', `Until ${formatInstant(OPEN_UNTIL)}`],
      actions: ['View the archive of run 6', 'Close viewing of run 6…'],
    }),
  );
  expect(runViewing(5)).toEqual({
    lines: ['Being loaded for viewing'],
    actions: [],
  });
  expect(runViewing(4)).toEqual({
    lines: ['Loading for viewing failed', LOAD_FAILED],
    actions: ['Open run 4 for viewing…'],
  });
  expect(runViewing(3)).toEqual({
    lines: ['Viewing closed', 'Its days ran out.'],
    actions: ['Open run 3 for viewing…'],
  });
  expect(runViewing(2)).toEqual({
    lines: ['Viewing cancelled', 'Cancelled by Bo.'],
    actions: ['Open run 2 for viewing…'],
  });
  expect(runViewing(1)).toEqual({
    lines: ['Viewing closed — removing the copy', 'Closed by Ada Admin.'],
    actions: ['Open run 1 for viewing…'],
  });
  // The run's own pill stays first; the table keeps its six columns.
  expect(runRow(6).State).toBe('Completed');
  expect(within(card('Runs')).getAllByRole('columnheader')).toHaveLength(6);

  // View opens the archive viewer of the open view.
  const viewLink = screen.getByRole('link', {
    name: 'View the archive of run 6',
  });
  expect(viewLink).toHaveAttribute(
    'href',
    '/administration/history-archival/views/9',
  );
  fireEvent.click(viewLink);
  expect(navigated).toEqual(['/administration/history-archival/views/9']);
  expect(writes).toEqual([]);
});

test('HA-32: Cancel… and Close viewing… ask first and post to the view; a refusal stays in the dialog as given; a lost answer reloads the runs', async () => {
  runs = [
    VIEWABLE(4, { view: { id: 7, state: 'REQUESTED' } }),
    VIEWABLE(3, { view: { id: 6, state: 'OPEN' } }),
  ];
  views = [view(7, 4), OPEN_VIEW(6, 3)];
  renderSection();
  await loaded();

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel viewing of run 4…' }),
  );
  const cancel = screen.getByRole('dialog', {
    name: 'Cancel viewing of run 4?',
  });
  expect(cancel.textContent).toContain(
    'The archive of run 4 is not loaded, and nothing changes in the database. To view it later, open the run for viewing again.',
  );
  fireEvent.click(
    within(cancel).getByRole('button', { name: 'Keep waiting (Esc)' }),
  );
  expect(writes).toEqual([]);

  // A table read before the job took the view: the server's text.
  const LOADING =
    'View 7 of run 4 is being loaded on the server now and cannot be cancelled. Close it once it is open.';
  failures['POST view cancel'] = { status: 409, detail: LOADING };
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel viewing of run 4…' }),
  );
  const refused = screen.getByRole('dialog', {
    name: 'Cancel viewing of run 4?',
  });
  fireEvent.click(
    within(refused).getByRole('button', { name: 'Cancel viewing' }),
  );
  expect(await within(refused).findByRole('alert')).toHaveTextContent(LOADING);
  delete failures['POST view cancel'];
  fireEvent.click(
    within(refused).getByRole('button', { name: 'Cancel viewing' }),
  );
  expect(
    await within(card('Runs')).findByText(
      '✓ The viewing of run 4 is cancelled.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(runViewing(4)).toEqual({
      lines: ['Viewing cancelled', 'Cancelled by Ada Admin.'],
      actions: ['Open run 4 for viewing…'],
    }),
  );

  writes = [];
  fireEvent.click(
    screen.getByRole('button', { name: 'Close viewing of run 3…' }),
  );
  const close = screen.getByRole('dialog', { name: 'Close viewing of run 3?' });
  expect(close.textContent).toContain(
    'The viewer page of run 3 closes now, and the archival job on the server removes the loaded copy from the database at its next start. The archive files stay as they are: to read them again, open the run for viewing again.',
  );
  fireEvent.click(within(close).getByRole('button', { name: 'Close viewing' }));
  expect(
    await within(card('Runs')).findByText(
      '✓ The viewing of run 3 is closed. The archival job on the server removes its loaded copy at its next start.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/views/6/close',
      body: undefined,
    },
  ]);
  await waitFor(() =>
    expect(runViewing(3)).toEqual({
      lines: ['Viewing closed — removing the copy', 'Closed by Ada Admin.'],
      actions: ['Open run 3 for viewing…'],
    }),
  );
  cleanup();

  // A close whose answer is lost: the runs reload and the card says so.
  runs = [VIEWABLE(3, { view: { id: 6, state: 'OPEN' } })];
  views = [OPEN_VIEW(6, 3)];
  failures['POST view close'] = { status: 502, detail: 'Bad gateway.' };
  renderSection();
  await loaded();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Close viewing of run 3…' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Close viewing of run 3?' }),
    ).getByRole('button', { name: 'Close viewing' }),
  );
  expect(await within(card('Runs')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so the viewing of run 3 may or may not have been closed. Check its state in the table; closing it again is safe.',
  );
});

test('HA-33: a User without the key sees none of the viewing and reads no view; offline every viewing write is blocked while View stays', async () => {
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  runs = [
    VIEWABLE(6, { view: { id: 9, state: 'OPEN' } }),
    VIEWABLE(5, { view: { id: 8, state: 'REQUESTED' } }),
    VIEWABLE(4),
  ];
  views = [OPEN_VIEW(9, 6), view(8, 5)];
  renderSection();
  await loaded();
  // The settings stay readable, as every setting of the section.
  expect(rows(card('Retention and schedule'))).toMatchObject({
    'A loaded archive stays open': '7 days',
    'Runs loaded for viewing at once': '1',
  });
  expect(document.querySelector('.admin-runviewing')).toBeNull();
  expect(screen.queryByRole('link')).toBeNull();
  expect(card('Runs').textContent).not.toContain('for viewing');
  expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
    'Reload',
  ]);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => String(input).includes('/views')),
  ).toBe(false);
  cleanup();

  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE']);
  renderSection('unavailable');
  await loaded();
  await waitFor(() =>
    expect(runViewing(6).lines).toEqual([
      'Open for viewing',
      `Until ${formatInstant(OPEN_UNTIL)}`,
    ]),
  );
  expect(
    screen.getByRole('button', { name: 'Close viewing of run 6…' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Cancel viewing of run 5…' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Open run 4 for viewing…' }),
  ).toBeDisabled();
  // A navigation and a read: View stays available while disconnected.
  expect(
    screen.getByRole('link', { name: 'View the archive of run 6' }),
  ).toHaveAttribute('href', '/administration/history-archival/views/9');
});
