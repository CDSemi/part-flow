import './priority.css';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';

import { areaRefColor } from '../../api/area-inventory';
import { ApiError, errorMessage } from '../../api/client';
import {
  applyHotListChange,
  getHotList,
  getHotListCandidates,
  hotListChanged,
} from '../../api/hot-list';
import type {
  HotList,
  HotListAction,
  HotListCandidates,
  HotListChangeInput,
  HotListEntry,
  HotListLocation,
} from '../../api/hot-list';
import { newDeviceEventId } from '../../api/production-release';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot, HotPn, TypeChip } from '../../components/indicators';
import { useToastNotice } from '../../components/toast-notice';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { ErrorState, LoadingState } from '../../components/view-states';
import { useUiClock } from '../../components/ui-clock';
import {
  DEFAULT_DUE_SOON_POLICY,
  dueCountdown,
  formatIsoDate,
  formatIsoDateShort,
} from '../dates';
import {
  completeRedo,
  completeUndo,
  dropStep,
  rebaseOp,
  recordChange,
  useHotHistory,
} from './hot-history';
import type { HistoryOp, HistoryStep } from './hot-history';

/** The derived due countdown of one demand: its own due date against
 * its Work Order's received date (the lead time of the Due Soon
 * window) under the shared Due Soon policy. */
function dueInfo(entry: HotListEntry, now: number) {
  return dueCountdown(entry.dueDate, now, {
    received: entry.workOrderReceivedDate,
    policy: DEFAULT_DUE_SOON_POLICY,
  });
}

/** `WO 007001`, or `WO —` for an internal Work Order (display-only). */
const workOrderText = (entry: HotListEntry) =>
  `WO ${entry.workOrderNumber ?? '—'}`;

/** The quiet label that keeps an internal Work Order recognizable. */
const internalLabel = (entry: HotListEntry) =>
  `internal Work Order · received ${formatIsoDate(entry.workOrderReceivedDate)}`;

/**
 * One demand named in notices and confirmations: PN + Work Order, and
 * for an internal Work Order its label and quantity, so several
 * internal demands of one PN stay distinguishable.
 */
function entryName(entry: HotListEntry): string {
  const internal =
    entry.workOrderNumber === null
      ? ` (${internalLabel(entry)} · ${entry.requestedQuantity} pcs)`
      : '';
  return `${entry.partNumber} · ${workOrderText(entry)}${internal}`;
}

type ReorderAction =
  'Drag and drop' | 'Move Up' | 'Move Down' | 'Undo' | 'Redo';

/** The Hot list command each confirmed order change travels as. */
const REORDER_COMMANDS: Record<ReorderAction, HotListAction> = {
  'Drag and drop': 'DRAG',
  'Move Up': 'MOVE_UP',
  'Move Down': 'MOVE_DOWN',
  Undo: 'UNDO',
  Redo: 'REDO',
};

// Undo/Redo confirmations lead with what the confirmation does, not with
// the implementation action name (GUI change §9.1); the action name stays
// available as secondary detail.
const REORDER_TITLES: Record<ReorderAction, string> = {
  'Drag and drop': 'Confirm Hot ranking change',
  'Move Up': 'Confirm Hot ranking change',
  'Move Down': 'Confirm Hot ranking change',
  Undo: 'Restore previous ranking',
  Redo: 'Reapply ranking',
};

const RESTORE_SUMMARIES: Partial<Record<ReorderAction, string>> = {
  Undo: 'The Hot ranking returns to its previous confirmed order.',
  Redo: 'The last undone ranking change is applied again.',
};

const REORDER_NOTICES: Record<ReorderAction, string> = {
  'Drag and drop': '🔥 Hot ranking updated',
  'Move Up': '🔥 Hot ranking updated',
  'Move Down': '🔥 Hot ranking updated',
  Undo: '⟲ Previous Hot ranking restored',
  Redo: '⟳ Ranking change reapplied',
};

/** What a successful submission does to the session history. */
type HistoryEffect =
  | { kind: 'record'; step: HistoryStep }
  | { kind: 'undo'; step: HistoryStep }
  | { kind: 'redo'; step: HistoryStep };

/**
 * One submitted Hot list change. It keeps its `deviceEventId` until it
 * resolves: a Retry after an unknown outcome resends exactly this
 * request, so the server replays a committed change instead of applying
 * it twice.
 */
interface Submission {
  input: HotListChangeInput;
  effect: HistoryEffect;
  /** Toast shown once the change is applied. */
  success: string;
}

/** Persistent inline feedback (the toast carries successes only). */
interface ViewMessage {
  tone: 'info' | 'warn' | 'error';
  text: string;
}

interface RankChange {
  id: number;
  /** The full demand entry (PN + explicit WO/Job metadata fields). */
  entry: HotListEntry;
  /** Current rank; null when the entry is not currently listed. */
  from: number | null;
  /** Proposed rank; null when the entry leaves the list. */
  to: number | null;
}

interface PendingReorder {
  action: ReorderAction;
  /** List order before the pending change (Current Position snapshot). */
  current: HotListEntry[];
  next: HotListEntry[];
  changes: RankChange[];
  /** Entry the user acted on directly; null for Undo/Redo restores. */
  movedId: number | null;
  effect: HistoryEffect;
}

