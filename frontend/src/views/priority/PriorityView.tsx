import './priority.css';

import { useState } from 'react';
import type { DragEvent } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { DevNotice } from '../../components/DevNotice';
import { TypeChip } from '../../components/indicators';
import { useToastNotice } from '../../components/toast-notice';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { ErrorState, LoadingState } from '../../components/view-states';
import { MOCK_HOT_CANDIDATES, MOCK_HOT_LIST } from '../../mocks/priority';
import { useUiClock } from '../../components/ui-clock';
import {
  DEFAULT_DUE_SOON_POLICY,
  dueCountdown,
  formatIsoDateShort,
} from '../dates';
import type { MockHotEntry } from '../view-models';

// Hot entries carry no parent-WO received date, so the derived due
// countdown has no lead time — the shared Due Soon policy (the future
// Administration configuration) then applies its minimum-window
// fallback (views/dates `dueSoonWindowDays`).
const DUE_SOON = { received: null, policy: DEFAULT_DUE_SOON_POLICY };

const hotKey = (h: MockHotEntry) => `${h.pn}|${h.workOrder}`;

/** "WO 007001 · Job 18112" → "WO 007001" for compact one-line summaries. */
const shortWorkOrder = (workOrder: string) => workOrder.split(' ·')[0];

type ReorderAction =
  'Drag and drop' | 'Move Up' | 'Move Down' | 'Undo' | 'Redo';

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

interface RankChange {
  key: string;
  pn: string;
  workOrder: string;
  /** The full demand entry (PN + explicit WO/Job metadata fields). */
  entry: MockHotEntry;
  /** Current rank; null when the entry is not currently listed. */
  from: number | null;
  /** Proposed rank; null when the entry leaves the list. */
  to: number | null;
}

interface PendingReorder {
  action: ReorderAction;
  /** List order before the pending change (Current Position snapshot). */
  current: MockHotEntry[];
  next: MockHotEntry[];
  changes: RankChange[];
  /** Entry the user acted on directly; null for Undo/Redo restores. */
  movedKey: string | null;
}

/**
 * Every entry whose rank would change, current → proposed. Entries that
 * would join or leave the list (an Undo/Redo may restore a removed entry
 * or take back an added one) are included with a null rank on the absent
 * side, so those restores are real changes rather than silent no-ops.
 */
