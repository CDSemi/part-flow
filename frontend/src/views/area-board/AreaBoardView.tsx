import './area-board.css';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

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

/**
 * Phone/tablet breakpoint of the narrow Area Board presentation — the
 * same §2.5 collapse point as the stylesheet's narrow rules
 * (area-board.css `@media (max-width: 720px)`; keep the two in sync).
 */
const NARROW_BOARD_MAX_WIDTH_PX = 720;

// One view, two modes behind a single tab strip on wide viewports: the
// All Areas overview (the §21 Manager Summary content — no separate
// route) and the per-Area detail. Tab selection is presentation state
// within the view. Narrow viewports (post-v18) hide the tab strip
// entirely: a `Summary` toggle (default OFF) switches between the
// swipeable per-Area detail pages and the stacked All Areas overview.
export function AreaBoardView() {
  const preview = getViewStatePreview();
  const [activeTab, setActiveTab] = useState<'all' | string>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('due');
  // All Areas layout choice on WIDE viewports: horizontal scroll
  // (default, GUI §6.2) or wrapping columns onto additional rows.
  // Presentation state within the view, like the active tab.
  const [wrapOverview, setWrapOverview] = useState(false);
  // Narrow presentation state (post-v18): `summary` OFF (default)
  // pages the per-Area details; ON stacks the All Areas overview.
  // `detailPage` remembers the active detail page across the toggle.
  const [narrow, setNarrow] = useState(false);
  const [summary, setSummary] = useState(false);
  const [detailPage, setDetailPage] = useState(0);
  // Shared minute clock: `Time in Area` sorting and every derived time
  // display stay current (and identical across views) while the board
  // stays open.
  const now = useUiClock('minute');

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia(
      `(max-width: ${NARROW_BOARD_MAX_WIDTH_PX}px)`,
    );
    const apply = () => setNarrow(query.matches);
    apply();
    query.addEventListener('change', apply);
    return () => query.removeEventListener('change', apply);
  }, []);

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

  const safeDetailPage = Math.min(detailPage, MOCK_AREAS.length - 1);
  const pageArea = MOCK_AREAS[safeDetailPage];
  const metaFor = (areaKey: string | null) =>
    areaKey === null ? (
      <>
        <b>{visible.length}</b> PN ·{' '}
        <b>{visible.reduce((s, c) => s + c.qty, 0)}</b> pcs across all Areas
      </>
    ) : (
      <>
        <b>{visible.filter((c) => c.area === areaKey).length}</b> PN ·{' '}
        <b>
          {visible
            .filter((c) => c.area === areaKey)
            .reduce((s, c) => s + c.qty, 0)}
        </b>{' '}
        pcs in {MOCK_AREAS.find((a) => a.key === areaKey)?.name}
      </>
    );

  return (
    <section className="ab" aria-label="Area Board">
      {/* Narrow viewports render NO tab strip — the detail pages (and
          the Summary overview's card headers) are the navigation. */}
      {!narrow ? (
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
      ) : null}

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
        {narrow ? (
          // Narrow layout choice (post-v18): OFF (default) pages the
          // per-Area details, ON stacks the All Areas overview — the
          // same slide-toggle presentation as Wrap columns.
          <button
            type="button"
            role="switch"
            aria-checked={summary}
            className={`ab-wrap ${summary ? 'on' : ''}`}
            onClick={() => setSummary((s) => !s)}
            title="Show the stacked All Areas summary instead of the per-Area pages"
          >
            <span className="knob" aria-hidden="true" />
            Summary
          </button>
        ) : activeTab === 'all' ? (
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
          {narrow
            ? summary
              ? metaFor(null)
              : metaFor(pageArea.key)
            : activeTab === 'all'
              ? metaFor(null)
              : metaFor(activeTab)}
        </span>
      </div>

      {narrow ? (
        summary ? (
          <AllAreasOverview
            cards={visible}
            wrap
            onOpenArea={(key) => {
              // A summary card header jumps straight to that Area's
              // detail page (the Summary toggle switches off).
              setSummary(false);
              setDetailPage(
                Math.max(
                  0,
                  MOCK_AREAS.findIndex((a) => a.key === key),
                ),
              );
            }}
          />
        ) : (
          <AreaDetailPager
            cards={visible}
            page={safeDetailPage}
            onPageChange={setDetailPage}
          />
        )
      ) : activeTab === 'all' ? (
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

/**
 * Narrow paged Area details (post-v18): ONE per-Area detail view per
 * page — the same shared Area/Machine monitoring layout as the wide
 * detail tabs — in a swipeable snap carousel: native horizontal
 * scrolling with mandatory snap (each swipe lands centered on one
 * Area's detail; the neighboring pages peek in at the edges as the
 * built-in cue that more pages exist), Area-colored page dots above
 * the pages, and floating ‹ › edge buttons. The active page derives
 * from the scroll position, so swipe, momentum, snap, dots and
 * buttons can never disagree; paging never wraps.
 */
function AreaDetailPager({
  cards,
  page,
  onPageChange,
}: {
  cards: MockAreaCard[];
  page: number;
  onPageChange: (page: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageCount = MOCK_AREAS.length;

  const scrollToPage = useCallback((target: number, smooth: boolean) => {
    const el = scrollRef.current;
    const child = el?.children[target] as HTMLElement | undefined;
    if (!el || !child) return;
    const left = child.offsetLeft - (el.clientWidth - child.offsetWidth) / 2;
    // Element scrolling is unavailable in DOM environments without
    // layout — the page state still drives the indicator.
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ left, behavior: smooth ? 'smooth' : 'auto' });
    }
  }, []);

  // Entering the pager (mount, or a Summary-card jump that remounts
  // it) starts on the requested page without animation.
  const initialPage = useRef(page);
  useLayoutEffect(() => {
    scrollToPage(initialPage.current, false);
  }, [scrollToPage]);

  const goTo = useCallback(
    (target: number) => {
      if (target < 0 || target >= pageCount) return;
      scrollToPage(target, true);
      onPageChange(target);
    },
    [pageCount, scrollToPage, onPageChange],
  );

  // The active page derives from the scroll position, so a native
  // swipe, momentum scrolling and the snap all update the indicator
  // through the same path as the buttons.
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const center = el.scrollLeft + el.clientWidth / 2;
    let best = 0;
    let bestDistance = Infinity;
    Array.from(el.children).forEach((child, index) => {
      const col = child as HTMLElement;
      const distance = Math.abs(col.offsetLeft + col.offsetWidth / 2 - center);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    });
    if (best !== page) onPageChange(best);
  }, [page, onPageChange]);

  return (
    <div className="ms-board paged">
      {/* Area-colored page dots: position indicator AND direct jump
          targets — the stable Area identity colors (§2.2) mirror the
          page accents, so the dot row reads as a map of the pages. */}
      <div className="ms-pagedots" aria-label="Area pages">
        {MOCK_AREAS.map((area, index) => (
          <button
            key={area.key}
            type="button"
            className={`ms-pagedot${index === page ? ' on' : ''}`}
            style={{ ['--acol' as string]: area.colorVar }}
            aria-label={`Go to ${area.name}`}
            aria-current={index === page ? 'true' : undefined}
            onClick={() => goTo(index)}
          />
        ))}
      </div>
      <div className="abd-scroll" ref={scrollRef} onScroll={onScroll}>
        {MOCK_AREAS.map((area) => (
          <div className="abd-page" key={area.key}>
            <AreaDetail
              area={area}
              cards={cards.filter((c) => c.area === area.key)}
            />
          </div>
        ))}
      </div>
      {/* Floating ‹ › edge buttons: the always-visible affordance that
          the pages continue — pinned mid-viewport so they stay in
          reach while a tall page scrolls vertically. Disabled at the
          ends: paging never wraps. */}
      <button
        type="button"
        className="ms-pagebtn prev"
        aria-label="Previous Area"
        disabled={page === 0}
        onClick={() => goTo(page - 1)}
      >
        ‹
      </button>
      <button
        type="button"
        className="ms-pagebtn next"
        aria-label="Next Area"
        disabled={page === pageCount - 1}
        onClick={() => goTo(page + 1)}
      >
        ›
      </button>
    </div>
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
