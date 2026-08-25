// Development-only UI state preview.
//
// Append `?state=loading|empty|error|long` to a view URL in a
// development build to force a deterministic state. The parser accepts
// all four values on every route; each view implements only the
// preview states that are meaningful for it. `loading`, `empty` and
// `error` are broadly supported; `long` swaps in a deterministic
// long-data fixture only on the data-heavy views that define one
// (e.g. Tracking, Work Orders, Machines, the Production Board) — a
// view without such a fixture (e.g. Administration, Priority) simply
// renders its normal sample data. The check is behind
// `import.meta.env.DEV`, so production builds compile the override
// away and never expose it.

export type ViewStatePreview = 'loading' | 'empty' | 'error' | 'long' | null;

const PREVIEW_STATES = ['loading', 'empty', 'error', 'long'] as const;

export function getViewStatePreview(): ViewStatePreview {
  if (!import.meta.env.DEV) return null;
  const value = new URLSearchParams(window.location.search).get('state');
  return (PREVIEW_STATES as readonly string[]).includes(value ?? '')
    ? (value as ViewStatePreview)
    : null;
}

/**
 * Development-only mock preview of a real view's LATER-phase workflows.
 *
 * The Scan Station is a real view since Phase 5 (transfer to an Area
 * queue), while its approved Phase 6+ one-shot workflows (Machine
 * assignment, DONE / QUEUE, Repair, Scrap, Undo, Worker sessions) still
 * exist only as the mock preview. `?preview=mock` on a Scan Station
 * route opts a development build into that preview; the choice is
 * remembered for the browser session so the preview's own navigation
 * (station selection, the Ctrl+Shift+K mode switch) stays inside it.
 * Production builds compile the check away and never expose it.
 */
const MOCK_PREVIEW_KEY = 'partflow.dev.mock-preview';

export function isMockPreviewRequested(): boolean {
  if (!import.meta.env.DEV) return false;
  const param = new URLSearchParams(window.location.search).get('preview');
  try {
    if (param === 'mock') {
      window.sessionStorage.setItem(MOCK_PREVIEW_KEY, 'mock');
      return true;
    }
    if (param === 'real') {
      window.sessionStorage.removeItem(MOCK_PREVIEW_KEY);
      return false;
    }
    return window.sessionStorage.getItem(MOCK_PREVIEW_KEY) === 'mock';
  } catch {
    return param === 'mock';
  }
}
