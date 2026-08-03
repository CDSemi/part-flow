import { useConnectivity } from '../app/connectivity-context';

/**
 * Compact backend-connectivity status chip: explicit text, never color
 * alone. Rendered in the top application navigation, and inside the
 * Scan Station header in production mode (where the top navigation is
 * hidden but connectivity status must stay visible).
 */
export function ConnectivityChip() {
  const { status } = useConnectivity();
  const text =
    status === 'connected'
      ? 'ONLINE'
      : status === 'connecting'
        ? 'CONNECTING…'
        : 'OFFLINE';
  return (
    <span
      className={`connchip ${status === 'unavailable' ? 'off' : status === 'connecting' ? 'connecting' : ''}`}
      role="status"
      aria-label={`Backend connection: ${text}`}
    >
      <span className="cdot" aria-hidden="true" />
      <span className="ctxt">{text}</span>
    </span>
  );
}