function diffRanks(
  current: readonly MockHotEntry[],
  next: readonly MockHotEntry[],
): RankChange[] {
  const changes: RankChange[] = [];
  next.forEach((entry, index) => {
    const from = current.findIndex((h) => hotKey(h) === hotKey(entry));
    if (from !== index) {
      changes.push({
        key: hotKey(entry),
        pn: entry.pn,
        workOrder: entry.workOrder,
        entry,
        from: from === -1 ? null : from + 1,
        to: index + 1,
      });
    }
  });
  current.forEach((entry, index) => {
    if (!next.some((h) => hotKey(h) === hotKey(entry))) {
      changes.push({
        key: hotKey(entry),
        pn: entry.pn,
        workOrder: entry.workOrder,
        entry,
        from: index + 1,
        to: null,
      });
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

// Hot WO Demand ranking. All interactions are local presentation state:
// reorder / add / remove change the mock list only — the intro carries the
// single development-only note saying so. Every operation that changes the
// order of existing Hot entries (drag, Move Up/Down, Undo, Redo) requires
// explicit confirmation before it is applied; the visible list is never
// renumbered before confirmation.
export function PriorityView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useToastNotice();

  const [hotList, setHotList] = useState<MockHotEntry[]>(MOCK_HOT_LIST);
  // Undo/Redo depth is unlimited within the current application
  // session (PROJECT_PROFILE §21, decided post-v18): no numeric cap is
  // ever applied, and the histories simply end with the session.
  const [undoHistory, setUndoHistory] = useState<MockHotEntry[][]>([]);
  const [redoHistory, setRedoHistory] = useState<MockHotEntry[][]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [removeIndex, setRemoveIndex] = useState<number | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingReorder | null>(null);
  // Shared minute clock: due countdowns are derived at render from the
  // fixed due dates and keep updating while the view stays open.
  const now = useUiClock('minute');

  if (preview === 'loading') {
    return (
      <section className="pr-view" aria-label="Priority Management">
        <LoadingState label="Loading Priority Management" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="pr-view" aria-label="Priority Management">
        <ErrorState
          message="The Hot list could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  const shownList = preview === 'empty' ? [] : hotList;

  function applyChange(next: MockHotEntry[], message: string) {
    setUndoHistory((h) => [...h, hotList]);
    setRedoHistory([]);
    setHotList(next);
    showNotice(message);
  }

  /** Ask for confirmation before any reorder of existing entries. */
  function requestReorder(
    action: ReorderAction,
    next: MockHotEntry[],
    movedKey: string | null = null,
  ) {
    const changes = diffRanks(hotList, next);
    if (!changes.length) return;
    setPending({ action, current: hotList, next, changes, movedKey });
  }

  function confirmPending() {
    if (!pending) return;
    const { action, next } = pending;
    setPending(null);
    if (action === 'Undo') {
      setUndoHistory((h) => h.slice(0, -1));
      setRedoHistory((h) => [...h, hotList]);
      setHotList(next);
      showNotice('⟲ Previous Hot ranking restored');
      return;
    }
    if (action === 'Redo') {
      setRedoHistory((h) => h.slice(0, -1));
      setUndoHistory((h) => [...h, hotList]);
      setHotList(next);
      showNotice('⟳ Ranking change reapplied');
      return;
    }
    applyChange(next, '🔥 Hot ranking updated');
  }

  function undo() {
    if (!undoHistory.length) return;
    requestReorder('Undo', undoHistory[undoHistory.length - 1]);
  }

  function redo() {
    if (!redoHistory.length) return;
    requestReorder('Redo', redoHistory[redoHistory.length - 1]);
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= hotList.length) return;
    const next = [...hotList];
    [next[index], next[target]] = [next[target], next[index]];
    requestReorder(
      delta < 0 ? 'Move Up' : 'Move Down',
      next,
      hotKey(hotList[index]),
    );
  }

  function handleDrop(event: DragEvent, targetIndex: number) {
    event.preventDefault();
    if (!dragKey) return;
    const movedKey = dragKey;
    const fromIndex = hotList.findIndex((h) => hotKey(h) === movedKey);
    setDragKey(null);
    if (fromIndex < 0 || fromIndex === targetIndex) return;
    const next = [...hotList];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(targetIndex, 0, moved);
    requestReorder('Drag and drop', next, movedKey);
  }

  const candidates = MOCK_HOT_CANDIDATES.filter(
    (c) => !hotList.some((h) => hotKey(h) === hotKey(c)),
  );

  return (
    <section className="pr-view" aria-label="Priority Management">
      <div className="pr-head">
        <h1>Priority Management — Hot WO Demand</h1>
        <span className="spacer" />
        <button
          className="btn primary"
          disabled={writeBlocked}
          onClick={() => setAddOpen(true)}
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
      <DevNotice>
        Development preview — confirmed changes update sample data in this
        browser session only.
      </DevNotice>

      {shownList.length === 0 ? (
        <div className="pr-empty">
          No Hot WO Demand — add one with “+ Add to Hot list”, or scan a PN
          barcode in the add dialog.
        </div>
      ) : (
        <ol className="pr-list" style={{ listStyle: 'none' }}>
          {shownList.map((entry, index) => (
            <li
              key={hotKey(entry)}
              className={`pr-item ${dragKey === hotKey(entry) ? 'dragging' : ''}`}
              draggable={!writeBlocked}
              onDragStart={() => setDragKey(hotKey(entry))}
              onDragEnd={() => setDragKey(null)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => handleDrop(e, index)}
            >
              <span className="grip" aria-hidden="true">
                ⠿
              </span>
              <span
                className={`rank ${index < 3 ? `r${index + 1}` : ''}`}
                aria-label={`Rank ${index + 1}`}
              >
                {index + 1}
              </span>
              <span className="body">
                <span className="l1">
                  <span className="pn" title={entry.pn}>
                    {entry.pn}
                  </span>
                  <span className="wo">{entry.workOrder}</span>
                  <TypeChip type={entry.type} />
                </span>
                <span className="l2">
                  {entry.figures.map((f) => (
                    <span key={f}>{f}</span>
                  ))}
                </span>
              </span>
              <span className="due">
                <span>{formatIsoDateShort(entry.due)}</span>
                {(() => {
                  const dueInfo = dueCountdown(entry.due, now, DUE_SOON);
                  return (
                    <span
                      className={`d2 ${dueInfo.dueClass}`}
                      style={{ display: 'block' }}
                    >
                      {dueInfo.note}
                    </span>
                  );
                })()}
              </span>
              <span className="movebtns">
                <button
                  aria-label={`Move ${entry.pn} up`}
                  disabled={writeBlocked || index === 0}
                  onClick={() => move(index, -1)}
                >
                  ▲
                </button>
                <button
                  aria-label={`Move ${entry.pn} down`}
                  disabled={writeBlocked || index === shownList.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ▼
                </button>
              </span>
              <button
                className="pr-x"
                title="Remove from Hot list"
                aria-label={`Remove ${entry.pn} from Hot list`}
                disabled={writeBlocked}
                onClick={() => setRemoveIndex(index)}
              >
                ✕
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="pr-bar">
        <button
          className="btn ghost"
          disabled={writeBlocked || !undoHistory.length}
          onClick={undo}
        >
          ⟲ Undo
        </button>
        <button
          className="btn ghost"
          disabled={writeBlocked || !redoHistory.length}
          onClick={redo}
        >
          ⟳ Redo
        </button>
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
          onCancel={() => setPending(null)}
          onConfirm={confirmPending}
        />
      ) : null}

      {addOpen && (
        <HotAddDialog
          candidates={candidates}
          onCancel={() => setAddOpen(false)}
          onAdd={(candidate) => {
            setAddOpen(false);
            // Adding appends at the bottom — existing ranks are not
            // reordered, so no reorder confirmation is required.
            applyChange(
              [...hotList, candidate],
              `🔥 ${candidate.pn} · ${shortWorkOrder(candidate.workOrder)} added at the bottom — rank #${hotList.length + 1}`,
            );
          }}
        />
      )}

      {removeIndex !== null && hotList[removeIndex] && (
        <ModalDialog
          label="Remove from Hot list?"
          onClose={() => setRemoveIndex(null)}
        >
          <h3>Remove from Hot list?</h3>
          <div className="big mono">{hotList[removeIndex].pn}</div>
          <div className="sub">
            Work Order Demand{' '}
            <b className="mono">{hotList[removeIndex].workOrder}</b> will be
            removed from the Hot ranking. Remaining ranks close the gap; Undo
            can restore the entry.
          </div>
          <div className="row">
            <button
              className="bigbtn ghost"
              onClick={() => setRemoveIndex(null)}
            >
              Cancel (Esc)
            </button>
            <button
              className="bigbtn danger"
              onClick={() => {
                const removed = hotList[removeIndex];
                setRemoveIndex(null);
                applyChange(
                  hotList.filter((_, i) => i !== removeIndex),
                  `✕ ${removed.pn} · ${shortWorkOrder(removed.workOrder)} removed from Hot list — remaining ranks close the gap · Undo can restore it`,
                );
              }}
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
 * WO + Job Number metadata as one light informational chip, visually
 * separate from the PN. Built from the explicit `workOrderNumber` /
 * `jobNumber` fields — never parsed out of a display string. The full
 * demand label stays available as a tooltip.
 */
function WoJobChip({ entry }: { entry: MockHotEntry }) {
  const label = `WO ${entry.workOrderNumber ?? '—'}${
    entry.jobNumber ? ` · Job ${entry.jobNumber}` : ''
  }`;
  return (
    <span className="wjchip" title={entry.workOrder}>
      {label}
    </span>
  );
}

/** One entry line inside a Current/New Position snapshot. */
interface SnapshotRow {
  key: string;
  entry: MockHotEntry;
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
  list: readonly MockHotEntry[],
  changes: readonly RankChange[],
  movedKey: string | null,
  lo: number,
  hi: number,
  withDirections: boolean,
  currentRanks?: ReadonlyMap<string, number>,
): SnapshotRow[] {
  const rows: SnapshotRow[] = list
    .map((entry, index) => ({ entry, rank: index + 1 }))
    .filter(({ rank }) => rank >= lo && rank <= hi)
    .map(({ entry, rank }) => {
      const change = changes.find((c) => c.key === hotKey(entry));
      const direction =
        withDirections && change && change.from !== null && change.to !== null
          ? change.to < change.from
            ? ('up' as const)
            : ('down' as const)
          : undefined;
      return {
        key: hotKey(entry),
        entry,
        rank,
        fromRank: currentRanks
          ? (currentRanks.get(hotKey(entry)) ?? null)
          : undefined,
        direction,
        moved: hotKey(entry) === movedKey,
      };
    });
  for (const change of changes) {
    if (!list.some((entry) => hotKey(entry) === change.key)) {
      rows.push({
        key: change.key,
        entry: change.entry,
        rank: null,
        fromRank: currentRanks ? change.from : undefined,
        moved: change.key === movedKey,
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
            key={row.key}
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
            <span className="prpn mono" title={row.entry.pn}>
              {row.entry.pn}
            </span>
            <WoJobChip entry={row.entry} />
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
  onCancel,
  onConfirm,
}: {
  pending: PendingReorder;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const title = REORDER_TITLES[pending.action];
  const moved = pending.movedKey
    ? pending.changes.find((c) => c.key === pending.movedKey)
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
    pending.current.map((entry, index) => [hotKey(entry), index + 1]),
  );
  return (
    <ModalDialog label={title} onClose={onCancel}>
      <h3>{title}</h3>
      {moved ? (
        <div className="pr-move-summary">
          Move <span className="mono">{moved.pn}</span> ·{' '}
          <span className="mono">{shortWorkOrder(moved.workOrder)}</span> from{' '}
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
            pending.movedKey,
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
            pending.movedKey,
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
        <button className="bigbtn primary" onClick={onConfirm}>
          Apply ranking
        </button>
      </div>
    </ModalDialog>
  );
}

function HotAddDialog({
  candidates,
  onCancel,
  onAdd,
}: {
  candidates: MockHotEntry[];
  onCancel: () => void;
  onAdd: (candidate: MockHotEntry) => void;
}) {
  const [query, setQuery] = useState('');
  // PN of the last ambiguous barcode scan (several active WO Demands
  // share the scanned PN): the list is filtered to it and an explicit
  // selection is required — cleared as soon as the user edits the
  // search text.
  const [ambiguousPn, setAmbiguousPn] = useState<string | null>(null);
  const now = useUiClock('minute');
  const q = query.trim().toLowerCase();
  const list = candidates.filter(
    (c) =>
      !q ||
      `${c.pn} ${c.workOrder} ${c.barcode ?? ''}`.toLowerCase().includes(q),
  );
  return (
    <ModalDialog label="Add WO Demand to Hot list" onClose={onCancel}>
      <h3>Add WO Demand to Hot list</h3>
      <div className="sub">
        Search by PN, WO or Job Number and select — or{' '}
        <b>scan the PN barcode</b> with this dialog open.
      </div>
      <input
        className="hotsearch"
        placeholder="Search PN, WO, Job Number… or scan PN barcode"
        aria-label="Search PN, WO, Job Number or scan PN barcode"
        autoComplete="off"
        autoFocus
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setAmbiguousPn(null);
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          const value = e.currentTarget.value.trim().toUpperCase();
          // Deterministic barcode resolution (PROJECT_PROFILE §21):
          // no eligible WO Demand adds nothing; exactly one adds
          // directly; several NEVER add by guess — the list filters to
          // the PN and an explicit selection is required.
          const byBarcode = candidates.filter((c) => c.barcode === value);
          if (byBarcode.length === 1) {
            onAdd(byBarcode[0]);
            return;
          }
          if (byBarcode.length > 1) {
            setQuery(byBarcode[0].pn);
            setAmbiguousPn(byBarcode[0].pn);
            return;
          }
          if (list.length === 1) onAdd(list[0]);
        }}
      />
      {ambiguousPn ? (
        <div className="sub" role="status">
          Multiple active WO Demands use PN <b>{ambiguousPn}</b> — select the
          Work Order to add.
        </div>
      ) : (
        <div className="sub">
          If a PN has multiple active WO Demands, each is listed separately.
        </div>
      )}
      {import.meta.env.DEV ? (
        <div className="hotadd-hint">
          Demo barcodes (development build only): <code>PF:PN:78-04-0031</code>{' '}
          — Enter adds its one WO Demand directly;{' '}
          <code>PF:PN:0455-20-0118-03</code> — two active WO Demands, Enter
          filters the list for an explicit selection.
        </div>
      ) : null}
      <div className="hotaddlist">
        {list.length ? (
          list.map((c) => (
            <button
              key={hotKey(c)}
              className="hotadd-item"
              onClick={() => onAdd(c)}
            >
              <span className="hpn">{c.pn}</span>
              <span className="hwo">{c.workOrder}</span>
              <TypeChip type={c.type} />
              {(() => {
                const dueInfo = dueCountdown(c.due, now, DUE_SOON);
                return (
                  <span
                    className={`hdue ${dueInfo.dueClass === 'late' ? 'late' : ''}`}
                  >
                    {c.due
                      ? `${formatIsoDateShort(c.due)} · ${dueInfo.note}`
                      : dueInfo.note}
                  </span>
                );
              })()}
            </button>
          ))
        ) : (
          <div className="hotadd-empty">
            No matching active WO Demand
            {q ? ` for “${query.trim()}”` : ' — everything is already Hot'}
          </div>
        )}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
      </div>
    </ModalDialog>
  );
}
