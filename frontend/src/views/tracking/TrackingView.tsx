import './tracking.css';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  TrackingAllocation,
  TrackingDetail,
  TrackingFilters,
  TrackingFlow,
  TrackingMovement,
  TrackingRow,
} from '../../api/tracking';
import {
  DEFAULT_TRACKING_FILTERS,
  loadTrackingAllocations,
  loadTrackingFlows,
  loadTrackingMovements,
  trackingListQuery,
} from '../../api/tracking';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import {
  AreaDot,
  HotPn,
  RouteModeChip,
  TypeChip,
} from '../../components/indicators';
import { PnImage } from '../../components/PnImage';
import { useUiClock } from '../../components/ui-clock';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatIsoDateShort, formatTimeOfDay } from '../dates';
import type { OlderPages } from './tracking-feed';
import {
  useOlderPages,
  useTrackingDetailFeed,
  useTrackingFilterOptions,
  useTrackingListFeed,
} from './tracking-feed';
import type { DetailRevisions } from './tracking-logic';
import {
  ALLOCATIONS_PAGE_SIZE,
  FLOWS_PAGE_SIZE,
  FLOW_STATUS_LABEL,
  MOVEMENTS_PAGE_SIZE,
  SCRAP_PAGE_SIZE,
  SEARCH_DEBOUNCE_MS,
  STATUS_CLASS,
  STATUS_LABEL,
  STATUS_TITLE,
  TRACKING_MAX_ROWS,
  TRACKING_PAGE_SIZE,
  describeMovement,
  detailRevisions,
  filtersAreDefault,
  flowId,
  locationPercent,
  locationRow,
  movementTypeClass,
  positionText,
  readyNote,
  routeSteps,
  traceText,
} from './tracking-logic';
import { LONG_PREVIEW_PAGE } from './tracking-preview';

const STATUS_OPTIONS: { value: TrackingFilters['status']; label: string }[] = [
  { value: 'ACTIVE', label: 'Active' },
  { value: 'STOCKED', label: 'Stocked' },
  { value: 'OPEN', label: 'Open' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'ALL', label: 'All' },
];

const DUE_OPTIONS: { value: TrackingFilters['due']; label: string }[] = [
  { value: 'ANY', label: 'Any' },
  { value: 'OVERDUE', label: 'Overdue' },
  { value: 'THIS_WEEK', label: 'This week' },
  { value: 'THIS_MONTH', label: 'This month' },
];

/** Area identity color, or the neutral fallback for Areas without one. */
function colorOf(area: { color: string | null }): string {
  return area.color ?? 'var(--faint)';
}

function timestamp(iso: string): string {
  return `${formatIsoDateShort(iso.slice(0, 10))} ${formatTimeOfDay(iso)}`;
}

