import '../tracking/tracking.css';

import {
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';
import type { FormEvent, MouseEvent, ReactNode } from 'react';

import { ApiError, errorMessage } from '../../api/client';
import {
  loadArchivedFlow,
  searchArchivedMovements,
} from '../../api/history-archive-views';
import type {
  ArchivedFlow,
  ArchivedMovement,
  ArchivedMovementSearch,
  ArchiveView,
  ArchiveViewNames,
} from '../../api/history-archive-views';
import { RetryFailedLoadsContext, useApiData } from '../../api/use-api-data';
import { Link } from '../../app/link';
import { useSession } from '../../app/session-context';
import { RouteModeChip } from '../../components/indicators';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  archivedTimestamp,
  flowId,
  movementTypeClass,
} from '../tracking/tracking-logic';
import { WholeCodes } from '../tracking/WholeCodes';
import {
  areaName,
  describeArchivedMovement,
  EMPTY_SEARCH_FORM,
  flowRecordLines,
  flowStatusLine,
  loadViewerContext,
  machineName,
  operationName,
  searchIsEmpty,
  searchOf,
  VIEWER_PAGE_SIZE,
  viewCopyText,
  viewNotOpen,
  workerName,
} from './archive-viewer';
import type { SearchForm } from './archive-viewer';

// Administration → History archival & purge → the archive viewer
// (Phase 16, archival design D16, build step 5; GUI_DESIGN §9): the page
// at `/administration/history-archival/views/:id` that reads one run's
// archive loaded for viewing. It needs the Run history archival and purge
// permission — without it the page says so and sends no request — and a
// view the server reads (`OPEN`); a view waiting, closed or ended shows
// the server's own words. A permanent mark says that this is archived
// history from run N, a read-only copy and not live history. The search
// (Part Number, Work Order Number exactly as stored, Quantity Flow, Area,
// Operation, Machine, Worker and a range of days) lists the matching
// Movements newest first and pages on with the server's cursor, as
// Tracking pages a history; a row opens its flow's whole archived history
// with its lineage in Tracking's modeless detail panel, every name as it
// was when the archive was written and every code kept whole on its line.
// A view closed while it is read (409) turns the page into its closed
// state, back at its top with the focus on the server's words.

/** The archive viewer page of view `viewId`, inside Administration. */
export function ArchiveViewerPage({
  viewId,
  onShowSection,
}: {
  viewId: number;
  /** Selects History archival & purge for the way back. */
  onShowSection: () => void;
}) {
  const { can } = useSession();
  const back = (
    <Link
      className="admin-arv-back"
      to="/administration"
      onClick={onShowSection}
    >
      ← History archival &amp; purge
    </Link>
  );
  if (!can('RUN_HISTORY_ARCHIVAL_AND_PURGE')) {
    return (
      <section className="admin-arv" aria-label="Archived history">
        {back}
        <h1>Archived history</h1>
        <p className="admin-confighelp">
          Your account cannot open archived history. Opening it needs the Run
          history archival and purge permission.
        </p>
        <p className="admin-confighelp">
          Ask an administrator if you need access.
        </p>
      </section>
    );
  }
  return <ArchiveViewer key={viewId} viewId={viewId} back={back} />;
}

