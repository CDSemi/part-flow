import './area-monitoring.css';

import type { ReactNode } from 'react';

import {
  splitAssignments,
  areaStats,
  directGroupLabel,
  FINISHED_GROUP_LABEL,
} from '../views/area-monitoring';
import { dueCountdown, formatElapsedSince } from '../views/dates';
import { formatStateAge } from '../views/machine-state';
import type { AreaAssignment } from '../views/area-monitoring';
import type {
  DueClass,
  MockArea,
  MockAreaCard,
  MockAreaMachine,
} from '../views/view-models';
import { AreaDot, HotPn } from './indicators';
import { useUiClock } from './ui-clock';

// Shared Area / Machine monitoring components used by BOTH the Area
// Board per-Area detail (read-only) and the Scan Station "In this Area
// now" layout (which may add action buttons through `rowAction`). One
// presentation language — no visual drift between the two views.
// Layout contract: the Area summary card is a fixed left column; the
// Machine cards occupy only the right-side grid and wrap there —
// never underneath the left card (align-items: start). Areas without
// Machines render only the full-width summary card.

/** Due status text in the shared color ramp (`—`/none stays neutral). */
export function DueStatus({
  due,
  dueClass,
}: {
  due: string;
  dueClass: DueClass | 'none';
}) {
  return (
    <span className={`mono ${dueClass === 'ok' ? '' : dueClass}`}>{due}</span>
  );
}

/**
 * Derived due status of one Area presence card: the countdown text and
 * urgency class come from the fixed due date plus the shared UI clock
 * (views/dates `dueCountdown`); a card-level `dueText` (e.g. the
 * Stockroom `allocated 50/50`) renders verbatim and neutral instead.
 */
function CardDueStatus({ card, now }: { card: MockAreaCard; now: number }) {
  if (card.dueText) return <DueStatus due={card.dueText} dueClass="ok" />;
  const { note, dueClass } = dueCountdown(card.due, now);
  return <DueStatus due={note} dueClass={dueClass} />;
}

/** Derived `… in Area` text (null without an Area-entry timestamp). */
function timeInArea(card: MockAreaCard, now: number): string | null {
  if (!card.enteredAreaAt) return null;
  return formatElapsedSince(card.enteredAreaAt, now);
}

/** Quantity with its `pcs` unit; the number stays its own element. */
export function QuantityStatus({ qty }: { qty: number }) {
  return (
    <span className="qtyline">
      <span className="q">{qty}</span> pcs
    </span>
  );
}

/** Readable in-Area status of one PN presence portion (row line 3). */
function inAreaStatusLabel(
  entry: AreaAssignment,
  directLabel: string,
): string | null {
  switch (entry.state) {
    case 'queue':
      return 'Awaiting Machine';
    case 'vendor':
      return 'External processing';
    case 'direct':
      return directLabel;
    case 'finished':
      // The Machine that completed the work is completion context only —
      // the quantity is no longer assigned to it and stays in the Area
      // until transferred.
      return entry.context !== '—'
        ? `Finished at ${entry.context} — ready to move`
        : 'Finished — ready to move';
    default:
      return 'On Machine';
  }
}

/**
 * One PN row in the shared Area presentation language, laid out as an
 * explicit grid: production information on the left, an optional
 * action rail in its own separated right-side cell (Scan Station
 * only — Area Board passes no actions).
 *
 *   line 1: Hot + PN               Machine/queue/done · quantity (pcs)
 *   line 2: WO Number (`—` when blank) · Job    due / days remaining
 *   line 3: in-Area status                             time in Area
 *   line 4: `{n} scrapped` — only when scrapped quantity exists
 *
 * WO/Job text may truncate; the full value stays available through the
 * `title` tooltip. Scrapped quantity appears only as readable text —
 * never as a compact `⊘` indicator. Rows inside a specific Machine
 * card pass `showContext={false}` so the Machine identified by the
 * card header is never repeated on every row.
 */
export function AreaPnRow({
  entry,
  action,
  directLabel = 'In processing',
  showContext = true,
}: {
  entry: AreaAssignment;
  action?: ReactNode;
  /** Line-3 label for direct (no-Machine) presence, e.g. `Stocked`. */
  directLabel?: string;
  /** Render the line-1 context chip (off inside a Machine card). */
  showContext?: boolean;
}) {
  const now = useUiClock('minute');
  const { card, context, qty, state } = entry;
  const woJob = `${card.workOrder.split(' ·')[0]} · ${card.job}`;
  const status = inAreaStatusLabel(entry, directLabel);
  const tia = timeInArea(card, now);
  const contextChip =
    state === 'finished' ? (
      <span className="ctx done">done</span>
    ) : context !== '—' ? (
      <span className="ctx">{context}</span>
    ) : null;
  return (
    <li className={action ? 'has-action' : undefined}>
      <div className="rowmain">
        <div className="r1">
          <span className="pnwrap">
            <HotPn rank={card.hotRank} pn={card.pn} pnClassName="p" />
          </span>
          <span className="r1r">
            {showContext ? contextChip : null}
            <QuantityStatus qty={qty} />
          </span>
        </div>
        <div className="r2">
          <span className="mono wo" title={woJob}>
            {woJob}
          </span>
          <CardDueStatus card={card} now={now} />
        </div>
        {status || tia !== null ? (
          <div className="r3">
            <span className={`st ${state === 'finished' ? 'done' : ''}`}>
              {status}
            </span>
            {tia !== null ? (
              <span className="mono tia">{tia} in Area</span>
            ) : null}
          </div>
        ) : null}
        {card.scrapped ? (
          <div className="r4">
            <span className="scraptxt">{card.scrapped} scrapped</span>
          </div>
        ) : null}
      </div>
      {action ? <div className="actcell">{action}</div> : null}
    </li>
  );
}

