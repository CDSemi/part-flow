import './priority.css';

import { useState } from 'react';
import type { DragEvent } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { TypeChip } from '../../components/indicators';
import { useMockNotice } from '../../components/mock-notice';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { MOCK_HOT_CANDIDATES, MOCK_HOT_LIST } from '../../mocks/priority';
import { formatIsoDateShort } from '../dates';
import type { MockHotEntry } from '../view-models';

const hotKey = (h: MockHotEntry) => `${h.pn}|${h.workOrder}`;

type ReorderAction =
  'Drag and drop' | 'Move Up' | 'Move Down' | 'Undo' | 'Redo';

interface RankChange {
  pn: string;
  workOrder: string;
  from: number;
  to: number;
}

interface PendingReorder {
  action: ReorderAction;
  next: MockHotEntry[];
  changes: RankChange[];
}

/** Every entry whose rank would change, previous → proposed. */
function diffRanks(
  current: readonly MockHotEntry[],
  next: readonly MockHotEntry[],
): RankChange[] {
  const changes: RankChange[] = [];
  next.forEach((entry, index) => {
    const from = current.findIndex((h) => hotKey(h) === hotKey(entry));
    if (from !== -1 && from !== index) {
      changes.push({
        pn: entry.pn,
        workOrder: entry.workOrder,
        from: from + 1,
        to: index + 1,
      });
    }
  });
  return changes;
}

// Hot WO Demand ranking. All interactions are local presentation state:
// reorder / add / remove change the mock list only and are labeled as
// development mocks — real prioritization persistence is a later phase.
// Every operation that changes the order of existing Hot entries (drag,
// Move Up/Down, Undo, Redo) requires explicit confirmation before it is
// applied; the visible list is never renumbered before confirmation.
export function PriorityView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useMockNotice();

  const [hotList, setHotList] = useState<MockHotEntry[]>(MOCK_HOT_LIST);
  const [undoHistory, setUndoHistory] = useState<MockHotEntry[][]>([]);
  const [redoHistory, setRedoHistory] = useState<MockHotEntry[][]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [removeIndex, setRemoveIndex] = useState<number | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingReorder | null>(null);

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
          detail="Check the backend connection, then retry from the offline banner."
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
  function requestReorder(action: ReorderAction, next: MockHotEntry[]) {
    const changes = diffRanks(hotList, next);
    if (!changes.length) return;
    setPending({ action, next, changes });
  }

  function confirmPending() {
    if (!pending) return;
    const { action, next } = pending;
    setPending(null);
    if (action === 'Undo') {
      setUndoHistory((h) => h.slice(0, -1));
      setRedoHistory((h) => [...h, hotList]);
      setHotList(next);
      showNotice(
        '⟲ Undo — previous Hot ranking restored (mock, audited in the real system)',
      );
      return;
    }
    if (action === 'Redo') {
      setRedoHistory((h) => h.slice(0, -1));
      setUndoHistory((h) => [...h, hotList]);
      setHotList(next);
      showNotice('⟳ Redo — change re-applied (mock)');
      return;
    }
    applyChange(next, '🔥 Hot ranking updated (mock) — presentation only');
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
    requestReorder(delta < 0 ? 'Move Up' : 'Move Down', next);
  }

  function handleDrop(event: DragEvent, targetIndex: number) {
    event.preventDefault();
    if (!dragKey) return;
    const fromIndex = hotList.findIndex((h) => hotKey(h) === dragKey);
    setDragKey(null);
    if (fromIndex < 0 || fromIndex === targetIndex) return;
    const next = [...hotList];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(targetIndex, 0, moved);
    requestReorder('Drag and drop', next);
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
        entries asks for confirmation before it is applied. ✕ removes (with
        confirmation). In the real application confirmed changes are audited; in
        Phase 2 they change local mock state only. New Hot entries are added at
        the bottom. Multiple Work Orders for the same PN may hold different
        priorities.
      </p>

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
                <span
                  className={`d2 ${entry.dueClass}`}
                  style={{ display: 'block' }}
                >
                  {entry.dueNote}
                </span>
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

      <div className="pr-note">
        <b>Hot Part</b> is a label on top of{' '}
        <span className="mono">priority_rank</span> — it never replaces it.
        Allocation &amp; work ordering: ① Hot rank ② demands with a due date,
        earliest first ③ demands without a due date, by the Work Order received
        date (oldest first) — a stable deterministic tie-breaker resolves equal
        values.
      </div>

      {pending ? (
        <ModalDialog
          label="Confirm Hot ranking change"
          onClose={() => setPending(null)}
        >
          <h3>Confirm Hot ranking change</h3>
          <div className="sub">
            <b>{pending.action}</b> — the following Work Order Demand rank
            {pending.changes.length === 1 ? ' changes' : 's change'} when
            applied. Nothing has changed yet.
          </div>
          <ul className="rankchanges">
            {pending.changes.map((change) => (
              <li key={`${change.pn}-${change.workOrder}`}>
                <span className="mono">{change.pn}</span> ·{' '}
                <span className="mono">{change.workOrder}</span> — rank{' '}
                <b>#{change.from}</b> → <b>#{change.to}</b>
              </li>
            ))}
          </ul>
          <div className="row">
            <button className="bigbtn ghost" onClick={() => setPending(null)}>
              Cancel — nothing changes
            </button>
            <button className="bigbtn primary" onClick={confirmPending}>
              Apply new ranking
            </button>
          </div>
        </ModalDialog>
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
              `🔥 ${candidate.pn} · ${candidate.workOrder.split(' ·')[0]} added at the bottom (mock) — rank #${hotList.length + 1}`,
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
            removed from the Hot ranking. Remaining ranks close the gap. In the
            real application the confirmed change is audited and can be restored
            with Undo — here it changes mock state only.
          </div>
          <div className="row">
            <button
              className="bigbtn ghost"
              onClick={() => setRemoveIndex(null)}
            >
              Cancel — nothing changes
            </button>
            <button
              className="bigbtn danger"
              onClick={() => {
                const removed = hotList[removeIndex];
                setRemoveIndex(null);
                applyChange(
                  hotList.filter((_, i) => i !== removeIndex),
                  `✕ ${removed.pn} · ${removed.workOrder.split(' ·')[0]} removed from Hot list (mock) — remaining ranks close the gap · Undo can restore it`,
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
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          const value = e.currentTarget.value.trim().toUpperCase();
          const byBarcode = candidates.find((c) => c.barcode === value);
          if (byBarcode) {
            onAdd(byBarcode);
            return;
          }
          if (list.length === 1) onAdd(list[0]);
        }}
      />
      <div className="hotadd-hint">
        Demo barcode: <code>PF:PN:1014</code> (0455-20-0118-03) — Enter adds it
        directly. If a PN has multiple active WO Demands, each is listed
        separately.
      </div>
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
              <span className={`hdue ${c.dueClass === 'late' ? 'late' : ''}`}>
                {c.due
                  ? `${formatIsoDateShort(c.due)} · ${c.dueNote}`
                  : c.dueNote}
              </span>
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
