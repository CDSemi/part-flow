/** Deterministic loading representation: labelled skeleton panels. */
export function LoadingState({ label }: { label: string }) {
  return (
    <div className="state-loading" role="status" aria-label={label}>
      <span className="skeleton" style={{ height: 34, maxWidth: 420 }} />
      <span className="skeleton" style={{ height: 120 }} />
      <span className="skeleton" style={{ height: 120 }} />
      <span className="skeleton" style={{ height: 80, maxWidth: 640 }} />
    </div>
  );
}

/** Deterministic empty representation. */
export function EmptyState({
  message,
  hint,
}: {
  message: string;
  hint?: string;
}) {
  return (
    <div className="state-empty">
      <div>{message}</div>
      {hint ? <div style={{ marginTop: 6 }}>{hint}</div> : null}
    </div>
  );
}

/** Deterministic error representation with actionable text. */
export function ErrorState({
  message,
  detail,
  onRetry,
}: {
  message: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-error" role="alert">
      <div>{message}</div>
      {detail ? <div className="detail">{detail}</div> : null}
      {onRetry ? (
        <button
          className="btn ghost"
          style={{ marginTop: 14 }}
          onClick={onRetry}
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
