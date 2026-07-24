import './area-board.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { AreaDot, HotChip } from '../../components/indicators';
import { ErrorState, LoadingState } from '../../components/view-states';
import { MOCK_AREA_CARDS, MOCK_AREA_CARDS_LONG } from '../../mocks/area-board';
import { MOCK_AREAS } from '../../mocks/areas';
import type { MockArea, MockAreaCard } from '../../mocks/types';

type SortKey = 'due' | 'prio' | 'tia' | 'qty';

function sortCards(cards: MockAreaCard[], sort: SortKey): MockAreaCard[] {
  return [...cards].sort((a, b) => {
    switch (sort) {
      case 'qty':
        return b.qty - a.qty;
      case 'prio':
        return (a.hotRank ?? 9) - (b.hotRank ?? 9);
      case 'tia':
        return 0;
      default:
        return a.dueDays - b.dueDays;
    }
  });
}

function matches(card: MockAreaCard, query: string): boolean {
  if (!query) return true;
  return (card.pn + card.po + card.job).toLowerCase().includes(query);
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
          placeholder="Search PN, PO, Job Number…"
          aria-label="Search PN, PO, Job Number"
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
        const total = areaCards.reduce((s, c) => s + c.qty, 0);
        const queued = areaCards.reduce(
          (s, c) =>
            s +
            c.machines
              .filter(([m]) => m === 'queue' || m === 'vendor')
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
                    <div className="n q">—</div>
                    <div className="l">Queued</div>
                  </div>
                  <div className="stat">
                    <div className="n m">—</div>
                    <div className="l">On machines</div>
                  </div>
                </>
              ) : (
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
              )}
            </div>
            {areaCards.length ? (
              <ul className="mc-list">
                {areaCards.map((c) => (
                  <li key={`${c.pn}-${c.po}`}>
                    <div className="r1">
                      {c.hotRank ? <HotChip rank={c.hotRank} /> : null}
                      <span className="p" title={c.pn}>
                        {c.pn}
                      </span>
                      <span className="q">{c.qty}</span>
                    </div>
                    <div className="r2">
                      <span className="mono">
                        {c.po.split(' ·')[0]} · {c.job}
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

function AreaDetail({
  area,
  cards,
}: {
  area: MockArea | undefined;
  cards: MockAreaCard[];
}) {
  if (!area) return null;
  if (!cards.length) {
    return (
      <div className="ab-grid">
        <div className="ab-cardempty">No production in {area.name}</div>
      </div>
    );
  }
  return (
    <div className="ab-grid">
      {cards.map((c) => (
        <div className="ab-card" key={`${c.pn}-${c.po}`}>
          <div className="top">
            <div className="id">
              <div className="part" title={c.pn}>
                {c.hotRank ? (
                  <>
                    <HotChip rank={c.hotRank} />{' '}
                  </>
                ) : null}
                {c.pn}
              </div>
              <div className="po">{c.po}</div>
              <div className="ext">Job: {c.job}</div>
            </div>
            <div className="qtyblk">
              <div className="qlbl">In this Area</div>
              <div className="qty-big">{c.qty}</div>
              <div className="qunit">pcs</div>
            </div>
          </div>
          {c.machines.length ? (
            <div className="machines">
              {c.machines.map(([m, q]) => (
                <span
                  key={`${m}-${q}`}
                  className={`mrow ${m === 'queue' ? 'queue' : ''}`}
                >
                  {m} · <b>{q}</b>
                </span>
              ))}
            </div>
          ) : null}
          <div className="foot">
            <span className={`duetxt ${c.dueClass}`}>{c.due}</span>
            <span className="tia">
              {c.timeInArea === '—' ? '' : `${c.timeInArea} in Area`}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
