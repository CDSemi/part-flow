// Scan Station presentation primitives shared by the real Scan Station
// view (Phase 5) and the development-only mock preview of the later
// workflows: notices, the confirmation summary, header Operations,
// guidance, step recaps, wizard buttons, quantity-step key handling,
// and the manual PN entry dialog. Production-safe — no mock imports
// (verified by src/production-boundary.test.ts).

import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';

import { useRouter } from '../../app/router-context';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { normalizePartNumber } from './barcode';
import { GUIDE_MARKERS, parseWorkOrderLabel } from './scan-station-wizard';

/**
 * One floating scan notification. Success/info notices auto-dismiss
 * after ~4 s, warnings and errors after ~8 s; a new notice replaces
 * the previous one and restarts the timer. The persistent OFFLINE
 * application banner is NOT a notice — it stays until reconnection.
 */
export type Notice = {
  kind: 'ok' | 'warn' | 'err' | 'info';
  icon?: string;
  title: string;
  detail?: string;
};

/**
 * Supported Operations as individual light informational chips — the
 * same presentation the Scan Station header uses (shared `.opchips` /
 * `.opchip` styling). Labels, not controls: no action color, no
 * button-like hover, wrapping cleanly for multi-Operation Areas.
 */
export function OperationChips({
  operations,
}: {
  operations: readonly string[];
}) {
  if (operations.length === 0) return <>—</>;
  return (
    <span className="opchips">
      {operations.map((op) => (
        <span className="opchip" key={op}>
          {op}
        </span>
      ))}
    </span>
  );
}
export function UnknownStation({
  stationId,
  detail,
  onRetry,
}: {
  stationId: string;
  /** Server-provided reason (real view); the generic sentence otherwise. */
  detail?: string;
  /** Re-check the station (real view) — the error may be transient. */
  onRetry?: () => void;
}) {
  const { navigate } = useRouter();
  return (
    <section className="ss" aria-label="Scan Station">
      <div className="ss-select">
        <div className="ss-feedback err" role="alert">
          <div className="fic" aria-hidden="true">
            ✕
          </div>
          <div>
            <div className="t1">Scan Station “{stationId}” is unavailable</div>
            <div className="t2">
              {detail ??
                'The Station ID is invalid or inactive. Select an available Scan Station to continue.'}
            </div>
          </div>
        </div>
        <div className="row">
          {onRetry ? (
            <button className="bigbtn ghost" onClick={onRetry}>
              Retry
            </button>
          ) : null}
          <button
            className="bigbtn primary"
            onClick={() => navigate('/scan-station')}
          >
            Select another Scan Station
          </button>
        </div>
      </div>
    </section>
  );
}
/**
 * Development-only clickable demo barcode inside the DevNotice. The
 * button is an invisible wrapper around the shared code chip — the
 * value keeps its `<code>` presentation, hover/focus reveal the
 * affordance (scan-station.css). A click feeds the value through the
 * EXACT scanner path (`onScan` → main input + `handleScan()`); there
 * is no parallel demo scan flow.
 */
export function DemoBarcode({
  value,
  onScan,
}: {
  value: string;
  onScan: (value: string) => void;
}) {
  return (
    <button
      type="button"
      className="ss-demobarcode"
      title="Click to simulate scan"
      onClick={() => onScan(value)}
    >
      <code>{value}</code>
    </button>
  );
}
/* ------------------------------------------------------------------ */
/* Floating notification                                               */
/* ------------------------------------------------------------------ */

/**
 * The single floating scan notification. It never reserves layout
 * space, shows only the most recent notice, carries an explicit close
 * button, and stays clear of the barcode input (bottom edge) and of
 * dialog actions (it renders beneath the modal overlay).
 */
