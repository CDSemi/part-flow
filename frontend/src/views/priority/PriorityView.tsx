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
import type { MockHotEntry } from '../view-models';

const hotKey = (h: MockHotEntry) => `${h.pn}|${h.po}`;

// Hot PO Demand ranking. All interactions are local presentation state:
// reorder / add / remove change the mock list only and are labeled as
// development mocks — real prioritization persistence is a later phase.
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

  function undo() {
    if (!undoHistory.length) return;
    const previous = undoHistory[undoHistory.length - 1];
    setUndoHistory((h) => h.slice(0, -1));
    setRedoHistory((h) => [...h, hotList]);
    setHotList(previous);
    showNotice(
      '⟲ Undo — previous Hot ranking restored (mock, audited in the real system)',
    );
  }

  function redo() {
    if (!redoHistory.length) return;
    const next = redoHistory[redoHistory.length - 1];
    setRedoHistory((h) => h.slice(0, -1));
    setUndoHistory((h) => [...h, hotList]);
    setHotList(next);
    showNotice('⟳ Redo — change re-applied (mock)');
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= hotList.length) return;
    const next = [...hotList];
    [next[index], next[target]] = [next[target], next[index]];
    applyChange(next, '🔥 Hot ranking updated (mock) — presentation only');
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
    applyChange(next, '🔥 Hot ranking updated (mock) — presentation only');
  }

  const candidates = MOCK_HOT_CANDIDATES.filter(
    (c) => !hotList.some((h) => hotKey(h) === hotKey(c)),
  );

  return (
    <section className="pr-view" aria-label="Priority Management">
      <div className="pr-head">
        <h1>Priority Management — Hot PO Demand</h1>
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
        Priority belongs to <b>PO Demand</b>, ranked per Department. Drag (or
        use the arrow buttons) to reorder, ✕ to remove — in the real application
        changes apply immediately and are audited; in Phase 2 they change local
        mock state only. New Hot entries are added at the bottom. Multiple POs
        for the same PN may hold different priorities.
      </p>

      {shownList.length === 0 ? (
        <div className="pr-empty">
          No Hot PO Demand — add one with “+ Add to Hot list”, or scan a PN
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
                  <span className="po">{entry.po}</span>
                  <TypeChip type={entry.type} />
                </span>
                <span className="l2">
                  {entry.figures.map((f) => (
                    <span key={f}>{f}</span>
                  ))}
                </span>
              </span>
              <span className="due">
                <span>{entry.due}</span>
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
        Allocation &amp; work ordering: ① Hot rank ② earliest due date — a
        stable deterministic tie-breaker is an implementation detail, not a
        business rule.
      </div>

      {addOpen && (
        <HotAddDialog
          candidates={candidates}
          onCancel={() => setAddOpen(false)}
          onAdd={(candidate) => {
            setAddOpen(false);
            applyChange(
              [...hotList, candidate],
              `🔥 ${candidate.pn} · ${candidate.po.split(' ·')[0]} added at the bottom (mock) — rank #${hotList.length + 1}`,
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
            PO Demand <b className="mono">{hotList[removeIndex].po}</b> will be
            removed from the Hot ranking. Remaining ranks close the gap. In the
            real application the change applies <b>immediately</b>, is audited,
            and can be restored with Undo — here it changes mock state only.
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
                  `✕ ${removed.pn} · ${removed.po.split(' ·')[0]} removed from Hot list (mock) — remaining ranks close the gap · Undo can restore it`,
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
    (c) => !q || `${c.pn} ${c.po} ${c.barcode ?? ''}`.toLowerCase().includes(q),
  );
  return (
    <ModalDialog label="Add PO Demand to Hot list" onClose={onCancel}>
      <h3>Add PO Demand to Hot list</h3>
      <div className="sub">
        Search by PN, PO or Job Number and select — or{' '}
        <b>scan the PN barcode</b> with this dialog open.
      </div>
      <input
        className="hotsearch"
        placeholder="Search PN, PO, Job Number… or scan PN barcode"
        aria-label="Search PN, PO, Job Number or scan PN barcode"
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
        Demo barcode: <code>PF:PN:1014</code> (PF-SHAFT-014) — Enter adds it
        directly. If a PN has multiple active PO Demands, each is listed
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
              <span className="hpo">{c.po}</span>
              <TypeChip type={c.type} />
              <span className={`hdue ${c.dueClass === 'late' ? 'late' : ''}`}>
                {c.due} · {c.dueNote}
              </span>
            </button>
          ))
        ) : (
          <div className="hotadd-empty">
            No matching active PO Demand
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
