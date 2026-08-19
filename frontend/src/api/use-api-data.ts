// Shared read-model loading hook for the real Phase 3.5 views.
//
// One deliberately small pattern instead of a data library: load once
// per loader identity, expose loading / error / ready, and offer an
// explicit reload (after a completed write, or as the user-facing
// Retry of an error state). A generation counter discards stale
// results, so an unmounted view or a superseded reload never applies.
//
// Pass a stable loader: a module-level function for parameterless
// lists, or a `useCallback` wrapping the parameters.

import { useCallback, useEffect, useRef, useState } from 'react';

import { errorMessage } from './client';

export type ApiDataState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T };

export interface ApiData<T> {
  state: ApiDataState<T>;
  /** Re-run the loader (keeps showing the current data while
   * refreshing; a failed refresh becomes the error state). */
  reload: () => void;
}

export function useApiData<T>(load: () => Promise<T>): ApiData<T> {
  const [state, setState] = useState<ApiDataState<T>>({ status: 'loading' });
  const [generation, setGeneration] = useState(0);
  const liveGeneration = useRef(0);

  useEffect(() => {
    const requested = ++liveGeneration.current;
    let cancelled = false;
    void load().then(
      (data) => {
        if (!cancelled && liveGeneration.current === requested) {
          setState({ status: 'ready', data });
        }
      },
      (error: unknown) => {
        if (!cancelled && liveGeneration.current === requested) {
          setState({ status: 'error', message: errorMessage(error) });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, generation]);

  const reload = useCallback(() => setGeneration((value) => value + 1), []);
  return { state, reload };
}