function ArchiveViewer({ viewId, back }: { viewId: number; back: ReactNode }) {
  const load = useCallback(() => loadViewerContext(viewId), [viewId]);
  const context = useApiData(load);
  /** A read refused because the view stopped being open meanwhile. */
  const [closed, setClosed] = useState<string | null>(null);
  const headingId = useId();
  const sectionRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const unavailableRef = useRef<HTMLDivElement>(null);
  // Arriving on the page (from View in the runs) moves focus to it.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const { state } = context;
  const data = state.status === 'ready' ? state.data : null;
  const view = data?.view ?? null;
  const unavailable =
    data === null
      ? null
      : (closed ?? (data.kind === 'open' ? null : data.message));
  // A view the server cannot read — waiting, closed, failed, cancelled or
  // unknown, also one closed while it was read, after the reader scrolled
  // and the control in focus went away — returns the page to its top, with
  // the focus on the server's words.
  useEffect(() => {
    if (unavailable === null) return;
    sectionRef.current?.scrollIntoView?.({ block: 'start' });
    unavailableRef.current?.focus({ preventScroll: true });
  }, [unavailable]);
  return (
    <section ref={sectionRef} className="admin-arv" aria-labelledby={headingId}>
      {back}
      <h1 id={headingId} ref={headingRef} tabIndex={-1}>
        {view === null
          ? 'Archived history'
          : `Archived history of run ${view.runId}`}
      </h1>
      {state.status === 'loading' ? (
        <LoadingState label="Loading archived history" />
      ) : null}
      {state.status === 'error' ? (
        <ErrorState
          message="The archived history could not be loaded."
          detail={state.message}
          onRetry={context.reload}
        />
      ) : null}
      {unavailable !== null ? (
        <div
          ref={unavailableRef}
          className="admin-arv-unavailable"
          role="status"
          tabIndex={-1}
        >
          <p>{unavailable}</p>
          {closed === null &&
          (view?.state === 'REQUESTED' || view?.state === 'LOADING') ? (
            <button
              type="button"
              className="admin-btn"
              onClick={context.reload}
            >
              Check again
            </button>
          ) : null}
        </div>
      ) : null}
      {data?.kind === 'open' && closed === null ? (
        <OpenViewer
          viewId={viewId}
          view={data.view}
          names={data.names}
          onClosed={setClosed}
        />
      ) : null}
    </section>
  );
}

/** The mark, the search, the results and the flow detail of a view the
 * server reads. */
