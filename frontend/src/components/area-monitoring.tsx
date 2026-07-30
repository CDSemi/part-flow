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

/** Quantity with its `pcs` unit; the number stays its own element. */
export function QuantityStatus({ qty }: { qty: number }) {
  return (
    <span className="qtyline">
      <span className="q">{qty}</span> pcs
    </span>
  );
}

/** Readable in-Area status of one PN presence (row line 3). */
function inAreaStatusLabel(
  context: string,
  directLabel: string,
): string | null {
  if (context === 'queue') return 'Awaiting Machine';
  if (context === 'vendor') return 'External processing';
  if (context === '—') return directLabel;
  return 'On Machine';
}

/**
 * One PN row in the shared Area presentation language, laid out as an
 * explicit grid: production information on the left, an optional
 * action rail in its own separated right-side cell (Scan Station
 * only — Area Board passes no actions).
 *
 *   line 1: Hot + PN                    Machine/queue · quantity (pcs)
 *   line 2: WO Number (`—` when blank) · Job    due / days remaining
 *   line 3: in-Area status                             time in Area
 *   line 4: `{n} scrapped` — only when scrapped quantity exists
 *
 * WO/Job text may truncate; the full value stays available through the
 * `title` tooltip. Scrapped quantity appears only as readable text —
 * never as a compact `⊘` indicator.
 */
export function AreaPnRow({
  entry,
  action,
  directLabel = 'In processing',
}: {
  entry: AreaAssignment;
  action?: ReactNode;
  /** Line-3 label for direct (no-Machine) presence, e.g. `Stocked`. */
  directLabel?: string;
}) {
  const { card, context, qty } = entry;
  const woJob = `${card.workOrder.split(' ·')[0]} · ${card.job}`;
  const status = inAreaStatusLabel(context, directLabel);
  return (
    <li className={action ? 'has-action' : undefined}>
      <div className="rowmain">
        <div className="r1">
          <span className="pnwrap">
            <HotPn rank={card.hotRank} pn={card.pn} pnClassName="p" />
          </span>
          <span className="r1r">
            {context !== '—' ? <span className="ctx">{context}</span> : null}
            <QuantityStatus qty={qty} />
          </span>
        </div>
        <div className="r2">
          <span className="mono wo" title={woJob}>
            {woJob}
          </span>
          <DueStatus due={card.due} dueClass={card.dueClass} />
        </div>
        {status || card.timeInArea !== '—' ? (
          <div className="r3">
            <span className="st">{status}</span>
            {card.timeInArea !== '—' ? (
              <span className="mono tia">{card.timeInArea} in Area</span>
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

export function AreaPnList({
  entries,
  rowAction,
  directLabel,
}: {
  entries: AreaAssignment[];
  rowAction?: (entry: AreaAssignment) => ReactNode;
  directLabel?: string;
}) {
  return (
    <ul className="mc-list">
      {entries.map((entry) => (
        <AreaPnRow
          key={`${entry.card.pn}-${entry.card.workOrder}-${entry.context}`}
          entry={entry}
          action={rowAction?.(entry)}
          directLabel={directLabel}
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
              <AreaPnList
                entries={queued}
                rowAction={rowAction}
                directLabel={directLabel}
              />
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
