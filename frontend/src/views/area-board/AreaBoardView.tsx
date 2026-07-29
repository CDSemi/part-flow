import './area-board.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import {
  AreaMachineLayout,
  AreaSummaryCard,
  MachineMonitoringCard,
} from '../../components/area-monitoring';
import { AreaDot, HotPn } from '../../components/indicators';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  MOCK_AREA_CARDS,
  MOCK_AREA_CARDS_LONG,
  MOCK_AREA_MACHINES,
} from '../../mocks/area-board';
import { MOCK_AREAS } from '../../mocks/areas';
import { isQueueContext, splitAssignments } from '../area-monitoring';
import { compareDemandOrder } from '../demand-order';
import type { MockArea, MockAreaCard } from '../view-models';

type SortKey = 'due' | 'prio' | 'tia' | 'qty';

function sortCards(cards: MockAreaCard[], sort: SortKey): MockAreaCard[] {
  const keyed = cards.map((card, seq) => ({ card, seq }));
  keyed.sort((a, b) => {
    switch (sort) {
      case 'qty':
        return b.card.qty - a.card.qty || a.seq - b.seq;
      case 'prio':
        return (a.card.hotRank ?? 9) - (b.card.hotRank ?? 9) || a.seq - b.seq;
      case 'tia':
        // Longest time in Area first — the most-waiting work surfaces.
        return (
          b.card.timeInAreaMinutes - a.card.timeInAreaMinutes || a.seq - b.seq
        );
      default:
        // Canonical demand order: Hot rank → earliest due date → undated
        // demands after all dated ones, by WO received date → stable.
        return compareDemandOrder(
          {
            hotRank: a.card.hotRank,
            due: dueIso(a.card),
            received: a.card.received,
            seq: a.seq,
          },
          {
            hotRank: b.card.hotRank,
            due: dueIso(b.card),
            received: b.card.received,
            seq: b.seq,
          },
        );
    }
  });
  return keyed.map((entry) => entry.card);
}

// The mock cards carry relative day counts instead of ISO dates; an
// artificial-but-ordered ISO key keeps the shared comparator applicable.
function dueIso(card: MockAreaCard): string | null {
  if (card.dueDays === null) return null;
  return `D${String(card.dueDays + 1000).padStart(5, '0')}`;
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
          detail="Check the backend connection, then retry from the offline banner."
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
  onOpenArea,
}: {
  cards: MockAreaCard[];
  onOpenArea: (key: string) => void;
}) {
  return (
    <div className="ms-scroll">
      {MOCK_AREAS.map((area) => {
        const areaCards = cards.filter((c) => c.area === area.key);
        const hasMachines = (MOCK_AREA_MACHINES[area.key] ?? []).length > 0;
        const total = areaCards.reduce((s, c) => s + c.qty, 0);
        const queued = areaCards.reduce(
          (s, c) =>
            s +
            c.machines
              .filter(([m]) => isQueueContext(m))
              .reduce((x, [, q]) => x + q, 0),
          0,
        );
        const onMachines = total - queued;
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
                    <div className="n">{areaCards.length}</div>
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
                </>
              ) : (
                // No Machines: no meaningless queue/Machine zeros.
                <>
                  <div className="stat">
                    <div className="n">{total}</div>
                    <div className="l">Total pcs</div>
                  </div>
                  <div className="stat">
                    <div className="n m">{total > 0 ? total : '—'}</div>
                    <div className="l">Processing</div>
                  </div>
                </>
              )}
            </div>
            {areaCards.length ? (
              <ul className="mc-list">
                {areaCards.map((c) => (
                  <li key={`${c.pn}-${c.workOrder}`}>
                    <div className="r1">
                      <HotPn rank={c.hotRank} pn={c.pn} pnClassName="p" />
                      <span className="q">{c.qty}</span>
                    </div>
                    <div className="r2">
                      <span className="mono">
                        {c.workOrder.split(' ·')[0]} · {c.job}
                      </span>
                      <span
                        className={`mono ${c.dueClass === 'ok' ? '' : c.dueClass}`}
                      >
                        {c.due}
                      </span>
                      {c.machines.length ? (
                        <span className="mono">
                          {c.machines
                            .map(([m, q]) => `${m} × ${q}`)
                            .join(' · ')}
                        </span>
                      ) : null}
                      {c.timeInArea !== '—' ? (
                        <span className="mono">{c.timeInArea} in Area</span>
                      ) : null}
                      {c.scrapped ? (
                        <span className="scraptxt">{c.scrapped} scrapped</span>
                      ) : null}
                    </div>
                  </li>
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