function OpenViewer({
  viewId,
  view,
  names,
  onClosed,
}: {
  viewId: number;
  /** Null when the server's newest views do not include it. */
  view: ArchiveView | null;
  names: ArchiveViewNames;
  onClosed: (message: string) => void;
}) {
  const [form, setForm] = useState<SearchForm>(EMPTY_SEARCH_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [search, setSearch] = useState<ArchivedMovementSearch>({});
  const results = useArchiveSearch(viewId, search, onClosed);
  const [selected, setSelected] = useState<{
    flowId: number;
    /** The row button that opened it, which gets the focus back. */
    opener: HTMLElement | null;
    /** Opened from a lineage link inside the panel: the panel's heading
     * takes the focus the link leaves. */
    fromPanel: boolean;
  } | null>(null);
  const source = view === null ? `view ${viewId}` : `run ${view.runId}`;

  const update = (patch: Partial<SearchForm>) =>
    setForm((current) => ({ ...current, ...patch }));
  const apply = (next: SearchForm) => {
    const asked = searchOf(next);
    if ('error' in asked) {
      setFormError(asked.error);
      return;
    }
    setFormError(null);
    setSearch(asked.search);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    apply(form);
  };
  const clear = () => {
    setForm(EMPTY_SEARCH_FORM);
    apply(EMPTY_SEARCH_FORM);
  };

  const close = useCallback(
    (restoreFocus: boolean) => {
      if (restoreFocus && selected?.opener?.isConnected) {
        selected.opener.focus();
      }
      setSelected(null);
    },
    [selected],
  );
  const toggleFlow = (event: MouseEvent<HTMLElement>, id: number) => {
    const opener = event.currentTarget.querySelector('button');
    setSelected((current) =>
      current?.flowId === id ? null : { flowId: id, opener, fromPanel: false },
    );
  };

  // Escape closes the modeless panel; a click outside every result row and
  // outside the panel closes it too (as Tracking's detail panel).
  useEffect(() => {
    if (selected === null) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        return;
      }
      close(true);
    }
    function onMouseDown(event: globalThis.MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (
        !target ||
        target.closest('.admin-arv-list li') ||
        target.closest('.admin-arv-flow')
      ) {
        return;
      }
      close(false);
    }
    window.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onMouseDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, [selected, close]);

  return (
    <>
      {/* The permanent mark: it stays in sight while the page scrolls. */}
      <div className="admin-arv-mark" role="note" aria-label="Archived history">
        <span className="admin-arv-badge">Archived</span>
        <span>{`Archived history from ${source} — a read-only copy, not live history`}</span>
      </div>
      <p className="admin-arv-about">
        {viewCopyText(view)} Nothing here can be changed, and none of it is part
        of the live history or of PartFlow&apos;s figures. Names read as they
        were when the archive was written.
      </p>

      <form
        className="admin-arv-search"
        aria-label="Search the archived Movements"
        onSubmit={submit}
      >
        <div className="admin-configgrid admin-arv-fields">
          <label>
            <span>Part Number</span>
            <input
              className="field mono"
              value={form.partNumber}
              autoComplete="off"
              onChange={(event) => update({ partNumber: event.target.value })}
            />
          </label>
          <label>
            <span>Work Order Number</span>
            <input
              className="field mono"
              value={form.workOrderNumber}
              placeholder="Exactly as stored"
              autoComplete="off"
              onChange={(event) =>
                update({ workOrderNumber: event.target.value })
              }
            />
          </label>
          <label>
            <span>Quantity Flow</span>
            <input
              className="field mono"
              value={form.flow}
              placeholder="QF-…"
              autoComplete="off"
              onChange={(event) => update({ flow: event.target.value })}
            />
          </label>
          <label>
            <span>Area</span>
            <select
              className="field"
              value={form.areaId}
              onChange={(event) => update({ areaId: event.target.value })}
            >
              <option value="">All</option>
              {names.areas.map((area) => (
                <option key={area.id} value={area.id}>
                  {areaName(area)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Operation</span>
            <select
              className="field"
              value={form.operationId}
              onChange={(event) => update({ operationId: event.target.value })}
            >
              <option value="">All</option>
              {names.operations.map((operation) => (
                <option key={operation.id} value={operation.id}>
                  {operationName(operation)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Machine</span>
            <select
              className="field"
              value={form.machineId}
              onChange={(event) => update({ machineId: event.target.value })}
            >
              <option value="">All</option>
              {names.machines.map((machine) => (
                <option key={machine.id} value={machine.id}>
                  {machineName(machine)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Worker</span>
            <select
              className="field"
              value={form.workerId}
              onChange={(event) => update({ workerId: event.target.value })}
            >
              <option value="">All</option>
              {names.workers.map((worker) => (
                <option key={worker.id} value={worker.id}>
                  {workerName(worker)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>First day</span>
            <input
              className="field"
              type="date"
              value={form.dateFrom}
              onChange={(event) => update({ dateFrom: event.target.value })}
            />
          </label>
          <label>
            <span>Last day</span>
            <input
              className="field"
              type="date"
              value={form.dateTo}
              onChange={(event) => update({ dateTo: event.target.value })}
            />
          </label>
        </div>
        {formError !== null ? (
          <div className="err" role="alert">
            {formError}
          </div>
        ) : null}
        <div className="admin-cardactionrow">
          <button type="button" className="admin-btn" onClick={clear}>
            Clear
          </button>
          <button type="submit" className="admin-btn primary">
            Search
          </button>
        </div>
      </form>

      <div className="tk-sec admin-arv-results">
        <h4>
          Movement history{' '}
          <span className="tag">archived — read-only, newest first</span>
        </h4>
        <SearchResults
          results={results}
          searchEmpty={searchIsEmpty(search)}
          selectedFlowId={selected?.flowId ?? null}
          onToggleFlow={toggleFlow}
        />
      </div>

      {selected !== null ? (
        <ArchivedFlowPanel
          key={selected.flowId}
          viewId={viewId}
          id={selected.flowId}
          source={source}
          focusHeading={selected.fromPanel}
          onSelectFlow={(id) =>
            setSelected((current) => ({
              flowId: id,
              opener: current?.opener ?? null,
              fromPanel: true,
            }))
          }
          onClose={() => close(true)}
          onClosed={onClosed}
        />
      ) : null}
    </>
  );
}

/** The results of a search as read so far. */
type SearchPages =
  | { status: 'loading' }
  /** The server refused the search (422): its text, as given. */
  | { status: 'refused'; message: string }
  | { status: 'error'; message: string }
  | {
      status: 'ready';
      movements: ArchivedMovement[];
      total: number;
      /** The cursor of the next (older) page; null on the last page. */
      nextBefore: number | null;
      loadingMore: boolean;
      moreError: string | null;
    };

/** The results with what the page can do next: read the first page
 * again after an error, or read the next older page. */
type Results =
  | Exclude<SearchPages, { status: 'error' } | { status: 'ready' }>
  | (Extract<SearchPages, { status: 'error' }> & { retry: () => void })
  | (Extract<SearchPages, { status: 'ready' }> & { showOlder: () => void });

/** The results of `search`: its first page, then each older page the
 * server's cursor names, appended — the view is a fixed copy, so the pages
 * never overlap. A view no longer open (409) calls `onClosed`. */
function useArchiveSearch(
  viewId: number,
  search: ArchivedMovementSearch,
  onClosed: (message: string) => void,
): Results {
  const [page, setPage] = useState<SearchPages>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  /** The search the answers belong to: an answer of an earlier one is
   * dropped. */
  const current = useRef(0);
  /** An older page is being read (one at a time). */
  const readingOlder = useRef(false);

  useEffect(() => {
    const token = ++current.current;
    readingOlder.current = false;
    setPage({ status: 'loading' });
    searchArchivedMovements(viewId, search, null, VIEWER_PAGE_SIZE).then(
      (answer) => {
        if (current.current !== token) return;
        setPage({
          status: 'ready',
          movements: answer.movements,
          total: answer.total,
          nextBefore: answer.hasMore ? answer.nextBeforeMovementId : null,
          loadingMore: false,
          moreError: null,
        });
      },
      (error: unknown) => {
        if (current.current !== token) return;
        if (viewNotOpen(error)) {
          onClosed(error.message);
          return;
        }
        setPage(
          error instanceof ApiError && error.status === 422
            ? { status: 'refused', message: error.message }
            : { status: 'error', message: errorMessage(error) },
        );
      },
    );
    return () => {
      current.current += 1;
    };
  }, [viewId, search, attempt, onClosed]);

  // A search that failed while the sign-in had ended runs again once the
  // same user's sign-in is usable (Administration's sign-in gate).
  const retrySignal = useContext(RetryFailedLoadsContext);
  const seenSignal = useRef(retrySignal);
  useEffect(() => {
    if (seenSignal.current === retrySignal) return;
    seenSignal.current = retrySignal;
    if (page.status === 'error') setAttempt((value) => value + 1);
  }, [retrySignal, page.status]);

  if (page.status === 'error') {
    return { ...page, retry: () => setAttempt((value) => value + 1) };
  }
  if (page.status !== 'ready') return page;
  const showOlder = () => {
    if (page.nextBefore === null || readingOlder.current) return;
    readingOlder.current = true;
    const token = current.current;
    const before = page.nextBefore;
    setPage({ ...page, loadingMore: true, moreError: null });
    searchArchivedMovements(viewId, search, before, VIEWER_PAGE_SIZE).then(
      (answer) => {
        if (current.current !== token) return;
        readingOlder.current = false;
        setPage((now) =>
          now.status !== 'ready'
            ? now
            : {
                ...now,
                movements: [...now.movements, ...answer.movements],
                total: answer.total,
                nextBefore: answer.hasMore ? answer.nextBeforeMovementId : null,
                loadingMore: false,
                moreError: null,
              },
        );
      },
      (error: unknown) => {
        if (current.current !== token) return;
        readingOlder.current = false;
        if (viewNotOpen(error)) {
          onClosed(error.message);
          return;
        }
        setPage((now) =>
          now.status !== 'ready'
            ? now
            : { ...now, loadingMore: false, moreError: errorMessage(error) },
        );
      },
    );
  };
  return { ...page, showOlder };
}

function SearchResults({
  results,
  searchEmpty,
  selectedFlowId,
  onToggleFlow,
}: {
  results: Results;
  searchEmpty: boolean;
  selectedFlowId: number | null;
  onToggleFlow: (event: MouseEvent<HTMLElement>, flowId: number) => void;
}) {
  if (results.status === 'loading') {
    return <LoadingState label="Searching the archived Movements" />;
  }
  if (results.status === 'refused') {
    return (
      <div className="err" role="alert">
        {results.message}
      </div>
    );
  }
  if (results.status === 'error') {
    return (
      <ErrorState
        message="The archived Movements could not be read."
        detail={results.message}
        onRetry={results.retry}
      />
    );
  }
  if (results.movements.length === 0) {
    return searchEmpty ? (
      <EmptyState message="This archive holds no Movements." />
    ) : (
      <EmptyState
        message="No archived Movement matches the search."
        hint="Every term must match, and a Work Order Number must be typed exactly as it is stored. Clear shows every Movement of the archive."
      />
    );
  }
  const total = results.total.toLocaleString('en-US');
  const one = results.total === 1;
  return (
    <>
      <p className="admin-arv-count" role="status">
        {searchEmpty
          ? `${total} archived Movement${one ? '' : 's'} in this archive.`
          : `${total} archived Movement${one ? ' matches' : 's match'} the search.`}
      </p>
      <ul className="mv history admin-arv-list">
        {results.movements.map((movement) => (
          // The whole row opens its flow's archived history (and closes it
          // again); its one button is the keyboard and screen-reader entry
          // point, whose activation reaches this same handler.
          <li
            key={movement.id}
            className={[
              movement.reversedByMovementId !== null ? 'reversed' : '',
              movement.quantityFlowId === selectedFlowId ? 'sel' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            onClick={(event) => onToggleFlow(event, movement.quantityFlowId)}
          >
            <MovementMarks movement={movement} />
            <span className="desc">
              <button
                type="button"
                className="admin-arv-rowbtn"
                aria-label={`Show the archived history of ${flowId(movement.quantityFlowId)} (${movement.partNumber})`}
                aria-pressed={movement.quantityFlowId === selectedFlowId}
              >
                <WholeCodes text={movement.partNumber} />
              </button>
              {' · '}
              <WholeCodes text={describeArchivedMovement(movement)} />
            </span>
          </li>
        ))}
      </ul>
      <div className="tk-paging">
        <span>
          Showing <b>{results.movements.length.toLocaleString('en-US')}</b> of{' '}
          <b>{total}</b> Movements
        </span>
        {results.nextBefore !== null ? (
          <button
            type="button"
            className="btn ghost"
            onClick={results.showOlder}
            disabled={results.loadingMore}
          >
            {results.loadingMore ? 'Loading…' : 'Show older Movements'}
          </button>
        ) : null}
        {results.moreError !== null ? (
          <span className="tk-error" role="alert">
            {results.moreError}
          </span>
        ) : null}
      </div>
    </>
  );
}

/** The time (always with its year: archived history is old), the
 * Movement type and its REPAIR and REVERSED badges, as Tracking shows
 * them. */
function MovementMarks({ movement }: { movement: ArchivedMovement }) {
  return (
    <>
      <span className="t">{archivedTimestamp(movement.occurredAt)}</span>
      <span className={`mtype ${movementTypeClass(movement.movementType)}`}>
        {movement.movementType}
      </span>
      {movement.movementReason === 'REPAIR' ? (
        <span className="mtype scr">REPAIR</span>
      ) : null}
      {movement.reversedByMovementId !== null ? (
        <span
          className="mtype rev"
          title={`Reversed by Movement #${movement.reversedByMovementId}`}
        >
          REVERSED
        </span>
      ) : null}
    </>
  );
}

type FlowAnswer =
  { kind: 'flow'; flow: ArchivedFlow } | { kind: 'gone'; message: string };

/** One archived flow in Tracking's modeless detail panel: its block (the
 * status and lineage, the route, what its record says), its splits and
 * merges as links to their flows, and its whole archived history. */
function ArchivedFlowPanel({
  viewId,
  id,
  source,
  focusHeading,
  onSelectFlow,
  onClose,
  onClosed,
}: {
  viewId: number;
  id: number;
  /** `run N` (or `view N`): what the mark names. */
  source: string;
  focusHeading: boolean;
  onSelectFlow: (id: number) => void;
  onClose: () => void;
  onClosed: (message: string) => void;
}) {
  const load = useCallback(
    () =>
      loadArchivedFlow(viewId, id).then(
        (flow): FlowAnswer => ({ kind: 'flow', flow }),
        (error: unknown): FlowAnswer => {
          if (viewNotOpen(error))
            return { kind: 'gone', message: error.message };
          throw error;
        },
      ),
    [viewId, id],
  );
  const data = useApiData(load);
  const answer = data.state.status === 'ready' ? data.state.data : null;
  const gone = answer?.kind === 'gone' ? answer.message : null;
  useEffect(() => {
    if (gone !== null) onClosed(gone);
  }, [gone, onClosed]);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusHeading) headingRef.current?.focus();
  }, [focusHeading]);
  const flow = answer?.kind === 'flow' ? answer.flow : null;

  return (
    <aside
      className="tk-right admin-arv-flow"
      aria-label={`Archived history of ${flowId(id)}`}
    >
      <div className="tk-pnrow">
        <div>
          <div className="admin-arv-flowmark">
            {`Archived history from ${source} · read-only`}
          </div>
          <h2 ref={headingRef} tabIndex={-1}>
            {flowId(id)}
          </h2>
          {flow !== null ? (
            <div className="jsub">
              <b>
                <WholeCodes text={flow.partNumber} />
              </b>
              {' · '}
              {flow.partNumberName ?? (
                <>
                  name <b>—</b>
                </>
              )}
            </div>
          ) : null}
        </div>
        <span className="spacer" />
        <button
          type="button"
          className="tk-close"
          aria-label="Close details"
          title="Close details"
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      {data.state.status === 'loading' ? (
        <LoadingState label="Loading the archived history of the flow" />
      ) : null}
      {data.state.status === 'error' ? (
        <ErrorState
          message="The archived history of this flow could not be loaded."
          detail={data.state.message}
          onRetry={data.reload}
        />
      ) : null}
      {flow !== null ? (
        <ArchivedFlowContent flow={flow} onSelectFlow={onSelectFlow} />
      ) : null}
    </aside>
  );
}

function ArchivedFlowContent({
  flow,
  onSelectFlow,
}: {
  flow: ArchivedFlow;
  onSelectFlow: (id: number) => void;
}) {
  const planned = flow.routeMode === 'PLANNED';
  const steps = planned && flow.route !== null ? flow.route.steps : [];
  const links = [
    ...flow.parents.map((link) => ({ ...link, way: 'from' as const })),
    ...flow.children.map((link) => ({ ...link, way: 'into' as const })),
  ];
  return (
    <>
      <div className="tk-sec">
        <h4>
          Quantity Flow <span className="tag">as archived</span>
        </h4>
        <div className="qflow">
          <div className="qf-head">
            <span className="qf-id">{flowId(flow.id)}</span>
            <span className="qf-q">{flow.quantity} pcs</span>
            {flow.routeMode === 'PLANNED' || flow.routeMode === 'FLOATING' ? (
              <RouteModeChip
                mode={flow.routeMode}
                detail={planned ? 'snapshot' : 'actual trace'}
              />
            ) : (
              <span className="qf-pos">{flow.routeMode}</span>
            )}
            <span className="qf-pos">
              <WholeCodes text={flowStatusLine(flow)} />
            </span>
          </div>
          {steps.length > 0 ? (
            <div className="route">
              {/* Steps and arrows are separate sibling flex items, as in
                  Tracking; the snapshot's steps carry no state here — the
                  history below is the record. */}
              {steps.flatMap((step, index) => {
                const node = (
                  <span
                    key={`step-${step.id}`}
                    className="rstep"
                    title={
                      step.operation === null
                        ? undefined
                        : operationName(step.operation)
                    }
                  >
                    {areaName(step.area)}
                  </span>
                );
                if (index === 0) return [node];
                return [
                  <span
                    key={`arrow-${step.id}`}
                    className="rarrow"
                    aria-hidden="true"
                  >
                    →
                  </span>,
                  node,
                ];
              })}
            </div>
          ) : null}
          <div className="devnote">
            {planned
              ? 'Planned Route (snapshot) — guidance only; the archived Movement history below stays authoritative.'
              : 'Floating Route — its actual trace is the archived Movement history below.'}
          </div>
          {flowRecordLines(flow).map((line) => (
            <div className="devnote archived" key={line}>
              <WholeCodes text={line} />
            </div>
          ))}
        </div>
      </div>
      {links.length > 0 ? (
        <div className="tk-sec">
          <h4>
            Lineage <span className="tag">splits and merges</span>
          </h4>
          <ul className="admin-arv-lineage">
            {links.map((link) => (
              <li key={`${link.way}-${link.quantityFlowId}`}>
                {`${link.relation} ${link.way} `}
                <button
                  type="button"
                  className="admin-arv-flowlink"
                  aria-label={`Show the archived history of ${flowId(link.quantityFlowId)}`}
                  onClick={() => onSelectFlow(link.quantityFlowId)}
                >
                  {flowId(link.quantityFlowId)}
                </button>
                {link.void ? ' (undone)' : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="tk-sec">
        <h4>
          Movement history{' '}
          <span className="tag">its whole archived history</span>
        </h4>
        <ul className="mv history">
          {flow.movements.map((movement) => (
            <li
              key={movement.id}
              className={
                movement.reversedByMovementId !== null ? 'reversed' : ''
              }
            >
              <MovementMarks movement={movement} />
              <span className="desc">
                <WholeCodes text={describeArchivedMovement(movement)} />
              </span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
