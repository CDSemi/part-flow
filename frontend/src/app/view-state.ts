// Development-only UI state preview.
//
// Append `?state=loading|empty|error|long` to any view URL in a
// development build to force that view's deterministic state. The check
// is behind `import.meta.env.DEV`, so production builds compile the
// override away and never expose it.

export type ViewStatePreview = 'loading' | 'empty' | 'error' | 'long' | null;

const PREVIEW_STATES = ['loading', 'empty', 'error', 'long'] as const;

export function getViewStatePreview(): ViewStatePreview {
  if (!import.meta.env.DEV) return null;
  const value = new URLSearchParams(window.location.search).get('state');
  return (PREVIEW_STATES as readonly string[]).includes(value ?? '')
    ? (value as ViewStatePreview)
    : null;
}
