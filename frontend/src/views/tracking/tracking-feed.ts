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

import { useCallback, useEffect, useState } from 'react';

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
  /** The keyset of the next page; null once the last page arrived
   * (meaningful once a page was appended — see `loaded`). */
  nextBefore: number | null;
  /** Pages appended so far (0 while the first older page is loading). */
  loaded: number;
  loading: boolean;
  error: string | null;
}

interface OlderState<T> extends OlderPages<T> {
  /** The generation of the first page the pages continue below. */
  generation: string;
  /** The depth (pages) the reader asked for. */
  wanted: number;
}

/**
 * Continuation of a paged section below the FIRST page the detail feed
 * delivered.
 *
 * `boundary` is that page's keyset (`next_before_*`): the older pages
 * continue below it. `revision` is the section's revision signature —
 * the figures of the polled detail whose change means rows already
 * appended may read differently now (a flow closed or reopened, a
 * scrap undone, a new row inserted below the boundary), even when the
 * boundary itself did not move. When either changes, the appended
 * pages are dropped and the same depth is read again below the new
 * boundary, so the section never shows a gap, a duplicate or a stale
 * row; a refresh that changes neither keeps the pages as they are.
 */
export function useOlderPages<T>(
  boundary: number | null,
  revision: string,
  load: (before: number) => Promise<{ items: T[]; nextBefore: number | null }>,
): {
  older: OlderPages<T> | null;
  /** Load the next older page (a no-op while one is loading). */
  showOlder: () => void;
} {
  const generation = `${boundary ?? 'none'}|${revision}`;
  const [state, setState] = useState<OlderState<T> | null>(null);
  const current =
    state !== null && state.generation === generation ? state : null;

  useEffect(() => {
    if (state === null) return;
    if (state.generation !== generation) {
      // The first page changed under the appended pages: start again
      // below the new boundary, to the depth the reader had reached.
      setState(
        boundary !== null && state.wanted > 0
          ? {
              generation,
              items: [],
              nextBefore: null,
              loading: false,
              error: null,
              loaded: 0,
              wanted: state.wanted,
            }
          : null,
      );
      return;
    }
    if (state.loading || state.error !== null || state.loaded >= state.wanted)
      return;
    const before = state.loaded === 0 ? boundary : state.nextBefore;
    if (before === null) return;
    setState({ ...state, loading: true });
    void load(before).then(
      (page) =>
        setState((latest) =>
          latest === null || latest.generation !== generation
            ? latest
            : {
                ...latest,
                items: [...latest.items, ...page.items],
                nextBefore: page.nextBefore,
                loading: false,
                loaded: latest.loaded + 1,
              },
        ),
      (error: unknown) =>
        setState((latest) =>
          latest === null || latest.generation !== generation
            ? latest
            : { ...latest, loading: false, error: errorMessage(error) },
        ),
    );
  }, [state, generation, boundary, load]);

  const showOlder = useCallback(() => {
    if (boundary === null) return;
    setState((latest) => {
      const base: OlderState<T> =
        latest !== null && latest.generation === generation
          ? latest
          : {
              generation,
              items: [],
              nextBefore: null,
              loading: false,
              error: null,
              loaded: 0,
              wanted: 0,
            };
      if (base.loading || (base.loaded > 0 && base.nextBefore === null))
        return latest;
      return { ...base, wanted: base.loaded + 1, error: null };
    });
  }, [boundary, generation]);

  return { older: current, showOlder };
}
