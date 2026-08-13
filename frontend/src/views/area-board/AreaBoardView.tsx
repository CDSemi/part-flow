import './area-board.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import {
  AreaMachineLayout,
  AreaOverviewRow,
  AreaSummaryCard,
  MachineMonitoringCard,
} from '../../components/area-monitoring';
import { AreaDot } from '../../components/indicators';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  MOCK_AREA_CARDS,
  MOCK_AREA_CARDS_LONG,
  MOCK_AREA_MACHINES,
} from '../../mocks/area-board';
import { MOCK_AREAS } from '../../mocks/areas';
import { useUiClock } from '../../components/ui-clock';
import { splitAssignments } from '../area-monitoring';
import { elapsedMinutesSince } from '../dates';
import { compareDemandOrder } from '../demand-order';
import type { MockArea, MockAreaCard } from '../view-models';

type SortKey = 'due' | 'prio' | 'tia' | 'qty';

function sortCards(
  cards: MockAreaCard[],
  sort: SortKey,
  now: number,
): MockAreaCard[] {
  const keyed = cards.map((card, seq) => ({ card, seq }));
  keyed.sort((a, b) => {
    switch (sort) {
      case 'qty':
        return b.card.qty - a.card.qty || a.seq - b.seq;
      case 'prio':
        return (a.card.hotRank ?? 9) - (b.card.hotRank ?? 9) || a.seq - b.seq;
      case 'tia':
        // Longest time in Area first — the most-waiting work surfaces.
        // Derived from the fixed Area-entry timestamps and the shared
        // UI clock (a card without one — Stockroom — sorts last).
        return (
          tiaMinutes(b.card, now) - tiaMinutes(a.card, now) || a.seq - b.seq
        );
      default:
        // Canonical demand order: Hot rank → earliest due date → undated
        // demands after all dated ones, by WO received date → stable.
        return compareDemandOrder(
          {
            hotRank: a.card.hotRank,
            due: a.card.due,
            received: a.card.received,
            seq: a.seq,
          },
          {
            hotRank: b.card.hotRank,
            due: b.card.due,
            received: b.card.received,
            seq: b.seq,
          },
        );
    }
  });
  return keyed.map((entry) => entry.card);
}

function tiaMinutes(card: MockAreaCard, now: number): number {
  return card.enteredAreaAt ? elapsedMinutesSince(card.enteredAreaAt, now) : -1;
}

function matches(card: MockAreaCard, query: string): boolean {
  if (!query) return true;
  return (card.pn + card.workOrder + card.job).toLowerCase().includes(query);
}

