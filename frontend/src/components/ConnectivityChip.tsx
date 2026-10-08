import { useConnectivity } from '../app/connectivity-context';

const CHIP_TEXT = {
  connected: 'ONLINE',
  connecting: 'CONNECTING…',
  unavailable: 'OFFLINE',
  outdated: 'UPDATED',
} as const;

const CHIP_CLASS = {
  connected: '',
  connecting: 'connecting',
  unavailable: 'off',
  outdated: 'outdated',
} as const;

/**
 * Compact backend-connectivity status chip: explicit text, never color
 * alone. Rendered in the top application navigation, and inside the
 * Scan Station header in production mode (where the top navigation is
 * hidden but connectivity status must stay visible). `UPDATED`: the
 * server runs another release than this page (GUI_DESIGN §3 rule 13).
 */
export function ConnectivityChip() {
  const { status } = useConnectivity();
  const text = CHIP_TEXT[status];
  return (
    <span
      className={`connchip ${CHIP_CLASS[status]}`}
      role="status"
      aria-label={`Backend connection: ${text}`}
    >
      <span className="cdot" aria-hidden="true" />
      <span className="ctxt">{text}</span>
    </span>
  );
}