// PN-centric management view (GUI_DESIGN §7): filterable list + read-only
// detail panel, both REAL reads since Phase 11 — the list is the polled
// `GET /api/tracking` page in the canonical demand order with every
// filter judged server-side, the detail the polled
// `GET /api/tracking/detail` of the selected PN. Movement history is
// immutable — no edit or delete affordances exist.
//
// The detail panel is a MODELESS floating overlay above the results:
// opening and closing it never resizes or reflows the table, and the
// list behind it stays visible and scrollable for comparison — never a
// blocking modal. Selection toggles: the whole result row selects (the
// PN cell button carries keyboard focus and the accessible name);
// clicking the selected row again, the panel's close button, Escape, or
// a click anywhere outside every row and the panel itself all close it
// (the row and panel cases restore focus to the originating row; a
// plain outside click does not).
export function TrackingView() {
  const preview = getViewStatePreview();
  const { status: connectivity } = useConnectivity();
  const [filters, setFilters] = useState<TrackingFilters>(
    DEFAULT_TRACKING_FILTERS,
  );
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [limit, setLimit] = useState(TRACKING_PAGE_SIZE);
  const [selectedPn, setSelectedPn] = useState<string | null>(null);
  /** Per-PN row buttons, for restoring focus after the panel closes. */
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());

  // The search field reaches the server debounced; the selects at once.
  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(filters.search),
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [filters.search]);

  const query = useMemo(
    () => trackingListQuery({ ...filters, search: debouncedSearch }, 0, limit),
    [filters, debouncedSearch, limit],
  );
  const feed = useTrackingListFeed(query, connectivity, preview === null);
  const options = useTrackingFilterOptions(preview === null);

  const updateFilters = (patch: Partial<TrackingFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setLimit(TRACKING_PAGE_SIZE);
  };

  const close = useCallback(
    (restoreFocus: boolean) => {
      if (restoreFocus && selectedPn !== null) {
        rowButtons.current.get(selectedPn)?.focus();
      }
      setSelectedPn(null);
    },
    [selectedPn],
  );

  const toggleSelected = (pn: string) =>
    setSelectedPn((current) => (current === pn ? null : pn));

  // Escape closes the modeless panel (a dialog on top of it — none in
  // Tracking today — would own Escape through its own focus scope).
  useEffect(() => {
    if (selectedPn === null) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        return;
      }
      close(true);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedPn, close]);

  // A click outside every result row and outside the panel itself also
  // closes it (mousedown, so it never races the row's own click
  // handler): clicking a different row still just switches the
  // selection via that row's own handler, never both toggles at once.
  useEffect(() => {
    if (selectedPn === null) return;
    function onDocumentMouseDown(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (
        target.closest('.tk-table tr.selrow') ||
        target.closest('.tk-right')
      ) {
        return;
      }
      close(false);
    }
    document.addEventListener('mousedown', onDocumentMouseDown);
    return () => document.removeEventListener('mousedown', onDocumentMouseDown);
  }, [selectedPn, close]);

  const page = useMemo(() => {
    if (preview === 'long') return LONG_PREVIEW_PAGE;
    if (preview === 'empty') {
      return { rows: [], total: 0, offset: 0, limit, hasMore: false };
    }
    if (preview !== null) return null;
    return feed.state.status === 'ready' ? feed.state.data : null;
  }, [preview, feed.state, limit]);

  // The feed status is the LIST's operational status: it reads live
  // only while a complete page is on screen. A first load still running
  // or failed, a failed refresh and an unhealthy connection all read
  // stale with the explicit note.
  const feedStale =
    preview === null &&
    (connectivity !== 'connected' ||
      page === null ||
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

  const rows: TrackingRow[] = page?.rows ?? [];
  const filtersActive = !filtersAreDefault(filters);

  return (
    <section className="tk" aria-label="PN Tracking">
      <div className="tk-wrap">
        <div className="tk-left">
          <div className="tk-head">
            <h1>PN Tracking</h1>
            <FeedStatus stale={feedStale} />
          </div>
          <div className="tk-filters">
            <input
              placeholder="Search: PN, WO, Job Number…"
              aria-label="Search PN, WO, Job Number"
              value={filters.search}
              onChange={(e) => updateFilters({ search: e.target.value })}
            />
            <select
              aria-label="Area"
              value={filters.areaId ?? ''}
              onChange={(e) =>
                updateFilters({
                  areaId: e.target.value ? Number(e.target.value) : null,
                })
              }
            >
              <option value="">Area: All</option>
              {options.areas.map((area) => (
                <option key={area.id} value={area.id}>
                  {area.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Operation"
              value={filters.operationId ?? ''}
              onChange={(e) =>
                updateFilters({
                  operationId: e.target.value ? Number(e.target.value) : null,
                })
              }
            >
              <option value="">Operation: All</option>
              {options.operations.map((operation) => (
                <option key={operation.id} value={operation.id}>
                  {operation.name ?? operation.code}
                </option>
              ))}
            </select>
            <select
              aria-label="Machine"
              value={filters.machineId ?? ''}
              onChange={(e) =>
                updateFilters({
                  machineId: e.target.value ? Number(e.target.value) : null,
                })
              }
            >
              <option value="">Machine: All</option>
              {options.machines.map((machine) => (
                <option key={machine.id} value={machine.id}>
                  {machine.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Request Type"
              value={filters.requestType ?? ''}
              onChange={(e) =>
                updateFilters({
                  requestType: (e.target.value || null) as
                    'NEW' | 'MODIFY' | null,
                })
              }
            >
              <option value="">Request Type: All</option>
              <option value="NEW">NEW</option>
              <option value="MODIFY">MODIFY</option>
            </select>
            <select
              aria-label="Priority"
              value={filters.hotOnly ? 'HOT' : ''}
              onChange={(e) =>
                updateFilters({ hotOnly: e.target.value === 'HOT' })
              }
            >
              <option value="">Priority: All</option>
              <option value="HOT">Hot only</option>
            </select>
            <select
              aria-label="Status"
              value={filters.status}
              onChange={(e) =>
                updateFilters({
                  status: e.target.value as TrackingFilters['status'],
                })
              }
            >
              {STATUS_OPTIONS.map((option, i) => (
                <option key={option.value} value={option.value}>
                  {i === 0 ? `Status: ${option.label}` : option.label}
                </option>
              ))}
            </select>
            <select
              aria-label="Due"
              value={filters.due}
              onChange={(e) =>
                updateFilters({ due: e.target.value as TrackingFilters['due'] })
              }
            >
              {DUE_OPTIONS.map((option, i) => (
                <option key={option.value} value={option.value}>
                  {i === 0 ? `Due: ${option.label}` : option.label}
                </option>
              ))}
            </select>
          </div>
          {loading ? (
            <LoadingState label="Loading PN Tracking" />
          ) : loadError !== null ? (
            <ErrorState
              message="PN Tracking data could not be loaded."
              detail={loadError}
              onRetry={preview === null ? feed.reload : undefined}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              message={
                filters.search.trim()
                  ? `No PNs match “${filters.search.trim()}” — clear filters.`
                  : 'No PNs match the current filters — clear filters.'
              }
              hint={
                filtersActive
                  ? undefined
                  : 'Part Numbers appear here once a Work Order Demand is saved or quantity is released to production.'
              }
            />
          ) : (
            <table className="tk-table">
              <thead>
                <tr>
                  <th>Part Number</th>
                  <th>Active WO Demand</th>
                  <th>Current distribution</th>
                  <th>Active qty</th>
                  <th>Stocked</th>
                  <th>Scrapped</th>
                  <th>Due (next)</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  // The COMPLETE row is the click target (no other
                  // interactive control lives inside it, so nothing
                  // nests); the PN-cell button stays the keyboard and
                  // screen-reader entry point — its activation bubbles
                  // to this same row handler, one toggle either way.
                  <tr
                    key={row.pn}
                    className={`selrow ${row.pn === selectedPn ? 'sel' : ''}`}
                    onClick={() => toggleSelected(row.pn)}
                  >
                    <td>
                      <button
                        className="rowbtn"
                        ref={(el) => {
                          if (el) rowButtons.current.set(row.pn, el);
                          else rowButtons.current.delete(row.pn);
                        }}
                        aria-pressed={row.pn === selectedPn}
                      >
                        <span className="part">
                          <HotPn rank={row.hotRank ?? undefined} pn={row.pn} />
                        </span>
                        {/* The master-derived name arrives with Part
                            Numbers management (Phase 13); until then —
                            and for a PN whose master record was
                            deleted — the line renders absent. */}
                        <span className="sub" style={{ display: 'block' }}>
                          —
                        </span>
                      </button>
                    </td>
                    <td className="demandcell">
                      {row.demands.length === 0 ? (
                        <span className="sub">—</span>
                      ) : (
                        row.demands.map((d) => (
                          <div key={d.workOrderDemandId}>
                            <span className="mono">
                              {d.workOrderNumber ?? '—'}
                            </span>{' '}
                            · {d.requestedQuantity}{' '}
                            <TypeChip type={d.requestType} />
                          </div>
                        ))
                      )}
                    </td>
                    <td>
                      <div className="distmini">
                        {row.distribution.length === 0 ? (
                          <span className="sub">—</span>
                        ) : (
                          row.distribution.map((d) => (
                            <span
                              key={`${d.area.id}-${d.stocked ? 's' : 'a'}`}
                              title={d.stocked ? 'stocked' : undefined}
                            >
                              <AreaDot colorVar={colorOf(d.area)} size={8} />
                              {d.area.name} <b>{d.quantity}</b>
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                    {/* data-label: inline column captions in the
                        collapsed stacked layout (GUI_DESIGN §2.5) —
                        bare quantities and dates are not self-evident
                        without the header row. */}
                    <td className="mono" data-label="Active qty">
                      {row.activeQuantity}
                    </td>
                    <td className="mono" data-label="Stocked">
                      {row.stockedQuantity}
                    </td>
                    <td
                      className={`mono ${row.scrappedQuantity ? 'scrapqty' : ''}`}
                      data-label="Scrapped"
                    >
                      {row.scrappedQuantity || '—'}
                    </td>
                    <td data-label="Due (next)">
                      {formatIsoDateShort(row.nextDueDate)}
                    </td>
                    <td>
                      <span
                        className={`status ${STATUS_CLASS[row.status]}`}
                        title={STATUS_TITLE[row.status]}
                      >
                        {STATUS_LABEL[row.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {page !== null && rows.length > 0 ? (
            <div className="tk-paging" role="status">
              <span>
                Showing <b>{rows.length}</b> of <b>{page.total}</b> PNs
              </span>
              {page.hasMore && limit < TRACKING_MAX_ROWS ? (
                <button
                  className="btn ghost"
                  onClick={() => setLimit(TRACKING_MAX_ROWS)}
                >
                  Show more
                </button>
              ) : page.hasMore ? (
                <span className="sub">
                  Only the first {TRACKING_MAX_ROWS} are listed — narrow the
                  search or filters to find the rest.
                </span>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Modeless floating detail overlay: rendered above the table
            (its own scroll area), so the results list never resizes or
            reflows and stays available for comparison behind it. The
            panel is keyed by PN: a different selection starts a fresh
            detail read with its own loading state. */}
        {selectedPn !== null ? (
          <TrackingDetailPanel
            key={selectedPn}
            pn={selectedPn}
            enabled={preview === null}
            onClose={() => close(true)}
          />
        ) : null}
      </div>
    </section>
  );
}

/**
 * Feed status of the list (GUI_DESIGN §6.1 / §5): the shared `Live` /
 * `Feed stale — reconnecting` statement of a live monitoring view,
 * never color-only — the wording changes with the tone.
 */
function FeedStatus({ stale }: { stale: boolean }) {
  return (
    <span className={`tk-feed${stale ? ' stale' : ''}`} role="status">
      <span className="ld" aria-hidden="true" />
      {stale ? 'Feed stale — reconnecting' : 'Live'}
    </span>
  );
}

/** Accessible detail-panel close control (≥ 48 px touch target). */
function CloseDetailButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      className="tk-close"
      aria-label="Close details"
      title="Close details"
      onClick={onClose}
    >
      ✕
    </button>
  );
}

/** One paged detail section: the first page plus the older pages loaded. */
interface PagedSection<T> {
  items: T[];
  total: number;
  hasOlder: boolean;
  loading: boolean;
  error: string | null;
  showOlder: () => void;
}

function pagedSection<T>(
  first: { items: T[]; total: number; hasMore: boolean },
  older: OlderPages<T> | null,
  showOlder: () => void,
): PagedSection<T> {
  return {
    items: [...first.items, ...(older?.items ?? [])],
    total: first.total,
    hasOlder:
      older === null || older.loaded === 0
        ? first.hasMore
        : older.nextBefore !== null,
    loading: older?.loading ?? false,
    error: older?.error ?? null,
    showOlder,
  };
}

interface DetailPaging {
  movements: PagedSection<TrackingMovement>;
  scrap: PagedSection<TrackingMovement>;
  flows: PagedSection<TrackingFlow>;
  allocations: PagedSection<TrackingAllocation>;
}

const NO_REVISIONS: DetailRevisions = {
  movements: '',
  scrap: '',
  flows: '',
  allocations: '',
};

/**
 * The floating detail overlay of ONE PN: its own polled read, with the
 * older pages of each paged section (Movement history, Scrap history,
 * Quantity Flows, allocation history) appended on request. The pages
 * continue below the keyset the first page ended on; a refresh that
 * moves that boundary (new rows arrived) or changes the section's
 * revision signature (`detailRevisions` — rows already appended may
 * read differently now) drops the appended pages and reads the same
 * depth again, so a section never shows a gap, a duplicate or a stale
 * row; a refresh that changes neither keeps the pages.
 */
function TrackingDetailPanel({
  pn,
  enabled,
  onClose,
}: {
  pn: string;
  enabled: boolean;
  onClose: () => void;
}) {
  const { status: connectivity } = useConnectivity();
  const feed = useTrackingDetailFeed(pn, connectivity, enabled);
  const detail = feed.state.status === 'ready' ? feed.state.data : null;
  const stale =
    connectivity !== 'connected' ||
    detail === null ||
    (feed.state.status === 'ready' && feed.state.stale);
  const revisions = detail === null ? NO_REVISIONS : detailRevisions(detail);

  const movements = useOlderPages(
    detail?.movements.nextBeforeMovementId ?? null,
    revisions.movements,
    useCallback(
      (before: number) =>
        loadTrackingMovements(pn, before, MOVEMENTS_PAGE_SIZE).then((page) => ({
          items: page.movements,
          nextBefore: page.nextBeforeMovementId,
        })),
      [pn],
    ),
  );
  const scrap = useOlderPages(
    detail?.scrapHistory.nextBeforeMovementId ?? null,
    revisions.scrap,
    useCallback(
      (before: number) =>
        loadTrackingMovements(pn, before, SCRAP_PAGE_SIZE, 'SCRAPPED').then(
          (page) => ({
            items: page.movements,
            nextBefore: page.nextBeforeMovementId,
          }),
        ),
      [pn],
    ),
  );
  const flows = useOlderPages(
    detail?.flows.nextBeforeFlowId ?? null,
    revisions.flows,
    useCallback(
      (before: number) =>
        loadTrackingFlows(pn, before, FLOWS_PAGE_SIZE).then((page) => ({
          items: page.flows,
          nextBefore: page.nextBeforeFlowId,
        })),
      [pn],
    ),
  );
  const allocations = useOlderPages(
    detail?.allocations.nextBeforeAllocationId ?? null,
    revisions.allocations,
    useCallback(
      (before: number) =>
        loadTrackingAllocations(pn, before, ALLOCATIONS_PAGE_SIZE).then(
          (page) => ({
            items: page.allocations,
            nextBefore: page.nextBeforeAllocationId,
          }),
        ),
      [pn],
    ),
  );

  return (
    <aside className="tk-right" aria-label="PN detail">
      {detail === null ? (
        <>
          <div className="tk-pnrow">
            <div>
              <h2>{pn}</h2>
            </div>
            <span className="spacer" />
            <CloseDetailButton onClose={onClose} />
          </div>
          {!enabled ? (
            <EmptyState message="PN details are not part of this state preview." />
          ) : feed.state.status === 'error' ? (
            <ErrorState
              message="PN details could not be loaded."
              detail={feed.state.message}
              onRetry={feed.reload}
            />
          ) : (
            <LoadingState label={`Loading details of ${pn}`} />
          )}
        </>
      ) : (
        <TrackingDetailContent
          detail={detail}
          stale={stale}
          paging={{
            movements: pagedSection(
              {
                items: detail.movements.movements,
                total: detail.movements.total,
                hasMore: detail.movements.hasMore,
              },
              movements.older,
              movements.showOlder,
            ),
            scrap: pagedSection(
              {
                items: detail.scrapHistory.movements,
                total: detail.scrapHistory.total,
                hasMore: detail.scrapHistory.hasMore,
              },
              scrap.older,
              scrap.showOlder,
            ),
            flows: pagedSection(
              {
                items: detail.flows.flows,
                total: detail.flows.total,
                hasMore: detail.flows.hasMore,
              },
              flows.older,
              flows.showOlder,
            ),
            allocations: pagedSection(
              {
                items: detail.allocations.allocations,
                total: detail.allocations.total,
                hasMore: detail.allocations.hasMore,
              },
              allocations.older,
              allocations.showOlder,
            ),
          }}
          onClose={onClose}
        />
      )}
    </aside>
  );
}

/** `Showing n of m <noun>` with the explicit continuation control. */
function OlderControl<T>({
  section,
  noun,
  label,
}: {
  section: PagedSection<T>;
  noun: string;
  label: string;
}) {
  return (
    <div className="tk-paging">
      <span>
        Showing <b>{section.items.length}</b> of <b>{section.total}</b> {noun}
      </span>
      {section.hasOlder ? (
        <button
          className="btn ghost"
          onClick={section.showOlder}
          disabled={section.loading}
        >
          {section.loading ? 'Loading…' : label}
        </button>
      ) : null}
      {section.error ? (
        <span className="tk-error" role="alert">
          {section.error}
        </span>
      ) : null}
    </div>
  );
}

/** One row of the immutable Movement history (also the Scrap history). */
function MovementRow({ movement: m }: { movement: TrackingMovement }) {
  return (
    <li className={m.reversedByMovementId !== null ? 'reversed' : ''}>
      <span className="t">{timestamp(m.occurredAt)}</span>
      <span className={`mtype ${movementTypeClass(m.movementType)}`}>
        {m.movementType}
      </span>
      {m.movementReason === 'REPAIR' ? (
        <span className="mtype scr">REPAIR</span>
      ) : null}
      {m.reversedByMovementId !== null ? (
        <span
          className="mtype rev"
          title={`Reversed by Movement #${m.reversedByMovementId}`}
        >
          REVERSED
        </span>
      ) : null}
      <span className="desc">{describeMovement(m)}</span>
    </li>
  );
}

function TrackingDetailContent({
  detail: d,
  stale,
  paging,
  onClose,
}: {
  detail: TrackingDetail;
  stale: boolean;
  paging: DetailPaging;
  onClose: () => void;
}) {
  const now = useUiClock('minute');
  const requestedTotal = d.demands.reduce((s, x) => s + x.requestedQuantity, 0);
  const allocatedTotal = d.demands.reduce((s, x) => s + x.allocatedQuantity, 0);
  const shareTotal = d.activeQuantity + d.stockedQuantity;
  const ready = readyNote(d.locations);

  return (
    <>
      <div className="tk-pnrow">
        {/* The ONE shared PN image presentation (PnImage) — the same
            default placeholder as Management → Part Numbers. */}
        <PnImage pn={d.pn} />
        <div>
          <h2>{d.pn}</h2>
          <div className="jsub">
            {/* Master-derived metadata (name, revision, image, ERP id)
                arrives with Part Numbers management (Phase 13); absent
                fields render `—`, and a PN without a master record
                keeps its canonical PN and derived barcode. */}
            name <b>—</b> · barcode <b>{d.barcodeValue}</b> · ERP id <b>—</b>
            {d.master === null ? (
              <> · no Part Number master record — history unaffected</>
            ) : null}
          </div>
          <div className="jsub">
            <span
              className={`status ${STATUS_CLASS[d.status]}`}
              title={STATUS_TITLE[d.status]}
            >
              {STATUS_LABEL[d.status]}
            </span>
            {stale ? (
              <span className="tk-stale" role="status">
                {' '}
                Feed stale — reconnecting
              </span>
            ) : null}
          </div>
        </div>
        <span className="spacer" />
        <CloseDetailButton onClose={onClose} />
      </div>

      <div className="tk-sec">
        <h4>
          Active WO Demand{' '}
          <span className="tag">business demand — requested quantity</span>
        </h4>
        {d.demands.length === 0 ? (
          <div className="prognote">
            No open Work Order Demand — every Work Order of this PN is complete,
            or none was saved.
          </div>
        ) : (
          <>
            <table className="demand">
              <thead>
                <tr>
                  <th>WO</th>
                  <th>Type</th>
                  <th>Req.</th>
                  <th>Released</th>
                  <th>Alloc.</th>
                  <th>Shortage</th>
                  <th>Due</th>
                  <th>Priority</th>
                </tr>
              </thead>
              <tbody>
                {d.demands.map((row) => (
                  // data-label: inline column captions in the collapsed
                  // stacked layout (GUI_DESIGN §2.5) — bare numbers and
                  // dates are not self-evident without the header row.
                  <tr key={row.workOrderDemandId}>
                    <td className="mono" data-label="WO">
                      {row.workOrderNumber ?? '—'}
                      {row.jobNumbers.length > 0 ? (
                        <span className="sub">
                          {' '}
                          · Job {row.jobNumbers.join(', ')}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <TypeChip type={row.requestType} />
                    </td>
                    <td className="mono" data-label="Req.">
                      {row.requestedQuantity}
                    </td>
                    <td className="mono" data-label="Released">
                      {row.releasedQuantity}
                    </td>
                    <td
                      className={`mono ${row.allocatedQuantity === 0 ? 'zero' : ''}`}
                      data-label="Alloc."
                    >
                      {row.allocatedQuantity}
                    </td>
                    <td className="mono short" data-label="Shortage">
                      {row.shortage}
                    </td>
                    <td data-label="Due">{formatIsoDateShort(row.dueDate)}</td>
                    <td data-label="Priority">
                      {row.priorityRank !== null
                        ? `🔥#${row.priorityRank}`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="prog">
              <i
                style={{
                  width: `${requestedTotal > 0 ? Math.round((allocatedTotal / requestedTotal) * 100) : 0}%`,
                }}
              />
            </div>
            <div className="prognote">
              Allocated {allocatedTotal} / {requestedTotal} requested
              {d.stockedQuantity === 0 ? ' — nothing stocked yet' : ''}
            </div>
          </>
        )}
      </div>

      <div className="tk-sec">
        <h4>
          Current quantity by Area{' '}
          <span className="tag">current recorded location</span>
        </h4>
        {d.locations.length === 0 && d.stocked.length === 0 ? (
          <div className="prognote">No quantity in production or in stock.</div>
        ) : (
          <div className="dist">
            {/* `tone` keeps the holding states visually distinct:
                active Machine assignment, Area-queue waiting, direct
                processing and Area completion (`done` —
                READY_TO_TRANSFER). A done row names the Area as the
                location; the Machine no longer holds the quantity. */}
            {d.locations.map((location, index) => {
              const view = locationRow(location);
              return (
                <div
                  className={`drow${view.tone === 'done' ? ' done' : ''}`}
                  key={`loc-${index}`}
                >
                  <AreaDot colorVar={colorOf(location.area)} />
                  <span className="nm">
                    {view.name} <span className="sub">{view.sub}</span>
                  </span>
                  <span className="bar">
                    <i
                      style={{
                        width: `${locationPercent(location.quantity, shareTotal)}%`,
                        background: colorOf(location.area),
                        opacity: view.tone === 'queue' ? 0.55 : 1,
                      }}
                    />
                  </span>
                  <span className="q">{location.quantity}</span>
                </div>
              );
            })}
            {d.stocked.map((entry) => (
              <div className="drow stocked" key={`stk-${entry.area.id}`}>
                <AreaDot colorVar={colorOf(entry.area)} />
                <span className="nm">
                  {entry.area.name} <span className="sub">stocked</span>
                </span>
                <span className="bar">
                  <i
                    style={{
                      width: `${locationPercent(entry.quantity, shareTotal)}%`,
                      background: colorOf(entry.area),
                      opacity: 0.7,
                    }}
                  />
                </span>
                <span className="q">{entry.quantity}</span>
              </div>
            ))}
          </div>
        )}
        {ready !== null ? (
          <div className="prognote donenote">{ready}</div>
        ) : null}
      </div>

      <div className="tk-sec">
        <h4>
          Quantity Flows &amp; Routes{' '}
          <span className="tag">
            Planned Route (guidance) or Floating actual route trace — the PN is
            not at one step
          </span>
        </h4>
        {paging.flows.items.map((flow) => (
          <FlowBlock flow={flow} now={now} key={flow.id} />
        ))}
        {/* One bounded page at a time — ACTIVE flows (oldest first)
            before closed ones (newest first); the current quantities
            above come from `locations`, complete regardless of paging. */}
        <OlderControl
          section={paging.flows}
          noun="Quantity Flows"
          label="Show older Quantity Flows"
        />
      </div>

      <div className="tk-sec">
        <h4>
          Movement history{' '}
          <span className="tag">complete activity history</span>
        </h4>
        <ul className="mv history">
          {paging.movements.items.map((m) => (
            <MovementRow movement={m} key={m.id} />
          ))}
        </ul>
        <OlderControl
          section={paging.movements}
          noun="Movements"
          label="Show older Movements"
        />
      </div>

      <div className="tk-sec">
        <h4>
          Scrap history{' '}
          <span className="tag">
            auditable — scrap never reduces requested quantity
          </span>
        </h4>
        <div className="prognote" style={{ marginTop: 0 }}>
          Cumulative scrapped: <b>{d.scrappedQuantity}</b> pcs (an undone scrap
          stays listed below, marked REVERSED, and no longer counts).
          Reconciliation: introduced {d.introducedQuantity} = active{' '}
          {d.activeQuantity} + stocked {d.stockedQuantity} + scrapped{' '}
          {d.scrappedQuantity}.
        </div>
        {paging.scrap.total === 0 ? (
          <div className="prognote">No scrap recorded for this PN.</div>
        ) : (
          <>
            <ul className="mv scrap">
              {paging.scrap.items.map((m) => (
                <MovementRow movement={m} key={m.id} />
              ))}
            </ul>
            <OlderControl
              section={paging.scrap}
              noun="scrap events"
              label="Show older scrap events"
            />
          </>
        )}
      </div>

      <div className="tk-sec">
        <h4>
          Stocked &amp; Allocation history{' '}
          <span className="tag">stocked quantity assigned to demand</span>
        </h4>
        {d.stockedQuantity === 0 && paging.allocations.total === 0 ? (
          <div className="prognote" style={{ marginTop: 0 }}>
            Nothing stocked yet for this PN. Allocation suggestions follow the
            Hot rank first, then the earliest due date.
          </div>
        ) : (
          <>
            <div className="prognote" style={{ marginTop: 0 }}>
              Stocked <b>{d.stockedQuantity}</b> pcs · allocated{' '}
              <b>{d.allocatedQuantity}</b> · available{' '}
              <b>{d.availableStockedQuantity}</b>. Allocation follows the Hot
              rank first, then the earliest due date.
            </div>
            <ul className="mv">
              {paging.allocations.items.map((a) => (
                <li
                  key={a.id}
                  className={
                    a.reversedByAllocationId !== null ? 'reversed' : ''
                  }
                >
                  <span className="t">{timestamp(a.allocatedAt)}</span>
                  <span
                    className={`mtype ${a.reversesAllocationId !== null ? 'rev' : 'stk'}`}
                  >
                    {a.reversesAllocationId !== null ? 'REVERSAL' : 'ALLOCATED'}
                  </span>
                  <span className="desc">
                    {a.quantity} pcs · WO {a.workOrder.workOrderNumber ?? '—'} ·{' '}
                    {a.source.toLowerCase()}
                    {a.isManualOverride ? ' · manual override' : ''}
                    {a.reversesAllocationId !== null
                      ? ` · reverses allocation #${a.reversesAllocationId}`
                      : ''}
                    {a.allocationReason
                      ? ` · reason: ${a.allocationReason}`
                      : ''}
                    {a.stationId ? ` · ${a.stationId}` : ''}
                  </span>
                </li>
              ))}
            </ul>
            <OlderControl
              section={paging.allocations}
              noun="allocation entries"
              label="Show older allocation entries"
            />
          </>
        )}
      </div>
    </>
  );
}

/** One Quantity Flow: header, position line and route line. */
function FlowBlock({ flow, now }: { flow: TrackingFlow; now: number }) {
  const steps = routeSteps(flow);
  return (
    <div className={`qflow${flow.position === null ? ' closed' : ''}`}>
      <div className="qf-head">
        <span className="qf-id">{flowId(flow.id)}</span>
        <span className="qf-q">{flow.quantity} pcs</span>
        <RouteModeChip
          mode={flow.routeMode}
          detail={flow.routeMode === 'FLOATING' ? 'actual trace' : 'snapshot'}
        />
        <span className="qf-pos">{positionText(flow, now)}</span>
      </div>
      {steps.length > 0 ? (
        <div className="route">
          {/* Steps and arrows are separate sibling flex items in
              document order (step, arrow, step, …) so an arrow can
              never overlap a step card and wrapping stays readable.
              Repeated Areas are preserved: the trace is Movement
              history, so the same Area may appear more than once
              and the step name alone is not a unique key. */}
          {steps.flatMap((step, i) => {
            const stepNode = (
              <span
                key={step.key}
                className={`rstep ${step.state === 'done' ? 'done' : step.state === 'cur' ? 'cur' : ''} ${step.repair ? 'repair' : ''}`}
                title={
                  step.inherited
                    ? 'Before the split — recorded on the source Quantity Flow'
                    : undefined
                }
              >
                {step.label}
                {step.repair ? (
                  <span className="repairmark"> ⟲ REPAIR</span>
                ) : null}
              </span>
            );
            if (i === 0) return [stepNode];
            return [
              <span
                key={`arrow-${step.key}`}
                className="rarrow"
                aria-hidden="true"
              >
                →
              </span>,
              stepNode,
            ];
          })}
        </div>
      ) : (
        <div className="devnote">
          {flow.position === null
            ? (FLOW_STATUS_LABEL[flow.status] ?? flow.status)
            : 'No arrival recorded on this Quantity Flow yet.'}
        </div>
      )}
      {flow.routeMode === 'PLANNED' ? (
        <div className="devnote">
          Planned Route{' '}
          {flow.sourceTemplate ? `“${flow.sourceTemplate.name}” ` : ''}
          (snapshot) — guidance only; actual Movement history stays
          authoritative.
          {flow.trace.length > 0 ? ` Actual path: ${traceText(flow)}.` : ''}
          {flow.offRoute ? ' Currently off the Planned Route.' : ''}
        </div>
      ) : (
        <div className="devnote">
          Floating Route — the trace above is the actual recorded history
          (repeated Areas preserved). ⟲ REPAIR marks an explicit Repair return.
        </div>
      )}
      {flow.deviations.map((deviation) => (
        <div className="devnote deviation" key={deviation.movementId}>
          Route deviation confirmed {timestamp(deviation.occurredAt)}
          {deviation.stationId ? ` at ${deviation.stationId}` : ''}: expected{' '}
          {deviation.expectedArea?.name ?? 'route end'}
          {deviation.expectedOperation
            ? ` (${deviation.expectedOperation.name ?? deviation.expectedOperation.code})`
            : ''}
          , actual {deviation.actualArea.name}
          {deviation.actualOperation
            ? ` (${deviation.actualOperation.name ?? deviation.actualOperation.code})`
            : ''}
          {deviation.reason ? ` — reason: ${deviation.reason}` : ''}. The
          previous route stays recorded unchanged.
        </div>
      ))}
    </div>
  );
}
