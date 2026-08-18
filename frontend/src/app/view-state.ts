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