// One view, two modes behind a single tab strip: the All Areas overview
// (the §21 Manager Summary content — no separate route) and the
// per-Area detail. Tab selection is presentation state within the view.
export function AreaBoardView() {
  const preview = getViewStatePreview();
  const [activeTab, setActiveTab] = useState<'all' | string>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('due');
  // All Areas layout choice: horizontal scroll (default, GUI §6.2) or
  // wrapping columns onto additional rows within the page width.
  // Presentation state within the view, like the active tab.
  const [wrapOverview, setWrapOverview] = useState(false);
  // Shared minute clock: `Time in Area` sorting and every derived time
  // display stay current (and identical across views) while the board
  // stays open.
  const now = useUiClock('minute');

  if (preview === 'loading') {
    return (
      <section className="ab" aria-label="Area Board">
        <LoadingState label="Loading Area Board" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="ab" aria-label="Area Board">
        <ErrorState
          message="Area Board data could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  const allCards =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? MOCK_AREA_CARDS_LONG
        : MOCK_AREA_CARDS;
  const query = search.trim().toLowerCase();
  const visible = sortCards(
    allCards.filter((c) => matches(c, query)),
    sort,
    now,
  );

  return (
    <section className="ab" aria-label="Area Board">
      <div className="ab-tabs">
        <button
          className={`ab-tab all ${activeTab === 'all' ? 'active' : ''}`}
          aria-pressed={activeTab === 'all'}
          onClick={() => setActiveTab('all')}
        >
          All Areas <span className="cnt">{allCards.length}</span>
        </button>
        {MOCK_AREAS.map((area) => (
          <button
            key={area.key}
            className={`ab-tab ${activeTab === area.key ? 'active' : ''}`}
            aria-pressed={activeTab === area.key}
            onClick={() => setActiveTab(area.key)}
          >
            <AreaDot colorVar={area.colorVar} />
            {area.name}{' '}
            <span className="cnt">
              {allCards.filter((c) => c.area === area.key).length}
            </span>
          </button>
        ))}
      </div>

      <div className="ab-tools">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search PN, WO, Job Number…"
          aria-label="Search PN, WO, Job Number"
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="Sort"
        >
          <option value="due">Sort: Due date</option>
          <option value="prio">Sort: Priority</option>
          <option value="tia">Sort: Time in Area</option>
          <option value="qty">Sort: Quantity</option>
        </select>
        {activeTab === 'all' ? (
          <button
            type="button"
            role="switch"
            aria-checked={wrapOverview}
            className={`ab-wrap ${wrapOverview ? 'on' : ''}`}
            onClick={() => setWrapOverview((w) => !w)}
            title="Wrap Area columns onto additional rows instead of scrolling horizontally"
          >
            <span className="knob" aria-hidden="true" />
            Wrap columns
          </button>
        ) : null}
        <span className="ab-meta">
          {activeTab === 'all' ? (
            <>
              <b>{visible.length}</b> PN ·{' '}
              <b>{visible.reduce((s, c) => s + c.qty, 0)}</b> pcs across all
              Areas
            </>
          ) : (
            <>
              <b>{visible.filter((c) => c.area === activeTab).length}</b> PN ·{' '}
              <b>
                {visible
                  .filter((c) => c.area === activeTab)
                  .reduce((s, c) => s + c.qty, 0)}
              </b>{' '}
              pcs in {MOCK_AREAS.find((a) => a.key === activeTab)?.name}
            </>
          )}
        </span>
      </div>

      {activeTab === 'all' ? (
        <AllAreasOverview
          cards={visible}
          wrap={wrapOverview}
          onOpenArea={(key) => setActiveTab(key)}
        />
      ) : (
        <AreaDetail
          area={MOCK_AREAS.find((a) => a.key === activeTab)}
          cards={visible.filter((c) => c.area === activeTab)}
        />
      )}
    </section>
  );
}

function AllAreasOverview({
  cards,
  wrap,
  onOpenArea,
}: {
  cards: MockAreaCard[];
  wrap: boolean;
  onOpenArea: (key: string) => void;
}) {
  return (
    <div className={`ms-scroll ${wrap ? 'wrap' : ''}`}>
      {MOCK_AREAS.map((area) => {
        const areaCards = cards.filter((c) => c.area === area.key);
        const hasMachines = (MOCK_AREA_MACHINES[area.key] ?? []).length > 0;
        const total = areaCards.reduce((s, c) => s + c.qty, 0);
        // The shared grouping keeps queued / on-Machine / finished
        // portions distinguishable (finished = READY_TO_TRANSFER).
        const split = splitAssignments(areaCards);
        const queued = split.queued.reduce((s, e) => s + e.qty, 0);
        const onMachines = split.assigned.reduce((s, e) => s + e.qty, 0);
        const finished = split.finished.reduce((s, e) => s + e.qty, 0);
        return (
          <div
            className="ms-col"
            key={area.key}
            style={{ ['--acol' as string]: area.colorVar }}
          >
            <button
              className="mc-head"
              onClick={() => onOpenArea(area.key)}
              title={`Open ${area.name} detail`}
            >
              <span className="mc-title">
                <AreaDot colorVar={area.colorVar} size={12} />
                {area.name}
                <span className="go">detail ›</span>
              </span>
              <span className="mc-desc" style={{ display: 'block' }}>
                {area.description}
              </span>
              <span className="mc-ops">
                {area.operations.map((op) => (
                  <span className="opchip" key={op}>
                    {op}
                  </span>
                ))}
              </span>
            </button>
            <div className="mc-stats">
              {area.terminal ? (
                <>
                  <div className="stat">
                    <div className="n">{total}</div>
                    <div className="l">Stocked pcs</div>
                  </div>
                  <div className="stat">
                    <div className="n pn">{areaCards.length}</div>
                    <div className="l">PNs</div>
                  </div>
                </>
              ) : hasMachines ? (
                <>
                  <div className="stat">
                    <div className="n">{total}</div>
                    <div className="l">Total pcs</div>
                  </div>
                  <div className="stat">
                    <div className="n q">{queued}</div>
                    <div className="l">Queued</div>
                  </div>
                  <div className="stat">
                    <div className="n m">
                      {onMachines > 0 ? onMachines : '—'}
                    </div>
                    <div className="l">On machines</div>
                  </div>
                  {finished > 0 ? (
                    <div className="stat">
                      <div className="n d">{finished}</div>
                      <div className="l">Done</div>
                    </div>
                  ) : null}
                </>
              ) : (
                // No Machines: no meaningless queue/Machine zeros.
                <>
                  <div className="stat">
                    <div className="n">{total}</div>
                    <div className="l">Total pcs</div>
                  </div>
                  <div className="stat">
                    <div className="n m">
                      {total - finished > 0 ? total - finished : '—'}
                    </div>
                    <div className="l">Processing</div>
                  </div>
                  {finished > 0 ? (
                    <div className="stat">
                      <div className="n d">{finished}</div>
                      <div className="l">Done</div>
                    </div>
                  ) : null}
                </>
              )}
            </div>
            {areaCards.length ? (
              <ul className="mc-list">
                {areaCards.map((c) => (
                  <AreaOverviewRow key={`${c.pn}-${c.workOrder}`} card={c} />
                ))}
              </ul>
            ) : (
              <div className="empty">No production in {area.name}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Per-Area detail — the shared Area/Machine monitoring layout:
 * `[ In this Area now | Machine cards grid ]`. Area Board stays
 * read-only: no Scan Station action buttons are passed to the shared
 * components. Areas without Machines render only the full-width Area
 * summary card.
 */
function AreaDetail({
  area,
  cards,
}: {
  area: MockArea | undefined;
  cards: MockAreaCard[];
}) {
  if (!area) return null;
  const machines = MOCK_AREA_MACHINES[area.key] ?? [];
  const { assigned } = splitAssignments(cards);

  return (
    <AreaMachineLayout
      summary={
        <AreaSummaryCard
          area={area}
          cards={cards}
          machines={machines}
          title={`In this Area now — ${area.name}`}
        />
      }
      machineCards={machines.map((machine) => (
        <MachineMonitoringCard
          key={machine.name}
          machine={machine}
          entries={assigned.filter((e) => e.context === machine.name)}
        />
      ))}
    />
  );
}