export function FloatingNotice({
  notice,
  onClose,
}: {
  notice: Notice;
  onClose: () => void;
}) {
  const alerting = notice.kind === 'warn' || notice.kind === 'err';
  return (
    <div
      className={`ss-toast ${notice.kind}`}
      role={alerting ? 'alert' : 'status'}
    >
      {notice.icon ? (
        <div className="fic" aria-hidden="true">
          {notice.icon}
        </div>
      ) : null}
      <div className="tx">
        <div className="t1">{notice.title}</div>
        {notice.detail ? <div className="t2">{notice.detail}</div> : null}
      </div>
      <button
        type="button"
        className="tclose"
        aria-label="Dismiss notification"
        onClick={onClose}
      >
        ×
      </button>
    </div>
  );
}
/** Optional row emphasis for the shared confirmation summary. */
export type SummaryEmphasis = 'primary' | 'secondary';

/**
 * Controlled semantic tone for a summary VALUE (v15). Additive only:
 * emphasis (weight/size) still carries the hierarchy, so tone is never
 * the only distinction. `ok` marks a successful/recorded result,
 * `warn` a deviation worth noticing, `err` a destructive result.
 * Audit context (Worker, Scan Station, timestamps) stays `secondary`
 * muted; Area identity is shown with an AreaDot beside plain text, not
 * by recoloring the text (area hues are not readable as body text in
 * both themes).
 */
export type SummaryTone = 'ok' | 'warn' | 'err';

export type SummaryRow = [
  string,
  ReactNode,
  (SummaryEmphasis | undefined)?,
  (SummaryTone | undefined)?,
];

/**
 * Structured confirmation summary — the dedicated final view of every
 * production wizard. Two columns (term / value); rows without a value
 * are omitted. Never a single interpolated sentence.
 *
 * A small optional row-emphasis mechanism keeps the primary
 * operational values (PN, Quantity, Action, Source, Destination,
 * Machine, Area, Operation, Reason) easy to scan and quiets the
 * secondary audit/context rows (Worker, Scan Station, recorded event
 * names, explanatory notes) — nothing is hidden, labels never look
 * like buttons, and the distinction never relies on color alone
 * (weight and size carry it in both themes; the optional value tone is
 * additive on top).
 */
export function ConfirmationSummary({ rows }: { rows: SummaryRow[] }) {
  return (
    <dl className="ss-confirm">
      {rows
        .filter((row) => row[1] !== null && row[1] !== undefined)
        .map(([label, value, emphasis, tone]) => (
          <Fragment key={label}>
            <dt className={emphasis ?? ''}>{label}</dt>
            <dd className={`${emphasis ?? ''}${tone ? ` tone-${tone}` : ''}`}>
              {value}
            </dd>
          </Fragment>
        ))}
    </dl>
  );
}
/**
 * Subtle non-interactive chip for a selected entity (Machine, Area,
 * Operation, source, Route Mode) inside recaps and confirmation
 * summaries — a label, never a control.
 */
export function EntityChip({ children }: { children: ReactNode }) {
  return <span className="dlgchip">{children}</span>;
}
/**
 * Header Operations row (§4.3) — always one line, fit-measured against
 * the identity cell (never a hard-coded breakpoint), shedding
 * presentation in tiers only when the full `Operations: <chips>` line
 * genuinely cannot fit: first the `Operations:` label is dropped
 * (chips only), then trailing chips are replaced by one `…` chip, and
 * when not even the first chip fits the row hides entirely. A hidden
 * natural-width ghost copy (label + every chip) provides the
 * measurements — and stands in for the visible row during the header
 * fit probe, so the Area totals still drop to their second row before
 * the Operations row ever sheds anything.
 */
