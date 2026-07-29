import './area-monitoring.css';

import type { ReactNode } from 'react';

import {
  splitAssignments,
  areaStats,
  directGroupLabel,
} from '../views/area-monitoring';
import type { AreaAssignment } from '../views/area-monitoring';
import type {
  DueClass,
  MockArea,
  MockAreaCard,
  MockAreaMachine,
} from '../views/view-models';
import { AreaDot, HotPn } from './indicators';

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

/** Quantity plus its damaged/scrapped companion where present. */
export function QuantityStatus({
  qty,
  scrapped,
}: {
  qty: number;
  scrapped?: number;
}) {
  return (
    <>
      <span className="q">{qty}</span>
      {scrapped ? (
        <span className="scrap" title={`${scrapped} pcs scrapped (SCRAPPED)`}>
          ⊘{scrapped}
        </span>
      ) : null}
    </>
  );
}

/**
 * One PN row in the shared Area presentation language: Hot indicator,
 * PN, quantity (+ scrap), Machine/queue context, WO Number (`—` when
 * blank), Job Number, due status, time in Area, and an optional
 * action supplied by the owning view (Scan Station only).
 */
export function AreaPnRow({
  entry,
  action,
}: {
  entry: AreaAssignment;
  action?: ReactNode;
}) {
  const { card, context, qty } = entry;
  return (
    <li>
      <div className="r1">
        <HotPn rank={card.hotRank} pn={card.pn} pnClassName="p" />
        {context !== '—' ? <span className="ctx">{context}</span> : null}
        <QuantityStatus qty={qty} scrapped={card.scrapped} />
        {action}
      </div>
      <div className="r2">
        <span className="mono">
          {card.workOrder.split(' ·')[0]} · {card.job}
        </span>
        <DueStatus due={card.due} dueClass={card.dueClass} />
        {card.timeInArea !== '—' ? (
          <span className="mono">{card.timeInArea} in Area</span>
        ) : null}
        {card.scrapped ? (
          <span className="scraptxt">{card.scrapped} scrapped</span>
        ) : null}
      </div>
    </li>
  );
}

export function AreaPnList({
  entries,
  rowAction,
}: {
  entries: AreaAssignment[];
  rowAction?: (entry: AreaAssignment) => ReactNode;
}) {
  return (
    <ul className="mc-list">
      {entries.map((entry) => (
        <AreaPnRow
          key={`${entry.card.pn}-${entry.card.workOrder}-${entry.context}`}
          entry={entry}
          action={rowAction?.(entry)}
        />
      ))}
    </ul>
  );
}

/**
 * The "In this Area now" card: Area color along the top edge, header
 * with Operations, meaningful statistics, and the grouped PN list
 * ("Assigned to Machines" / "Area queue — awaiting Machine" only when
 * the Area has Machines; a direct "In processing" group otherwise).
 */
export function AreaSummaryCard({
  area,
  cards,
  machines,
  title,
  rowAction,
}: {
  area: MockArea;
  cards: MockAreaCard[];
  machines: readonly MockAreaMachine[];
  /** Card heading; defaults to the Area name. */
  title?: string;
  rowAction?: (entry: AreaAssignment) => ReactNode;
}) {
  const hasMachines = machines.length > 0;
  const { assigned, queued } = splitAssignments(cards);
  const stats = areaStats(area, cards, hasMachines);
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
      {cards.length === 0 ? (
        <div className="empty">No production in {area.name}</div>
      ) : (
        <>
          {hasMachines && assigned.length ? (
            <>
              <div className="abd-grp">Assigned to Machines</div>
              <AreaPnList entries={assigned} rowAction={rowAction} />
            </>
          ) : null}
          {queued.length ? (
            <>
              <div className="abd-grp">
                {directGroupLabel(area, hasMachines)}
              </div>
              <AreaPnList entries={queued} rowAction={rowAction} />
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

/** One monitoring card per Machine (running / idle / maintenance). */
export function MachineMonitoringCard({
  machine,
  entries,
  rowAction,
}: {
  machine: MockAreaMachine;
  entries: AreaAssignment[];
  rowAction?: (entry: AreaAssignment) => ReactNode;
}) {
  const totalQty = entries.reduce((s, e) => s + e.qty, 0);
  return (
    <div className={`abd-card abd-machine ${machine.status}`}>
      <div className="mhead">
        <span className="mname">{machine.name}</span>
        <span className={`mstat ${machine.status}`}>
          {MACHINE_STATUS_LABEL[machine.status]}
        </span>
      </div>
      <div className="mtotals">
        <b>{totalQty}</b> pcs assigned · <b>{entries.length}</b> PN
        {entries.length === 1 ? '' : 's'}
      </div>
      {entries.length ? (
        <AreaPnList entries={entries} rowAction={rowAction} />
      ) : (
        <div className="mempty">
          {machine.status === 'maintenance'
            ? 'Under maintenance — accepts no production'
            : 'No production assigned'}
        </div>
      )}
    </div>
  );
}

/**
 * Shared structural layout: `[ Area summary | Machine cards grid ]`.
 * Machine cards wrap to additional rows inside the right-side grid and
 * never underneath the left card. Without Machines, the summary card
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
