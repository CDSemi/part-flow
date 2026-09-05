import './area-board.css';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import type { AreaBoardArea } from '../../api/area-board';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import {
  AreaMachineLayout,
  AreaOverviewRow,
  AreaSummaryCard,
  MachineMonitoringCard,
} from '../../components/area-monitoring';
import { AreaDot } from '../../components/indicators';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { useUiClock } from '../../components/ui-clock';
import {
  aggregateByPartNumber,
  presentAreaInventory,
  presentationArea,
} from '../area-presentation';
import { splitAssignments } from '../area-monitoring';
import { elapsedMinutesSince } from '../dates';
import { compareDemandOrder } from '../demand-order';
import type { MockArea, MockAreaCard, MockAreaMachine } from '../view-models';
import { useAreaBoardFeed } from './area-board-feed';
import { LONG_PREVIEW_BOARD } from './area-board-preview';

type SortKey = 'due' | 'prio' | 'tia' | 'qty';

/**
 * One Area of the board in the shared presentation shapes: the Area
 * itself, one card per quantity presence, and its Machine cards. The
 * All Areas overview and the per-Area detail both render THIS — two
 * presentations of one read, never two reads.
 */
interface AreaPresentation {
  area: MockArea;
  cards: MockAreaCard[];
  machines: MockAreaMachine[];
}

/**
 * The Department the view shows: `?department=<id>` on the URL —
 * otherwise the server resolves the single active Department and
 * refuses an ambiguous configuration with an explicit message. A
 * presentation address, not a route.
 */
function departmentIdFromLocation(): number | null {
  const value = new URLSearchParams(window.location.search).get('department');
  if (value === null || !/^\d+$/.test(value)) return null;
  return Number(value);
}

/**
 * One Area of the server's answer as the shared components take it.
 *
 * Active quantity goes through the SAME mapping the Scan Station uses
 * (`presentAreaInventory`), so neither view can describe a quantity
 * the other one shows differently. A terminal Area holds no active
 * quantity at all — its stocked lines (Phase 10: manufacturing-complete
 * quantity whose flows are closed) become direct `Stocked` rows whose
 * status text states the PN's allocation instead of a due countdown.
 */