export function HeaderOperations({
  operations,
}: {
  operations: readonly string[];
}) {
  const ghostRef = useRef<HTMLDivElement>(null);
  // `label`: the `Operations:` prefix is visible; `chips`: how many
  // chips render (operations.length = all of them, no `…` chip).
  const [fit, setFit] = useState({ label: true, chips: operations.length });
  const measure = useCallback(() => {
    const ghost = ghostRef.current;
    const cell = ghost?.parentElement; // the .ss-id identity cell
    if (!ghost || !cell) return;
    const available = cell.clientWidth;
    const rowGap = Number.parseFloat(getComputedStyle(ghost).columnGap) || 0;
    const chipsWrap = ghost.querySelector('.opchips');
    const chipGap = chipsWrap
      ? Number.parseFloat(getComputedStyle(chipsWrap).columnGap) || 0
      : 0;
    const width = (el: Element | null) =>
      el ? el.getBoundingClientRect().width : 0;
    const labelWidth = width(ghost.querySelector('.oplabel'));
    const moreWidth = width(ghost.querySelector('.opchip-more'));
    const chipWidths = Array.from(
      ghost.querySelectorAll('.opchip:not(.opchip-more)'),
      width,
    );
    const chipsWidth = (count: number) =>
      chipWidths.slice(0, count).reduce((sum, w) => sum + w, 0) +
      chipGap * Math.max(0, count - 1);
    const all = chipWidths.length;
    // Half-pixel tolerance so subpixel rounding never flips a tier.
    const fits = (required: number) => required <= available + 0.5;
    let next: { label: boolean; chips: number };
    if (fits(labelWidth + rowGap + chipsWidth(all))) {
      next = { label: true, chips: all };
    } else if (fits(chipsWidth(all))) {
      next = { label: false, chips: all };
    } else {
      let count = all - 1;
      while (count > 0 && !fits(chipsWidth(count) + chipGap + moreWidth)) {
        count -= 1;
      }
      next = { label: false, chips: count };
    }
    setFit((current) =>
      current.label === next.label && current.chips === next.chips
        ? current
        : next,
    );
  }, []);
  useLayoutEffect(() => {
    measure();
  }, [measure, operations]);
  useEffect(() => {
    window.addEventListener('resize', measure);
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => measure());
      const cell = ghostRef.current?.parentElement;
      if (cell) observer.observe(cell);
    }
    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, [measure]);
  const truncated = fit.chips < operations.length;
  return (
    <>
      {(fit.chips > 0 || !truncated) && (
        <div className="op">
          {fit.label && 'Operations:'}
          <span className="opchips">
            {operations.slice(0, fit.chips).map((op) => (
              <span className="opchip" key={op}>
                {op}
              </span>
            ))}
            {truncated && (
              <span
                className="opchip opchip-more"
                title={operations.slice(fit.chips).join(', ')}
              >
                …
              </span>
            )}
          </span>
        </div>
      )}
      <div className="op-ghost" aria-hidden="true" ref={ghostRef}>
        <span className="oplabel">Operations:</span>
        <span className="opchips">
          {operations.map((op) => (
            <span className="opchip" key={op}>
              {op}
            </span>
          ))}
          <span className="opchip opchip-more">…</span>
        </span>
      </div>
    </>
  );
}

/**
 * Semantic quantity/step guidance directly above the related input or
 * choice. Four kinds only (§3.10, v15 — the former marker-less
 * `neutral` kind is retired): instructions and information are `info`,
 * important constraints and deviations are `warn`, required next
 * actions are `action`, and validation errors are `error`. Every kind
 * carries a small marker plus an accent edge — color is never the
 * only distinction, and validation reads stronger than any
 * instruction. Deliberately light: never a large framed card.
 */
export function Guidance({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn' | 'action' | 'error';
  children: ReactNode;
}) {
  return (
    <div className={`ss-guide ${tone}`}>
      <span className="gmark" aria-hidden="true">
        {GUIDE_MARKERS[tone]}
      </span>
      <span className="gtext">{children}</span>
    </div>
  );
}

/**
 * Work Order context line for recaps: `WO` stays a plain label, the
 * WO Number value carries the shared `.rval` emphasis (`—` for an
 * internal blank number), the Operation renders as the shared entity
 * chip, and a NEW/MODIFY segment stays the shared Request Type chip.
 */