const idOf = (entry: HotListEntry) => entry.workOrderDemandId;

/**
 * Every entry whose rank would change, current → proposed, keyed by the
 * Work Order Demand id. Entries that would join or leave the list (an
 * Undo/Redo may restore a removed entry or take back an added one) are
 * included with a null rank on the absent side, so those restores are
 * real changes rather than silent no-ops.
 */
function diffRanks(
  current: readonly HotListEntry[],
  next: readonly HotListEntry[],
): RankChange[] {
  const changes: RankChange[] = [];
  next.forEach((entry, index) => {
    const from = current.findIndex((h) => idOf(h) === idOf(entry));
    if (from !== index) {
      changes.push({
        id: idOf(entry),
        entry,
        from: from === -1 ? null : from + 1,
        to: index + 1,
      });
    }
  });
  current.forEach((entry, index) => {
    if (!next.some((h) => idOf(h) === idOf(entry))) {
      changes.push({ id: idOf(entry), entry, from: index + 1, to: null });
    }
  });
  // Proposed order keeps the comparison scannable; departures go last.
  changes.sort(
    (a, b) =>
      (a.to ?? Number.MAX_SAFE_INTEGER) - (b.to ?? Number.MAX_SAFE_INTEGER),
  );
  return changes;
}

/** e.g. "2 other demands will shift down." — real counts from the diff. */
function shiftSummary(others: readonly RankChange[]): string | null {
  const ranked = others.filter((c) => c.from !== null && c.to !== null);
  const up = ranked.filter((c) => (c.to as number) < (c.from as number)).length;
  const down = ranked.length - up;
  const parts: string[] = [];
  if (up) parts.push(`${up} other demand${up === 1 ? '' : 's'} will shift up`);
  if (down) {
    parts.push(`${down} other demand${down === 1 ? '' : 's'} will shift down`);
  }
  return parts.length ? `${parts.join('; ')}.` : null;
}