/**
 * One PN row of the All Areas overview — the same shared row shell and
 * subcomponents as the detail surfaces, with the card's portions
 * aggregated into compact context chips (Machine × qty, queue × qty,
 * processing × qty, done × qty) instead of one row per portion. The
 * overview and the detail views therefore cannot drift apart.
 */
export function AreaOverviewRow({ card }: { card: MockAreaCard }) {
  const now = useUiClock('minute');
  const { assigned, queued, finished } = splitAssignments([card]);
  const woJob = `${card.workOrder.split(' ·')[0]} · ${card.job}`;
  const tia = timeInArea(card, now);
  const portions: { key: string; label: string; done?: boolean }[] = [
    ...assigned.map((e) => ({
      key: `m-${e.context}`,
      label: `${e.context} × ${e.qty}`,
    })),
    ...queued
      .filter((e) => e.qty > 0)
      .map((e) => ({
        key: `q-${e.context}`,
        label:
          e.state === 'queue'
            ? `queue × ${e.qty}`
            : e.state === 'vendor'
              ? `vendor × ${e.qty}`
              : card.finished?.length
                ? `processing × ${e.qty}`
                : '',
      }))
      .filter((p) => p.label !== ''),
    ...finished.map((e) => ({
      key: `d-${e.context}`,
      label: `done × ${e.qty}`,
      done: true,
    })),
  ];
  return (
    <li>
      <div className="rowmain">
        <div className="r1">
          <span className="pnwrap">
            <HotPn rank={card.hotRank} pn={card.pn} pnClassName="p" />
          </span>
          <span className="r1r">
            <QuantityStatus qty={card.qty} />
          </span>
        </div>
        <div className="r2">
          <span className="mono wo" title={woJob}>
            {woJob}
          </span>
          <CardDueStatus card={card} now={now} />
        </div>
        {portions.length > 0 || tia !== null ? (
          <div className="r3">
            <span className="ctxs">
              {portions.map((portion) => (
                <span
                  key={portion.key}
                  className={`ctx ${portion.done ? 'done' : ''}`}
                >
                  {portion.label}
                </span>
              ))}
            </span>
            {tia !== null ? (
              <span className="mono tia">{tia} in Area</span>
            ) : null}
          </div>
        ) : null}
        {card.scrapped ? (
          <div className="r4">
            <span className="scraptxt">{card.scrapped} scrapped</span>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function AreaPnList({
  entries,
  rowAction,
  directLabel,
  showContext,
}: {
  entries: AreaAssignment[];
  rowAction?: (entry: AreaAssignment) => ReactNode;
  directLabel?: string;
  showContext?: boolean;
}) {
  return (
    <ul className="mc-list">
      {entries.map((entry) => (
        <AreaPnRow
          key={`${entry.card.pn}-${entry.card.workOrder}-${entry.state}-${entry.context}`}
          entry={entry}
          action={rowAction?.(entry)}
          directLabel={directLabel}
          showContext={showContext}
        />
      ))}
    </ul>
  );
}

/**
 * The "In this Area now" card: Area color along the top edge, a header
 * with the compact Area description and Operations, optional
 * statistics (`showStats` — the Scan Station header is the single
 * summary surface there, so the card omits them), and the grouped PN
 * list: `On Machines` / `Area queue — awaiting Machine` when the Area
 * has Machines, a direct `In processing` group otherwise, and
 * `Finished — ready to move` for quantity waiting on the finished
 * rack. Machine cards show only actively assigned quantity — finished
 * quantity belongs to this summary card.
 */
export function AreaSummaryCard({
  area,
  cards,
  machines,
  title,
  rowAction,
  showStats = true,
}: {
  area: MockArea;
  cards: MockAreaCard[];
  machines: readonly MockAreaMachine[];
  /** Card heading; defaults to the Area name. */
  title?: string;
  rowAction?: (entry: AreaAssignment) => ReactNode;
  /** Render the statistics row (Area Board keeps it; Scan Station
   * omits it — the header carries the Area totals there). */
  showStats?: boolean;
}) {
  const hasMachines = machines.length > 0;
  const { assigned, queued, finished } = splitAssignments(cards);
  const stats = areaStats(area, cards, hasMachines);
  const directLabel = area.terminal ? 'Stocked' : 'In processing';
  return (
    <div
      className="abd-card abd-summary"
      style={{ ['--acol' as string]: area.colorVar }}
    >
      <div className="mhead">
        <span className="mname">
          <AreaDot colorVar={area.colorVar} size={12} /> {title ?? area.name}
        </span>
        <span className="mc-ops">
          {area.operations.map((op) => (
            <span className="opchip" key={op}>
              {op}
            </span>
          ))}
        </span>
      </div>
      <div className="abd-desc">{area.description}</div>
      {showStats ? (
        <div
          className="mc-stats"
          style={{ gridTemplateColumns: `repeat(${stats.length}, 1fr)` }}
        >
          {stats.map((stat) => (
            <div className="stat" key={stat.label}>
              <div className={`n ${stat.tone ?? ''}`}>{stat.value}</div>
              <div className="l">{stat.label}</div>
            </div>
          ))}
        </div>
      ) : null}
      {cards.length === 0 ? (
        <div className="empty">No production in {area.name}</div>
      ) : (
        <>
          {hasMachines && assigned.length ? (
            <>
              <div className="abd-grp">On Machines</div>
              <AreaPnList entries={assigned} rowAction={rowAction} />
            </>
          ) : null}
          {queued.length ? (
            <>
              <div className="abd-grp">
                {directGroupLabel(area, hasMachines)}
              </div>
              <AreaPnList
                entries={queued}
                rowAction={rowAction}
                directLabel={directLabel}
              />
            </>
          ) : null}
          {finished.length ? (
            <>
              <div className="abd-grp done">{FINISHED_GROUP_LABEL}</div>
              <AreaPnList entries={finished} rowAction={rowAction} />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

const MACHINE_STATUS_LABEL: Record<MockAreaMachine['status'], string> = {
  running: 'running',
  idle: 'idle',
  maintenance: 'maintenance',
};

/**
 * One monitoring card per Machine (running / idle / maintenance).
 * Cards list only actively assigned quantity — after DONE the quantity
 * leaves the Machine card and appears in the Area summary under
 * `Finished — ready to move`. Rows never repeat the Machine name; the
 * card header already identifies it. The header status carries the
 * time in the current state (`running · 1h 24m`), derived from the
 * shared `stateChangedAt` timestamp so every view shows the same age.
 */
export function MachineMonitoringCard({
  machine,
  entries,
  rowAction,
}: {
  machine: MockAreaMachine;
  entries: AreaAssignment[];
  rowAction?: (entry: AreaAssignment) => ReactNode;
}) {
  const now = useUiClock('minute');
  const totalQty = entries.reduce((s, e) => s + e.qty, 0);
  return (
    <div className={`abd-card abd-machine ${machine.status}`}>
      <div className="mhead">
        <span className="mname">{machine.name}</span>
        <span className={`mstat ${machine.status}`}>
          {MACHINE_STATUS_LABEL[machine.status]}
          <span className="mage">
            {' '}
            · {formatStateAge(machine.stateChangedAt, now)}
          </span>
        </span>
      </div>
      {/* Semantic totals classes — the same shared tones as the Scan
          Station header statistics: pcs = secondary neutral (`Total
          pcs`), PN count = primary neutral (`Total PNs`). Never
          selected by position. */}
      <div className="mtotals">
        <b className="machine-total-pcs">{totalQty}</b> pcs assigned ·{' '}
        <b className="machine-total-pns">{entries.length}</b> PN
        {entries.length === 1 ? '' : 's'}
      </div>
      {entries.length ? (
        <AreaPnList
          entries={entries}
          rowAction={rowAction}
          showContext={false}
        />
      ) : (
        <div className="mempty">
          {machine.status === 'maintenance' ? (
            <>
              Under maintenance — accepts no production
              {machine.maintenanceNote ? (
                <> · {machine.maintenanceNote}</>
              ) : null}
              {machine.expectedReturn ? (
                <> · expected back {machine.expectedReturn}</>
              ) : null}
            </>
          ) : (
            'No production assigned'
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Shared structural layout: `[ Area summary | Machine cards grid ]`.
 * Machine cards wrap to additional rows inside the right-side grid and
 * never underneath the left card. When the container is too narrow to
 * fit one usable Machine card beside the summary, the summary fills
 * the full row and the Machine cards continue below it (container
 * query in area-monitoring.css). Without Machines, the summary card
 * spans the full width and no Machine region renders at all.
 */
export function AreaMachineLayout({
  summary,
  machineCards,
}: {
  summary: ReactNode;
  machineCards: ReactNode[];
}) {
  if (machineCards.length === 0) {
    return <div className="am am-single">{summary}</div>;
  }
  return (
    <div className="am">
      {summary}
      <div className="am-machines">{machineCards}</div>
    </div>
  );
}
