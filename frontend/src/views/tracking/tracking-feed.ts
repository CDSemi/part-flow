// PN Tracking feeds: the polling reads of `GET /api/tracking` (the
// list) and `GET /api/tracking/detail` (the open PN).
//
// Both follow the shared monitoring feed (views/monitoring-feed), the
// same behaviour the Production Board and the Area Board read through:
// one request at a time, a failed refresh keeping the last COMPLETE
// answer with the feed marked stale, a failed FIRST load as the error
// state with Retry, and an immediate refresh when connectivity returns.
// A Management view is read while work moves on the floor, so it
// follows the feed rather than waiting for a manual reload.

import { useCallback, useState } from 'react';

import { errorMessage } from '../../api/client';
import type { Area, Operation } from '../../api/environment';
import { listAreas, listOperations } from '../../api/environment';
import type { Machine } from '../../api/machines';
import { listMachines } from '../../api/machines';
import type { TrackingDetail, TrackingPage } from '../../api/tracking';
import {
  loadTrackingDetail,
  loadTrackingListByQuery,
} from '../../api/tracking';
import { useApiData } from '../../api/use-api-data';
import type { ConnectivityStatus } from '../../app/connectivity-context';
import type { MonitoringFeed } from '../monitoring-feed';
import { useMonitoringFeed } from '../monitoring-feed';
import { MOVEMENTS_PAGE_SIZE, TRACKING_REFRESH_MS } from './tracking-logic';

/**
 * The list feed. The loader identity is the QUERY the filters produce
 * (`trackingListQuery`), so two filter states that reach the server
 * identically never re-read, while any effective change reads at once.
 */
export function useTrackingListFeed(
  query: string,
  connectivity: ConnectivityStatus,
  enabled = true,
): MonitoringFeed<TrackingPage> {
  const load = useCallback(() => loadTrackingListByQuery(query), [query]);
  return useMonitoringFeed(load, TRACKING_REFRESH_MS, connectivity, enabled);
}

/** The detail feed of the selected PN (`enabled: false` while none). */
export function useTrackingDetailFeed(
  pn: string | null,
  connectivity: ConnectivityStatus,
  enabled = true,
): MonitoringFeed<TrackingDetail> {
  const load = useCallback(
    () =>
      pn === null
        ? Promise.reject(new Error('No Part Number selected.'))
        : loadTrackingDetail(pn, MOVEMENTS_PAGE_SIZE),
    [pn],
  );
  return useMonitoringFeed(
    load,
    TRACKING_REFRESH_MS,
    connectivity,
    enabled && pn !== null,
  );
}

/** The choices of the Area / Operation / Machine filter selects. */
export interface TrackingFilterOptions {
  areas: Pick<Area, 'id' | 'name'>[];
  operations: Pick<Operation, 'id' | 'code' | 'name'>[];
  machines: Pick<Machine, 'id' | 'name'>[];
}

const NO_OPTIONS: TrackingFilterOptions = {
  areas: [],
  operations: [],
  machines: [],
};

async function loadFilterOptions(): Promise<TrackingFilterOptions> {
  const [areas, operations, machines] = await Promise.all([
    listAreas(),
    listOperations(),
    listMachines(),
  ]);
  return {
    areas: areas.filter((area) => area.isActive),
    operations: operations.filter((operation) => operation.isActive),
    // Retired Machines hold no quantity; only active ones can filter.
    machines: machines.filter((machine) => machine.retiredOn === undefined),
  };
}

const loadNoOptions = () => Promise.resolve(NO_OPTIONS);

/**
 * The filter choices, read once. A failed read leaves the selects at
 * `All` — the list itself is unaffected, and the choices reload with
 * the view.
 */
export function useTrackingFilterOptions(
  enabled = true,
): TrackingFilterOptions {
  const { state } = useApiData(enabled ? loadFilterOptions : loadNoOptions);
  return state.status === 'ready' ? state.data : NO_OPTIONS;
}

/** Older pages of one paged detail section, appended on request. */
export interface OlderPages<T> {
  items: T[];
  /** The keyset of the next page; null once the last page arrived. */
  nextBefore: number | null;
  loading: boolean;
  error: string | null;
}

/**
 * Continuation of a paged section below the FIRST page the detail feed
 * delivered. `boundary` is that page's keyset (`next_before_*`): the
 * older pages continue below it, and when a refresh moves it (new rows
 * arrived on the first page) the appended pages are dropped and start
 * again, so the section never shows a gap or a duplicate.
 */
export function useOlderPages<T>(
  boundary: number | null,
  load: (before: number) => Promise<{ items: T[]; nextBefore: number | null }>,
): {
  older: OlderPages<T> | null;
  /** Load the next older page (a no-op while one is loading). */
  showOlder: () => void;
} {
  const [state, setState] = useState<
    (OlderPages<T> & { boundary: number }) | null
  >(null);
  const current = state !== null && state.boundary === boundary ? state : null;

  const showOlder = useCallback(() => {
    if (boundary === null || current?.loading) return;
    const before = current?.nextBefore ?? boundary;
    if (current !== null && current.nextBefore === null) return;
    setState({
      boundary,
      items: current?.items ?? [],
      nextBefore: current?.nextBefore ?? null,
      loading: true,
      error: null,
    });
    void load(before).then(
      (page) =>
        setState((latest) =>
          latest === null || latest.boundary !== boundary
            ? latest
            : {
                ...latest,
                items: [...latest.items, ...page.items],
                nextBefore: page.nextBefore,
                loading: false,
              },
        ),
      (error: unknown) =>
        setState((latest) =>
          latest === null || latest.boundary !== boundary
            ? latest
            : { ...latest, loading: false, error: errorMessage(error) },
        ),
    );
  }, [boundary, current, load]);

  return { older: current, showOlder };
}