// Hot WO Demand ranking on the live Hot list (Phase 12). Every change is
// ONE server command: adding at the bottom applies directly; removal and
// every change to the order of existing entries (drag, Move Up/Down,
// Undo, Redo) are confirmed first. The visible list is never renumbered
// before the server answers — it then renders the committed entries.
// A change whose outcome is unknown freezes every write until it is
// retried with the same idempotency key or abandoned by reloading.
export function PriorityView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const disconnected = status !== 'connected';
  const { showNotice, noticeElement } = useToastNotice();
  // Loaded on view activation (mount); every command answers with the
  // committed list, which then replaces the loaded one.
  const listData = useApiData(getHotList);
  const history = useHotHistory();
  const [override, setOverride] = useState<{
    base: HotList;
    entries: HotListEntry[];
  } | null>(null);
  const [message, setMessage] = useState<ViewMessage | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const [unknownOutcome, setUnknownOutcome] = useState<Submission | null>(null);
  const submitting = useRef(false);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<HotListEntry | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [pending, setPending] = useState<PendingReorder | null>(null);
  // Shared minute clock: due countdowns are derived at render from the
  // fixed due dates and keep updating while the view stays open.
  const now = useUiClock('minute');
  const addButton = useRef<HTMLButtonElement>(null);
  const retryButton = useRef<HTMLButtonElement>(null);

  // Focus discipline: the control that opened a confirmation is frozen
  // while the change is in flight, so focus would be lost. Once the
  // submission settles, an unknown outcome takes focus to its Retry;
  // otherwise lost focus returns to "+ Add to Hot list".
  useEffect(() => {
    if (inFlight) return;
    if (unknownOutcome) {
      retryButton.current?.focus();
      return;
    }
    const active = document.activeElement;
    if (!active || active === document.body) addButton.current?.focus();
  }, [inFlight, unknownOutcome]);

  const loaded = listData.state.status === 'ready' ? listData.state.data : null;
  // A command's committed entries apply to the read they were made
  // against; a fresh read (reload) supersedes them.
  const hotList =
    loaded && override?.base === loaded ? override.entries : loaded?.entries;
  const entries = preview === 'empty' ? [] : (hotList ?? []);
  const order = entries.map(idOf);
  // Writes are blocked while disconnected (GUI_DESIGN §3 rule 6), while
  // a submission is in flight, and while one has an unknown outcome.
  const writesFrozen =
    disconnected || inFlight || unknownOutcome !== null || loaded === null;

  async function submit(submission: Submission) {
    if (submitting.current || disconnected || loaded === null) return;
    submitting.current = true;
    const base = loaded;
    setInFlight(true);
    setMessage(null);
    try {
      const result = await applyHotListChange(submission.input);
      setUnknownOutcome(null);
      setOverride({ base, entries: result.entries });
      const { effect } = submission;
      if (effect.kind === 'record') recordChange(effect.step);
      else if (effect.kind === 'undo') completeUndo(effect.step);
      else completeRedo(effect.step);
      showNotice(
        result.created
          ? submission.success
          : `${submission.success} — it had already been applied`,
      );
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        // The intent is frozen: the exact same request replays the
        // committed change or applies it once.
        setUnknownOutcome(submission);
      } else {
        setUnknownOutcome(null);
        handleRefusal(error, submission, base);
      }
    } finally {
      submitting.current = false;
      setInFlight(false);
    }
  }

  /** An explicit server refusal — nothing was written. */
  function handleRefusal(
    error: unknown,
    submission: Submission,
    base: HotList,
  ) {
    const current = hotListChanged(error);
    if (current) {
      // Changed elsewhere: show the current list; both histories stay,
      // and a later Undo/Redo re-bases on this list.
      setOverride({ base, entries: current });
      setMessage({ tone: 'warn', text: errorMessage(error) });
      return;
    }
    const { effect } = submission;
    const op =
      effect.kind === 'undo'
        ? effect.step.undo
        : effect.kind === 'redo'
          ? effect.step.redo
          : null;
    if (
      op?.kind === 'insert' &&
      error instanceof ApiError &&
      (error.status === 404 || error.status === 409)
    ) {
      // The demand is gone or no longer eligible: this step can never
      // be applied again.
      dropStep(effect.step);
      setMessage({
        tone: 'warn',
        text: `${errorMessage(error)} This step was removed from the history.`,
      });
      return;
    }
    setMessage({ tone: 'error', text: errorMessage(error) });
  }

  function abandonUnknownOutcome() {
    setUnknownOutcome(null);
    listData.reload();
    setMessage({
      tone: 'warn',
      text: 'The change may already have been applied. The list was reloaded — check it before making another change.',
    });
  }

  function entriesFor(ids: readonly number[], extra?: HotListEntry) {
    const byId = new Map(entries.map((entry) => [idOf(entry), entry]));
    if (extra && !byId.has(idOf(extra))) byId.set(idOf(extra), extra);
    return ids.flatMap((id) => {
      const entry = byId.get(id);
      return entry ? [entry] : [];
    });
  }

  /** Ask for confirmation before any change to the order. */
  function requestReorder(
    action: ReorderAction,
    newOrder: number[],
    movedId: number | null,
    effect: HistoryEffect,
  ) {
    const next = entriesFor(newOrder, effect.step.entrySnapshot);
    const changes = diffRanks(entries, next);
    if (!changes.length) return;
    setMessage(null);
    setPending({ action, current: entries, next, changes, movedId, effect });
  }

  function confirmPending() {
    if (!pending || writesFrozen) return;
    const { action, current, next, effect } = pending;
    setPending(null);
    void submit({
      input: {
        deviceEventId: newDeviceEventId(),
        action: REORDER_COMMANDS[action],
        expectedOrder: current.map(idOf),
        newOrder: next.map(idOf),
      },
      effect,
      success: REORDER_NOTICES[action],
    });
  }

  /** Re-base one history step on the current list (Undo/Redo). */
  function stepHistory(action: 'Undo' | 'Redo', step: HistoryStep) {
    const op: HistoryOp = action === 'Undo' ? step.undo : step.redo;
    const rebased = rebaseOp(op, step.demandId, order);
    const name = entryName(step.entrySnapshot);
    if (rebased.kind === 'inapplicable') {
      dropStep(step);
      setMessage({
        tone: 'warn',
        text:
          op.kind === 'insert'
            ? `${name}: this entry is already on the Hot list, so this step was removed from the history.`
            : `${name}: this entry is no longer on the Hot list, so this step was removed from the history.`,
      });
      return;
    }
    if (rebased.kind === 'noop') {
      if (action === 'Undo') completeUndo(step);
      else completeRedo(step);
      setMessage({
        tone: 'info',
        text: `${name} is already at #${order.indexOf(step.demandId) + 1}, so nothing needs to change. The step moved to ${action === 'Undo' ? 'Redo' : 'Undo'}.`,
      });
      return;
    }
    requestReorder(action, rebased.newOrder, null, {
      kind: action === 'Undo' ? 'undo' : 'redo',
      step,
    });
  }

  function undo() {
    const step = history.undo[history.undo.length - 1];
    if (step && !writesFrozen) stepHistory('Undo', step);
  }

  function redo() {
    const step = history.redo[history.redo.length - 1];
    if (step && !writesFrozen) stepHistory('Redo', step);
  }

  function moveTo(action: ReorderAction, fromIndex: number, toIndex: number) {
    if (writesFrozen || fromIndex === toIndex) return;
    if (toIndex < 0 || toIndex >= entries.length) return;
    const moved = entries[fromIndex];
    const newOrder = order.filter((id) => id !== idOf(moved));
    newOrder.splice(toIndex, 0, idOf(moved));
    requestReorder(action, newOrder, idOf(moved), {
      kind: 'record',
      step: {
        demandId: idOf(moved),
        entrySnapshot: moved,
        undo: { kind: 'move', toIndex: fromIndex },
        redo: { kind: 'move', toIndex },
      },
    });
  }

  function handleDrop(event: DragEvent, targetIndex: number) {
    event.preventDefault();
    if (dragId === null) return;
    const fromIndex = order.indexOf(dragId);
    setDragId(null);
    if (fromIndex < 0) return;
    moveTo('Drag and drop', fromIndex, targetIndex);
  }

  function addCandidate(candidate: HotListEntry) {
    setAddOpen(false);
    if (writesFrozen) return;
    // Adding appends at the bottom — existing ranks are not reordered,
    // so no order-change confirmation is required.
    void submit({
      input: {
        deviceEventId: newDeviceEventId(),
        action: 'ADD',
        expectedOrder: order,
        newOrder: [...order, idOf(candidate)],
      },
      effect: {
        kind: 'record',
        step: {
          demandId: idOf(candidate),
          entrySnapshot: candidate,
          undo: { kind: 'remove' },
          redo: { kind: 'insert', index: order.length },
        },
      },
      success: `🔥 ${entryName(candidate)} added at the bottom — rank #${order.length + 1}`,
    });
  }

  function confirmRemove(target: HotListEntry) {
    setRemoveTarget(null);
    const index = order.indexOf(idOf(target));
    if (writesFrozen || index < 0) return;
    void submit({
      input: {
        deviceEventId: newDeviceEventId(),
        action: 'REMOVE',
        expectedOrder: order,
        newOrder: order.filter((id) => id !== idOf(target)),
      },
      effect: {
        kind: 'record',
        step: {
          demandId: idOf(target),
          entrySnapshot: target,
          undo: { kind: 'insert', index },
          redo: { kind: 'remove' },
        },
      },
      success: `✕ ${entryName(target)} removed from Hot list — remaining ranks close the gap · Undo can restore it`,
    });
  }

  const header = (
    <>
      <div className="pr-head">
        <h1>Priority Management — Hot WO Demand</h1>
        <span className="spacer" />
        <button
          ref={addButton}
          className="btn primary"
          disabled={writesFrozen}
          onClick={() => {
            setMessage(null);
            setAddOpen(true);
          }}
        >
          + Add to Hot list
        </button>
      </div>
      <p className="pr-sub">
        Priority belongs to <b>Work Order Demand</b>, ranked per Department.
        Drag (or use the arrow buttons) to reorder — every reorder of existing
        entries asks for confirmation before it is applied, and ✕ removes with
        its own confirmation. Confirmed changes can be stepped back and forward
        with Undo/Redo. New Hot entries are added at the bottom. Multiple Work
        Orders for the same PN may hold different priorities.
      </p>
    </>
  );

  if (preview === 'loading' || listData.state.status === 'loading') {
    return (
      <section className="pr-view" aria-label="Priority Management">
        {header}
        <LoadingState label="Loading Priority Management" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="pr-view" aria-label="Priority Management">
        {header}
        <ErrorState
          message="The Hot list could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }
  if (listData.state.status === 'error') {
    return (
      <section className="pr-view" aria-label="Priority Management">
        {header}
        <ErrorState
          message="The Hot list could not be loaded."
          detail={listData.state.message}
          onRetry={listData.reload}
        />
      </section>
    );
  }

  return (
    <section className="pr-view" aria-label="Priority Management">
      {header}

      {unknownOutcome ? (
        <div className="pr-msg warn" role="alert">
          <div>
            The server did not answer — this Hot list change may already have
            been applied. Retry the same change to find out: the server answers
            with the recorded result, or applies it once. Or reload the list and
            check it. Nothing else can be changed until then.
          </div>
          <div className="pr-msgbtns">
            <button
              ref={retryButton}
              className="btn primary"
              disabled={inFlight || disconnected}
              onClick={() => void submit(unknownOutcome)}
            >
              Retry the same change
            </button>
            <button
              className="btn ghost"
              disabled={inFlight}
              onClick={abandonUnknownOutcome}
            >
              Reload list
            </button>
          </div>
        </div>
      ) : message ? (
        <div
          className={`pr-msg ${message.tone}`}
          role={message.tone === 'info' ? 'status' : 'alert'}
        >
          {message.text}
        </div>
      ) : null}

      {entries.length === 0 ? (
        <div className="pr-empty">
          No Hot WO Demand — add one with “+ Add to Hot list”, or scan a PN
          barcode in the add dialog.
        </div>
      ) : (
        <ol className="pr-list" style={{ listStyle: 'none' }}>
          {entries.map((entry, index) => {
            const due = dueInfo(entry, now);
            return (
              <li
                key={idOf(entry)}
                className={`pr-item ${dragId === idOf(entry) ? 'dragging' : ''}`}
                draggable={!writesFrozen}
                onDragStart={() => setDragId(idOf(entry))}
                onDragEnd={() => setDragId(null)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => handleDrop(e, index)}
              >
                <span className="grip" aria-hidden="true">
                  ⠿
                </span>
                <span className="body">
                  <span className="l1">
                    <HotPn
                      rank={entry.rank ?? index + 1}
                      pn={entry.partNumber}
                      pnClassName="pn"
                    />
                    <WoJobChip entry={entry} />
                    {entry.workOrderNumber === null ? (
                      <span className="pr-quiet">{internalLabel(entry)}</span>
                    ) : null}
                    <TypeChip type={entry.requestType} />
                    {entry.workOrderCompleted ? (
                      <span className="wostat completed">Completed</span>
                    ) : null}
                  </span>
                  <span className="l2">
                    <span>requested {entry.requestedQuantity}</span>
                    <span>allocated {entry.allocatedQuantity}</span>
                    <span>shortage {entry.shortageQuantity}</span>
                    {!entry.workOrderCompleted &&
                    entry.shortageQuantity === 0 ? (
                      <span className="pr-quiet">
                        Fully allocated — nothing left to expedite
                      </span>
                    ) : null}
                  </span>
                  <Distribution entry={entry} />
                </span>
                <span className="due">
                  <span>{formatIsoDateShort(entry.dueDate)}</span>
                  <span
                    className={`d2 ${due.dueClass}`}
                    style={{ display: 'block' }}
                  >
                    {due.note}
                  </span>
                </span>
                <span className="movebtns">
                  <button
                    aria-label={`Move ${entry.partNumber} up`}
                    disabled={writesFrozen || index === 0}
                    onClick={() => moveTo('Move Up', index, index - 1)}
                  >
                    ▲
                  </button>
                  <button
                    aria-label={`Move ${entry.partNumber} down`}
                    disabled={writesFrozen || index === entries.length - 1}
                    onClick={() => moveTo('Move Down', index, index + 1)}
                  >
                    ▼
                  </button>
                </span>
                <button
                  className="pr-x"
                  title="Remove from Hot list"
                  aria-label={`Remove ${entry.partNumber} from Hot list`}
                  disabled={writesFrozen}
                  onClick={() => {
                    setMessage(null);
                    setRemoveTarget(entry);
                  }}
                >
                  ✕
                </button>
              </li>
            );
          })}
        </ol>
      )}

      <div className="pr-bar">
        <button
          className="btn ghost"
          disabled={writesFrozen || !history.undo.length}
          onClick={undo}
        >
          ⟲ Undo
        </button>
        <button
          className="btn ghost"
          disabled={writesFrozen || !history.redo.length}
          onClick={redo}
        >
          ⟳ Redo
        </button>
        {inFlight ? (
          <span className="pr-busy" role="status">
            Applying the change…
          </span>
        ) : null}
      </div>

      <PageNote>
        <b>Hot</b> demand is always worked first, in rank order. Allocation
        &amp; work ordering: ① Hot rank ② demands with a due date, earliest
        first ③ demands without a due date, by the Work Order received date
        (oldest first).
      </PageNote>

      {pending ? (
        <ReorderConfirmDialog
          pending={pending}
          disabled={writesFrozen}
          onCancel={() => setPending(null)}
          onConfirm={confirmPending}
        />
      ) : null}

      {addOpen && (
        <HotAddDialog
          disabled={writesFrozen}
          onCancel={() => setAddOpen(false)}
          onAdd={addCandidate}
        />
      )}

      {removeTarget !== null && (
        <ModalDialog
          label="Remove from Hot list?"
          onClose={() => setRemoveTarget(null)}
        >
          <h3>Remove from Hot list?</h3>
          <div className="big mono">{removeTarget.partNumber}</div>
          <div className="sub">
            Work Order Demand{' '}
            <b className="mono">{woJobLabel(removeTarget, true)}</b> will be
            removed from the Hot ranking. Remaining ranks close the gap; Undo
            can restore the entry.
          </div>
          <div className="row">
            <button
              className="bigbtn ghost"
              onClick={() => setRemoveTarget(null)}
            >
              Cancel (Esc)
            </button>
            <button
              className="bigbtn danger"
              disabled={writesFrozen}
              onClick={() => confirmRemove(removeTarget)}
            >
              Remove entry
            </button>
          </div>
        </ModalDialog>
      )}
      {noticeElement}
    </section>
  );
}

/**
 * The WO + Job Number label, built from the explicit `workOrderNumber`
 * / `jobNumbers` fields — never parsed out of a display string.
 * `detailed` adds the internal Work Order label and the quantity, so
 * several internal demands of one PN stay distinguishable.
 */
function woJobLabel(entry: HotListEntry, detailed = false): string {
  const jobs = entry.jobNumbers.length
    ? ` · Job ${entry.jobNumbers.join(', ')}`
    : '';
  const internal =
    detailed && entry.workOrderNumber === null
      ? ` · ${internalLabel(entry)} · ${entry.requestedQuantity} pcs`
      : '';
  return `${workOrderText(entry)}${jobs}${internal}`;
}

/**
 * WO + Job Number metadata as one light informational chip, visually
 * separate from the PN. The full demand label stays available as a
 * tooltip.
 */
function WoJobChip({
  entry,
  detailed = false,
}: {
  entry: HotListEntry;
  detailed?: boolean;
}) {
  return (
    <span className="wjchip" title={woJobLabel(entry, true)}>
      {woJobLabel(entry, detailed)}
    </span>
  );
}

const LOCATION_STATES: Record<HotListLocation['state'], string> = {
  MACHINE: 'on machine',
  QUEUE: 'queue',
  PROCESSING: 'processing',
  DONE: 'done',
};

/**
 * The PN's current distribution in the Department — labeled as the
 * PN's, since every demand of the PN shares the same quantity and none
 * of it is attributed to this one demand.
 */
function Distribution({ entry }: { entry: HotListEntry }) {
  const locations = entry.partNumberLocations;
  return (
    <span className="l3">
      <span className="dlbl">{entry.partNumber} in production</span>
      {locations.length === 0 ? (
        <span className="pr-quiet">
          {entry.releasedQuantity === 0
            ? 'Not yet released'
            : 'No active quantity — released quantity is stocked, scrapped or awaiting allocation'}
        </span>
      ) : (
        <>
          {locations.map((location, index) => (
            <span
              key={`${location.area.id}-${location.machine?.id ?? ''}-${location.state}-${index}`}
              className="dloc"
            >
              <AreaDot colorVar={areaRefColor(location.area)} size={9} />
              {location.area.name}
              {location.machine ? ` · ${location.machine.name}` : ''}{' '}
              <b>{location.quantity}</b>{' '}
              <span className="dstate">
                {location.state === 'PROCESSING' && location.activity
                  ? location.activity
                  : LOCATION_STATES[location.state]}
              </span>
            </span>
          ))}
          {entry.releasedQuantity === 0 ? (
            <span className="pr-quiet">not yet released for this demand</span>
          ) : null}
        </>
      )}
    </span>
  );
}

/** One entry line inside a Current/New Position snapshot. */
interface SnapshotRow {
  id: number;
  entry: HotListEntry;
  /** Rank in this snapshot; null renders the `Not listed` placeholder. */
  rank: number | null;
  /**
   * Current (pre-change) rank — the New Position side renders every
   * row as a `#current → #new` transition; null renders the explicit
   * `Not listed` origin for a newly listed entry.
   */
  fromRank?: number | null;
  /** `up` moves toward #1, `down` away from it (Current side only). */
  direction?: 'up' | 'down';
  moved: boolean;
}

/**
 * The rows of one snapshot side, restricted to the affected rank range
 * [lo..hi]. Entries that do not exist on this side (an Undo/Redo may
 * restore a removed entry or take back an added one) are appended with
 * `rank: null` — shown as `Not listed`, never silently omitted. When
 * `currentRanks` is given (the New Position side), every row also
 * carries its pre-change rank so the transition `#old → #new` can be
 * rendered — including `Not listed → #n` for a restored entry and
 * `#n → Not listed` for a removed one.
 */
function snapshotRows(
  list: readonly HotListEntry[],
  changes: readonly RankChange[],
  movedId: number | null,
  lo: number,
  hi: number,
  withDirections: boolean,
  currentRanks?: ReadonlyMap<number, number>,
): SnapshotRow[] {
  const rows: SnapshotRow[] = list
    .map((entry, index) => ({ entry, rank: index + 1 }))
    .filter(({ rank }) => rank >= lo && rank <= hi)
    .map(({ entry, rank }) => {
      const change = changes.find((c) => c.id === idOf(entry));
      const direction =
        withDirections && change && change.from !== null && change.to !== null
          ? change.to < change.from
            ? ('up' as const)
            : ('down' as const)
          : undefined;
      return {
        id: idOf(entry),
        entry,
        rank,
        fromRank: currentRanks
          ? (currentRanks.get(idOf(entry)) ?? null)
          : undefined,
        direction,
        moved: idOf(entry) === movedId,
      };
    });
  for (const change of changes) {
    if (!list.some((entry) => idOf(entry) === change.id)) {
      rows.push({
        id: change.id,
        entry: change.entry,
        rank: null,
        fromRank: currentRanks ? change.from : undefined,
        moved: change.id === movedId,
      });
    }
  }
  return rows;
}

/** `#n`, or the explicit `Not listed` placeholder — never omitted. */
function RankLabel({ rank }: { rank: number | null }) {
  if (rank === null) return <span className="notlisted">Not listed</span>;
  return <>#{rank}</>;
}

function SnapshotSection({
  title,
  rows,
}: {
  title: string;
  rows: SnapshotRow[];
}) {
  return (
    <div className="pr-snapshot">
      <h4 className="pr-snaptitle">{title}</h4>
      <ul className="pr-snaplist">
        {/* Vertical divider between the shared position track and the
            PN column (v15): one grid item spanning every row of this
            section — attached to the shared track edge, never a
            per-content-row border that could disturb the subgrid
            alignment. Rendered only where the subgrid chain is active
            (priority.css); the row count places it without creating
            implicit rows. */}
        <span
          className="pr-snapdivider"
          aria-hidden="true"
          style={{ gridRow: `1 / span ${Math.max(1, rows.length)}` }}
        />
        {rows.map((row, index) => (
          // Explicit row placement (v15): the divider above occupies
          // column 2 across these rows, and auto-placed full-width
          // items would be pushed BELOW a definite-position item —
          // pinning each row to its own line keeps the deliberate
          // overlap and the original order.
          <li
            key={row.id}
            style={{ gridRow: index + 1 }}
            className={`pr-snaprow ${row.moved ? 'moved' : 'shifted'}${
              row.rank === null ? ' absent' : ''
            }${row.fromRank !== undefined ? ' trans' : ''}`}
          >
            <span className="prr">
              {row.fromRank !== undefined ? (
                // New Position side: every row reads as its complete
                // rank transition `#old → #new`, with the add/remove
                // edge cases spelled out (`Not listed → #n`,
                // `#n → Not listed`) — a missing side is never
                // silently omitted.
                <>
                  <span className="prfrom">
                    <RankLabel rank={row.fromRank} />
                  </span>{' '}
                  <span className="prarrow">→</span>{' '}
                  <span className="prto">
                    <RankLabel rank={row.rank} />
                  </span>
                </>
              ) : row.rank === null ? (
                <span className="notlisted">Not listed</span>
              ) : (
                <>
                  #{row.rank}
                  {row.direction ? (
                    <span
                      className={`dir ${row.direction}`}
                      title={
                        row.direction === 'up'
                          ? 'Moves toward rank #1'
                          : 'Moves away from rank #1'
                      }
                    >
                      {row.direction === 'up' ? '↑' : '↓'}
                    </span>
                  ) : null}
                </>
              )}
            </span>
            <span className="prpn mono" title={row.entry.partNumber}>
              {row.entry.partNumber}
            </span>
            <WoJobChip entry={row.entry} detailed />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Reorder confirmation as two snapshots: `Current Position` (order
 * before the change, with per-row direction arrows beside the current
 * rank), one centered transition arrow, and `New Position` (order after
 * the change, no per-row arrows). Both sections show exactly the
 * affected rank range. Undo/Redo restores use the same layout and lead
 * with what the restore does; an entry that exists on only one side
 * renders a `Not listed` placeholder on the other.
 */
function ReorderConfirmDialog({
  pending,
  disabled,
  onCancel,
  onConfirm,
}: {
  pending: PendingReorder;
  disabled: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const title = REORDER_TITLES[pending.action];
  const moved =
    pending.movedId !== null
      ? pending.changes.find((c) => c.id === pending.movedId)
      : undefined;
  const others = pending.changes.filter((c) => c !== moved);
  const shifts = moved ? shiftSummary(others) : null;
  const affectedRanks = pending.changes
    .flatMap((c) => [c.from, c.to])
    .filter((rank): rank is number => rank !== null);
  const lo = Math.min(...affectedRanks);
  const hi = Math.max(...affectedRanks);
  // Pre-change rank per entry: the New Position side renders every row
  // as its complete `#current → #new` transition.
  const currentRanks = new Map(
    pending.current.map((entry, index) => [idOf(entry), index + 1]),
  );
  return (
    <ModalDialog label={title} onClose={onCancel}>
      <h3>{title}</h3>
      {moved ? (
        <div className="pr-move-summary">
          Move <span className="mono">{moved.entry.partNumber}</span> ·{' '}
          <span className="mono">{workOrderText(moved.entry)}</span> from{' '}
          <b>{moved.from === null ? 'unlisted' : `#${moved.from}`}</b> to{' '}
          <b>{moved.to === null ? 'unlisted' : `#${moved.to}`}</b>
        </div>
      ) : (
        <div className="pr-move-summary">
          {RESTORE_SUMMARIES[pending.action]}
        </div>
      )}
      {/* Compact impact/action block: the shift impact reads as a
          sentence; the Action label sits apart from its value, and the
          value is emphasized through weight and semantic text only —
          no decorative pill, and never louder than the moved-PN
          summary above. */}
      <div className="pr-impact">
        {shifts ? <span className="pr-shifts">{shifts}</span> : null}
        <span className="pr-action">
          <span className="pr-actionlbl">Action</span>
          <span className="pr-actionval">{pending.action}</span>
        </span>
      </div>
      {/* One shared grid wrapper around BOTH snapshot sections: the
          content-sized position track is common to Current Position
          and New Position (subgrid chain in priority.css), so the PN
          column sits at the same offset in both sections — sized by
          the widest real position value of either side, with no
          overlap and no wide fixed label column. */}
      <div className="pr-snapwrap">
        <SnapshotSection
          title="Current Position"
          rows={snapshotRows(
            pending.current,
            pending.changes,
            pending.movedId,
            lo,
            hi,
            true,
          )}
        />
        {/* The single transition arrow: Current Position → New
            Position. Distinct from the per-row rank direction arrows
            above. */}
        <div className="pr-transition" aria-hidden="true">
          ↓
        </div>
        <SnapshotSection
          title="New Position"
          rows={snapshotRows(
            pending.next,
            pending.changes,
            pending.movedId,
            lo,
            hi,
            false,
            currentRanks,
          )}
        />
      </div>
      <div className="sub">No ranks change until you confirm.</div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={disabled}
          onClick={onConfirm}
        >
          Apply ranking
        </button>
      </div>
    </ModalDialog>
  );
}

/** A free-text search waits for a short typing pause before it runs. */
const SEARCH_DEBOUNCE_MS = 200;

/** A scanned (or typed) barcode — resolved on Enter, never searched. */
const isBarcodeInput = (text: string) => text.toUpperCase().startsWith('PF:');

type CandidateList = HotListCandidates & {
  /** What produced the list: everything eligible, a search, or a scan. */
  source: 'all' | 'search' | 'barcode';
  term: string;
};

function HotAddDialog({
  disabled,
  onCancel,
  onAdd,
}: {
  disabled: boolean;
  onCancel: () => void;
  onAdd: (candidate: HotListEntry) => void;
}) {
  const [query, setQuery] = useState('');
  const [list, setList] = useState<CandidateList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Feedback of the last barcode scan: nothing eligible, rejected, or
  // ambiguous (several eligible WO Demands share the scanned PN — the
  // list then shows exactly those and an explicit selection is
  // required). Cleared as soon as the user edits the search text.
  const [scanFeedback, setScanFeedback] = useState<ViewMessage | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Only the latest candidate request may present its answer.
  const generation = useRef(0);
  const now = useUiClock('minute');

  const runSearch = useCallback(async (term: string) => {
    const requested = ++generation.current;
    try {
      const data = await getHotListCandidates(term ? { search: term } : {});
      if (generation.current !== requested) return;
      setList({ ...data, source: term ? 'search' : 'all', term });
      setLoadError(null);
    } catch (error) {
      if (generation.current !== requested) return;
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    const term = query.trim();
    // A barcode is resolved on Enter only — never searched as text.
    if (isBarcodeInput(term)) return;
    const timer = setTimeout(
      () => void runSearch(term),
      term ? SEARCH_DEBOUNCE_MS : 0,
    );
    return () => clearTimeout(timer);
  }, [query, runSearch]);

  /** Keep the dialog ready for the next scan: the rejected value stays
   * visible, selected, so the next scan replaces it. */
  function readyForNextScan() {
    inputRef.current?.focus();
    inputRef.current?.select();
  }

  async function resolveBarcode(barcode: string) {
    const requested = ++generation.current;
    setScanFeedback(null);
    let data: HotListCandidates;
    try {
      data = await getHotListCandidates({ barcode });
    } catch (error) {
      if (generation.current !== requested) return;
      setScanFeedback({ tone: 'error', text: errorMessage(error) });
      readyForNextScan();
      return;
    }
    if (generation.current !== requested) return;
    const pn = data.partNumber ?? barcode;
    // Deterministic barcode resolution (PROJECT_PROFILE §21): no
    // eligible WO Demand adds nothing; exactly one adds directly;
    // several NEVER add by guess — the list shows exactly those and an
    // explicit selection is required.
    if (data.candidates.length === 0) {
      const listed =
        data.alreadyListedCount > 0
          ? ` — ${data.alreadyListedCount} already on the Hot list`
          : '';
      setScanFeedback({
        tone: 'error',
        text: `No eligible Work Order Demand for ${pn}${listed}. Nothing was added.`,
      });
      readyForNextScan();
      return;
    }
    if (data.candidates.length === 1) {
      if (disabled) {
        setScanFeedback({
          tone: 'error',
          text: 'Changes are blocked right now, so nothing was added. Try again once the Hot list is ready.',
        });
        return;
      }
      onAdd(data.candidates[0]);
      return;
    }
    setList({ ...data, source: 'barcode', term: pn });
    setScanFeedback({
      tone: 'info',
      text: `Multiple eligible Work Order Demands use PN ${pn} — select the Work Order to add.`,
    });
  }

  const candidates = list?.candidates ?? [];
  const listedNote =
    list && list.alreadyListedCount > 0
      ? ` — ${list.alreadyListedCount} already on the Hot list`
      : '';
  return (
    <ModalDialog label="Add WO Demand to Hot list" onClose={onCancel}>
      <h3>Add WO Demand to Hot list</h3>
      <div className="sub">
        Search by PN, WO or Job Number and select — or{' '}
        <b>scan the PN barcode</b> with this dialog open.
      </div>
      <input
        ref={inputRef}
        className="hotsearch"
        placeholder="Search PN, WO, Job Number… or scan PN barcode"
        aria-label="Search PN, WO, Job Number or scan PN barcode"
        autoComplete="off"
        autoFocus
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setScanFeedback(null);
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          const value = e.currentTarget.value.trim();
          if (!value) return;
          if (isBarcodeInput(value)) void resolveBarcode(value);
          else void runSearch(value);
        }}
      />
      {scanFeedback ? (
        <div
          className={`sub hotadd-feedback ${scanFeedback.tone}`}
          role={scanFeedback.tone === 'info' ? 'status' : 'alert'}
        >
          {scanFeedback.text}
        </div>
      ) : (
        <div className="sub">
          If a PN has multiple active WO Demands, each is listed separately.
        </div>
      )}
      <div className="hotaddlist">
        {loadError !== null ? (
          <div className="hotadd-empty" role="alert">
            {loadError}
          </div>
        ) : list === null ? (
          <div className="hotadd-empty" role="status">
            Loading eligible Work Order Demand…
          </div>
        ) : candidates.length ? (
          candidates.map((c) => {
            const due = dueInfo(c, now);
            return (
              <button
                key={idOf(c)}
                className="hotadd-item"
                disabled={disabled}
                onClick={() => onAdd(c)}
              >
                <span className="hpn">{c.partNumber}</span>
                <span className="hwo" title={woJobLabel(c, true)}>
                  {woJobLabel(c, true)} · shortage {c.shortageQuantity}
                </span>
                <TypeChip type={c.requestType} />
                <span
                  className={`hdue ${due.dueClass === 'late' ? 'late' : ''}`}
                >
                  {c.dueDate
                    ? `${formatIsoDateShort(c.dueDate)} · ${due.note}`
                    : due.note}
                </span>
              </button>
            );
          })
        ) : (
          <div className="hotadd-empty">
            {list.source === 'search'
              ? `No eligible Work Order Demand matches “${list.term}”${listedNote}`
              : `No eligible Work Order Demand${listedNote}`}
          </div>
        )}
        {list?.truncated ? (
          <div className="hotadd-empty">
            Showing the first {candidates.length} — refine the search to find
            the Work Order Demand.
          </div>
        ) : null}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
      </div>
    </ModalDialog>
  );
}