function presentBoardArea(entry: AreaBoardArea): AreaPresentation {
  const area = presentationArea(entry.inventory.area, entry.operations);
  const { cards, machines } = presentAreaInventory(entry.inventory, {
    scrapped: entry.scrapped,
    // A monitoring view says what the PN is worked FOR, never what the
    // quantity happens to descend from.
    demandSource: 'open',
  });
  const stocked = entry.stocked.map((line): MockAreaCard => {
    const scrapped = entry.scrapped[line.partNumber];
    return {
      area: area.key,
      pn: line.partNumber,
      // Stocked quantity is manufacturing-complete and PN-level: the
      // Quantity Flow that carried it is closed, so no Work Order
      // context remains on it. The allocation below is what the row
      // states instead.
      workOrder: 'WO —',
      job: '—',
      qty: line.quantity,
      machines: [],
      due: null,
      dueText: `allocated ${line.allocatedQuantity}/${line.quantity}`,
      enteredAreaAt: null,
      received: '',
      ...(scrapped ? { scrapped } : {}),
    };
  });
  return { area, cards: [...cards, ...stocked], machines };
}

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
      case 'prio': {
        // Every Hot rank before every unranked row, whatever the rank
        // number: a Manager may rank past any fixed value, so there is
        // no sentinel — "not Hot" is its own tier, always last.
        const left = a.card.hotRank;
        const right = b.card.hotRank;
        if (left === undefined || right === undefined) {
          if (left === right) return a.seq - b.seq;
          return left === undefined ? 1 : -1;
        }
        return left - right || a.seq - b.seq;
      }
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
//
// Since Phase 11 the content is the REAL Department read
// (`GET /api/area-board`), polled and kept fresh; search, sorting and
// the layout choices stay presentation state of this view.
export function AreaBoardView() {
  const preview = getViewStatePreview();
  const { status: connectivity } = useConnectivity();
  const [departmentId] = useState(departmentIdFromLocation);
  const feed = useAreaBoardFeed(departmentId, connectivity, preview === null);
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

  const board = useMemo(() => {
    if (preview === 'long') return LONG_PREVIEW_BOARD;
    if (preview === 'empty')
      return { department: { id: 0, name: '' }, areas: [] };
    if (preview !== null) return null;
    return feed.state.status === 'ready' ? feed.state.data : null;
  }, [preview, feed.state]);

  const areas = useMemo(
    () => (board ? board.areas.map(presentBoardArea) : []),
    [board],
  );

  // The `Live` status is the BOARD's operational status, not the raw
  // connectivity: it reads healthy only while a complete board is on
  // screen. A first load still running or failed, a failed refresh and
  // an unhealthy connection all read stale with the explicit note.
  const feedStale =
    preview === null &&
    (connectivity !== 'connected' ||
      board === null ||
      (feed.state.status === 'ready' && feed.state.stale));

  const loading =
    preview === 'loading' ||
    (preview === null && feed.state.status === 'loading');
  const loadError =
    preview === 'error'
      ? 'Check the backend connection and try again.'
      : preview === null && feed.state.status === 'error'
        ? feed.state.message
        : null;

  const allCards = areas.flatMap((entry) => entry.cards);
  const query = search.trim().toLowerCase();
  const visible = sortCards(
    allCards.filter((c) => matches(c, query)),
    sort,
    now,
  );
  // The detail keeps one row per separate quantity (each is acted on
  // separately at the Scan Station); the overview is PN-centric — one
  // row per Part Number in the Area, portions aggregated (§6.2).
  const cardsOf = (key: string) => visible.filter((c) => c.area === key);
  const overviewRowsOf = (key: string) => aggregateByPartNumber(cardsOf(key));

  const safeDetailPage = Math.min(detailPage, Math.max(0, areas.length - 1));
  const pageArea = areas[safeDetailPage];
  const activeArea =
    activeTab === 'all'
      ? undefined
      : areas.find((entry) => entry.area.key === activeTab);
  // The meta line counts PART NUMBERS, never rows: one PN may hold
  // several separate quantities in an Area.
  const metaFor = (entry: AreaPresentation | undefined) =>
    entry === undefined ? (
      <>
        <b>{new Set(visible.map((c) => `${c.area}|${c.pn}`)).size}</b> PN ·{' '}
        <b>{visible.reduce((s, c) => s + c.qty, 0)}</b> pcs across all Areas
      </>
    ) : (
      <>
        <b>{overviewRowsOf(entry.area.key).length}</b> PN ·{' '}
        <b>{cardsOf(entry.area.key).reduce((s, c) => s + c.qty, 0)}</b> pcs in{' '}
        {entry.area.name}
      </>
    );

  if (loading) {
    return (
      <section className="ab" aria-label="Area Board">
        <LoadingState label="Loading Area Board" />
      </section>
    );
  }
  if (loadError !== null) {
    return (
      <section className="ab" aria-label="Area Board">
        <ErrorState
          message="Area Board data could not be loaded."
          detail={loadError}
          onRetry={preview === null ? feed.reload : undefined}
        />
      </section>
    );
  }
  if (areas.length === 0) {
    return (
      <section className="ab" aria-label="Area Board">
        <EmptyState
          message="No active Areas are configured in this Department."
          hint="Add an Area in Administration to monitor production here."
        />
      </section>
    );
  }

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
            All Areas{' '}
            <span className="cnt">
              {new Set(allCards.map((c) => `${c.area}|${c.pn}`)).size}
            </span>
          </button>
          {areas.map((entry) => (
            <button
              key={entry.area.key}
              className={`ab-tab ${activeTab === entry.area.key ? 'active' : ''}`}
              aria-pressed={activeTab === entry.area.key}
              onClick={() => setActiveTab(entry.area.key)}
            >
              <AreaDot colorVar={entry.area.colorVar} />
              {entry.area.name}{' '}
              <span className="cnt">
                {new Set(entry.cards.map((c) => c.pn)).size}
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
              ? metaFor(undefined)
              : metaFor(pageArea)
            : metaFor(activeArea)}
        </span>
        <FeedStatus stale={feedStale} />
      </div>

      {narrow ? (
        summary ? (
          <AllAreasOverview
            areas={areas}
            cardsOf={overviewRowsOf}
            wrap
            onOpenArea={(key) => {
              // A summary card header jumps straight to that Area's
              // detail page (the Summary toggle switches off).
              setSummary(false);
              setDetailPage(
                Math.max(
                  0,
                  areas.findIndex((entry) => entry.area.key === key),
                ),
              );
            }}
          />
        ) : (
          <AreaDetailPager
            areas={areas}
            cardsOf={cardsOf}
            page={safeDetailPage}
            onPageChange={setDetailPage}
          />
        )
      ) : activeTab === 'all' ? (
        <AllAreasOverview
          areas={areas}
          cardsOf={overviewRowsOf}
          wrap={wrapOverview}
          onOpenArea={(key) => setActiveTab(key)}
        />
      ) : (
        <AreaDetail
          presentation={activeArea}
          cards={activeArea ? cardsOf(activeArea.area.key) : []}
        />
      )}
    </section>
  );
}

/**
 * Feed status of the board (GUI_DESIGN §6.1): the shared `Live` /
 * `Feed stale — reconnecting` statement of a live monitoring view,
 * never color-only — the wording changes with the tone.
 */
function FeedStatus({ stale }: { stale: boolean }) {
  return (
    <span className={`ab-feed${stale ? ' stale' : ''}`} role="status">
      <span className="ld" aria-hidden="true" />
      {stale ? 'Feed stale — reconnecting' : 'Live'}
    </span>
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
  areas,
  cardsOf,
  page,
  onPageChange,
}: {
  areas: AreaPresentation[];
  cardsOf: (key: string) => MockAreaCard[];
  page: number;
  onPageChange: (page: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageCount = areas.length;

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
        {areas.map((entry, index) => (
          <button
            key={entry.area.key}
            type="button"
            className={`ms-pagedot${index === page ? ' on' : ''}`}
            style={{ ['--acol' as string]: entry.area.colorVar }}
            aria-label={`Go to ${entry.area.name}`}
            aria-current={index === page ? 'true' : undefined}
            onClick={() => goTo(index)}
          />
        ))}
      </div>
      <div className="abd-scroll" ref={scrollRef} onScroll={onScroll}>
        {areas.map((entry) => (
          <div className="abd-page" key={entry.area.key}>
            <AreaDetail presentation={entry} cards={cardsOf(entry.area.key)} />
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
  areas,
  cardsOf,
  wrap,
  onOpenArea,
}: {
  areas: AreaPresentation[];
  cardsOf: (key: string) => MockAreaCard[];
  wrap: boolean;
  onOpenArea: (key: string) => void;
}) {
  return (
    <div className={`ms-scroll ${wrap ? 'wrap' : ''}`}>
      {areas.map((entry) => {
        const area = entry.area;
        const areaCards = cardsOf(area.key);
        const hasMachines = entry.machines.length > 0;
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
                  <AreaOverviewRow key={c.pn} card={c} />
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
  presentation,
  cards,
}: {
  presentation: AreaPresentation | undefined;
  cards: MockAreaCard[];
}) {
  if (!presentation) return null;
  const { area, machines } = presentation;
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