export function WorkOrderRecapLine({ workOrder }: { workOrder: string }) {
  const { number, segments } = parseWorkOrderLabel(workOrder);
  return (
    <>
      WO <b className="rval">{number}</b>
      {segments.map((segment, index) => (
        <Fragment key={`${segment}-${index}`}>
          {' · '}
          {segment === 'NEW' || segment === 'MODIFY' ? (
            <TypeChip type={segment} />
          ) : (
            <EntityChip>{segment}</EntityChip>
          )}
        </Fragment>
      ))}
    </>
  );
}
/** Concise recap of the selections carried into the current step. */
export function StepRecap({ lines }: { lines: ReactNode[] }) {
  return (
    <div className="ss-recap">
      {lines.filter(Boolean).map((line, index) => (
        <div key={index} className="ss-recapline">
          {line}
        </div>
      ))}
    </div>
  );
}
/**
 * Wizard navigation row: Back only when a meaningful previous view
 * exists, Cancel (Esc) always abandons the whole one-shot workflow
 * with no write (the standard label is exactly `Cancel (Esc)`), and
 * the primary button names the actual operation.
 */
export function StepButtons({
  onBack,
  onCancel,
  cancelLabel = 'Cancel (Esc)',
  primary,
}: {
  onBack?: () => void;
  onCancel: () => void;
  cancelLabel?: string;
  primary: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    danger?: boolean;
    autoFocus?: boolean;
  };
}) {
  const primaryRef = useRef<HTMLButtonElement>(null);
  const { autoFocus } = primary;
  useEffect(() => {
    if (autoFocus) primaryRef.current?.focus();
  }, [autoFocus]);
  return (
    <div className="row">
      {onBack ? (
        <button className="bigbtn ghost ss-back" onClick={onBack}>
          ‹ Back
        </button>
      ) : null}
      <button className="bigbtn ghost" onClick={onCancel}>
        {cancelLabel}
      </button>
      <button
        ref={primaryRef}
        className={`bigbtn ${primary.danger ? 'danger' : 'primary'}`}
        disabled={primary.disabled}
        onClick={primary.onClick}
      >
        {primary.label}
      </button>
    </div>
  );
}
/** Standard quantity validation message for a 1..max range: the range
 *  prompt is a required action; exceeding the limit is a validation
 *  error and reads visually stronger. */
export function ManualEntryDialog({
  initialPn,
  examplePn,
  onCancel,
  onConfirm,
}: {
  /** Previously entered PN, preserved when Back returns here. */
  initialPn?: string;
  /** Example PN shown in the placeholder (`e.g. …`). */
  examplePn: string;
  onCancel: () => void;
  /** Called with the canonical PN, or '' when the entry was empty. */
  onConfirm: (pn: string) => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [entryError, setEntryError] = useState<string | null>(null);
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);
  // Normalize to the canonical uppercase PN before resolving:
  // surrounding whitespace trims away; a value with INTERNAL whitespace
  // is invalid and stays in the dialog with an explanation — it is
  // never silently cleaned up into a valid PN.
  function submit() {
    const raw = fieldRef.current?.value ?? '';
    if (raw.trim() === '') {
      onConfirm('');
      return;
    }
    const pn = normalizePartNumber(raw);
    if (!pn) {
      setEntryError(
        'A Part Number cannot contain spaces, tabs, or other whitespace inside the value. Correct the entry and try again.',
      );
      return;
    }
    onConfirm(pn);
  }
  return (
    <ModalDialog label="Enter Part Number manually" onClose={onCancel}>
      <h3>Enter Part Number manually</h3>
      {/* Operator wording only — engineering detail: the entry is
          normalized to the canonical uppercase, whitespace-free PN; an
          unknown PN opens the intake wizard, where the PartNumber
          master metadata record is created on first valid use. */}
      <div className="sub">
        Enter the Part Number exactly as shown on the traveler or job paperwork.
        Lowercase letters are accepted and shown in uppercase. Unknown Part
        Numbers will open the receive workflow for review. Nothing is recorded
        at this step.
      </div>
      <input
        aria-label="Part Number"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        defaultValue={initialPn}
        placeholder={`Part Number, e.g. ${examplePn}`}
        onChange={() => setEntryError(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
        }}
      />
      {entryError ? <Guidance tone="error">{entryError}</Guidance> : null}
      <StepButtons
        onCancel={onCancel}
        primary={{
          label: 'Continue',
          onClick: submit,
        }}
      />
    </ModalDialog>
  );
}
