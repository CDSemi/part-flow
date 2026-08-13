import './machines.css';

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { DevNotice } from '../../components/DevNotice';
import { useUiClock } from '../../components/ui-clock';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { TypedConfirmDialog } from '../../components/TypedConfirmDialog';
import { UnsavedChoiceDialog } from '../../components/UnsavedChoiceDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { MOCK_AREA_CARDS } from '../../mocks/area-board';
import { MOCK_ASSET_TAG_FORMAT } from '../../mocks/administration';
import { MOCK_AREAS, areaByKey } from '../../mocks/areas';
import { MOCK_MACHINE_ACTOR, MOCK_MACHINES } from '../../mocks/machines';
import { machineBarcode, nextAssetTag } from '../asset-tags';
import { code128ModuleCount, encodeCode128B } from '../code128';
import {
  MACHINE_STATE_LABEL,
  effectiveMachineStatus,
  formatStateAge,
  machineAssignments,
} from '../machine-state';
import type { AreaKey, MockMachine } from '../view-models';

// Management → Machines: the single place for operational Machine
// monitoring and authorized Machine configuration. Access is
// permission-based (Production Manager, Process Engineer, Maintenance
// Manager, or another authorized specialist) — full Administrator
// access is deliberately NOT required, and Administration keeps no
// duplicate Machine screen. Focused scope: lifecycle, maintenance and
// asset identification only — PartFlow is not a CMMS (no spare parts,
// no maintenance schedules, no service contracts, no cost accounting).

type PendingDialog =
  | { kind: 'new' }
  | { kind: 'edit'; machine: MockMachine }
  | { kind: 'start-maintenance'; machine: MockMachine }
  | { kind: 'clear-maintenance'; machine: MockMachine }
  | { kind: 'retired-details'; machine: MockMachine }
  | { kind: 'reactivate'; machine: MockMachine };

/** Sortable columns of the active Machines table. */
type SortKey = 'machine' | 'state' | 'assigned' | 'asset' | 'maintenance';
/** Sortable columns of the Retired Machines table. */
type RetiredSortKey = 'machine' | 'retired' | 'asset' | 'notes';
type SortDir = 'asc' | 'desc';

/** State column sort order: working machines first, maintenance last. */
const STATE_SORT_RANK = { running: 0, idle: 1, maintenance: 2 } as const;

/**
 * Stable one-column sort: equal primary values keep name order, then
 * the input (registry) order — the same rows never swap between
 * renders.
 */
function sortMachines(
  rows: MockMachine[],
  dir: SortDir,
  value: (m: MockMachine) => string | number,
): MockMachine[] {
  return rows
    .map((machine, index) => ({ machine, index }))
    .sort((a, b) => {
      const va = value(a.machine);
      const vb = value(b.machine);
      const primary = va < vb ? -1 : va > vb ? 1 : 0;
      return (
        (dir === 'asc' ? primary : -primary) ||
        a.machine.name.localeCompare(b.machine.name) ||
        a.index - b.index
      );
    })
    .map((entry) => entry.machine);
}

/**
 * One sortable column header: a toggle cycling ascending → descending
 * → unsorted. The arrow names the direction; the active sort renders
 * emphasized in the info tone. `aria-sort` lives on the owning th.
 */
function SortHeader({
  label,
  active,
  dir,
  onToggle,
  ariaLabel,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onToggle: () => void;
  /** Distinct accessible name when two tables share column labels. */
  ariaLabel?: string;
}) {
  return (
    <button
      type="button"
      className={`mg-sortbtn${active ? ' on' : ''}`}
      aria-label={ariaLabel ?? `Sort by ${label}`}
      onClick={onToggle}
    >
      {label}
      <span className="arrow" aria-hidden="true">
        {active ? (dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </button>
  );
}

/** Typed-confirmation identifier: always the Asset Tag (required on
 * every Machine) — never the reusable display name. */
function confirmIdentifier(machine: MockMachine): {
  value: string;
  label: string;
} {
  return { value: machine.assetTag, label: 'Asset Tag' };
}

export function MachinesView() {
  const preview = getViewStatePreview();
  const [machines, setMachines] = useState<MockMachine[]>(MOCK_MACHINES);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  // Per-table sort: null = the registry order. Each header cycles
  // ascending → descending → unsorted; the two tables sort
  // independently.
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const toggleSort = (key: SortKey) =>
    setSort((current) =>
      current?.key !== key
        ? { key, dir: 'asc' }
        : current.dir === 'asc'
          ? { key, dir: 'desc' }
          : null,
    );
  const [retiredSort, setRetiredSort] = useState<{
    key: RetiredSortKey;
    dir: SortDir;
  } | null>(null);
  const toggleRetiredSort = (key: RetiredSortKey) =>
    setRetiredSort((current) =>
      current?.key !== key
        ? { key, dir: 'asc' }
        : current.dir === 'asc'
          ? { key, dir: 'desc' }
          : null,
    );

  const assignmentsById = useMemo(
    () =>
      new Map(
        machines.map((machine) => [
          machine.id,
          machineAssignments(MOCK_AREA_CARDS, machine),
        ]),
      ),
    [machines],
  );
  const assignedQty = (machine: MockMachine): number =>
    (assignmentsById.get(machine.id) ?? []).reduce((s, a) => s + a.qty, 0);

  if (preview === 'loading') {
    return (
      <section className="mg" aria-label="Machines">
        <LoadingState label="Loading Machines" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="mg" aria-label="Machines">
        <ErrorState
          message="Machine data could not be loaded."
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const query = search.trim().toLowerCase();
  const matches = (machine: MockMachine): boolean =>
    !query ||
    [
      machine.name,
      areaByKey(machine.area)?.name ?? '',
      machine.assetTag,
      machine.model ?? '',
      machine.manufacturer ?? '',
    ]
      .join(' ')
      .toLowerCase()
      .includes(query);

  const visible = preview === 'empty' ? [] : machines.filter(matches);
  const unsortedActive = visible.filter((m) => m.retiredOn === undefined);
  const unsortedRetired = visible.filter((m) => m.retiredOn !== undefined);

  /** Active-table column value driving the current sort. */
  const sortValue = (m: MockMachine, key: SortKey): string | number => {
    switch (key) {
      case 'machine':
        return m.name.toLowerCase();
      case 'state':
        return STATE_SORT_RANK[effectiveMachineStatus(m, assignedQty(m))];
      case 'assigned':
        return assignedQty(m);
      case 'asset':
        return m.assetTag.toLowerCase();
      case 'maintenance':
        return m.maintenance ? 0 : 1;
    }
  };
  const active = sort
    ? sortMachines(unsortedActive, sort.dir, (m) => sortValue(m, sort.key))
    : unsortedActive;

  /** Retired-table column value driving the current sort. */
  const retiredSortValue = (
    m: MockMachine,
    key: RetiredSortKey,
  ): string | number => {
    switch (key) {
      case 'machine':
        return m.name.toLowerCase();
      case 'retired':
        return m.retiredOn ?? '';
      case 'asset':
        return m.assetTag.toLowerCase();
      case 'notes':
        return (m.notes ?? '').toLowerCase();
    }
  };
  const retired = retiredSort
    ? sortMachines(unsortedRetired, retiredSort.dir, (m) =>
        retiredSortValue(m, retiredSort.key),
      )
    : unsortedRetired;

  const update = (id: string, change: (m: MockMachine) => MockMachine) =>
    setMachines((current) => current.map((m) => (m.id === id ? change(m) : m)));

  /** Finalize a retirement: current state + append-only lifecycle. */
  const retireMachine = (machine: MockMachine) => {
    const now = new Date().toISOString();
    update(machine.id, (m) => ({
      ...m,
      retiredOn: now.slice(0, 10),
      maintenance: undefined,
      stateChangedAt: now,
      lifecycle: [
        ...(m.lifecycle ?? []),
        { event: 'RETIRED' as const, at: now, by: MOCK_MACHINE_ACTOR },
      ],
    }));
    setDialog(null);
  };

  return (
    <section className="mg" aria-label="Machines">
      <h1>Machines</h1>
      <p className="mg-sub">
        Operational Machine monitoring, maintenance, and configuration for
        authorized production roles. Running and idle are derived from the
        assigned quantity — maintenance is the only state set by hand.
      </p>
      <DevNotice>
        Development preview — Machines shown are sample data and changes affect
        only this preview.
      </DevNotice>
      <div className="mg-toolbar">
        <input
          type="search"
          placeholder="Search: Machine, Area, asset tag…"
          aria-label="Search Machines"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="spacer" />
        <button
          className="btn primary"
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Machine
        </button>
      </div>

      {active.length === 0 ? (
        <EmptyState
          message={
            query
              ? `No Machines match “${search.trim()}”.`
              : 'No Machines configured yet.'
          }
        />
      ) : (
        <table className="mg-table">
          <thead>
            <tr>
              {(
                [
                  { key: 'machine', label: 'Machine', className: undefined },
                  { key: 'state', label: 'State', className: 'mg-statecol' },
                  {
                    key: 'assigned',
                    label: 'Assigned now',
                    className: undefined,
                  },
                  { key: 'asset', label: 'Asset', className: 'mg-metacol' },
                  {
                    key: 'maintenance',
                    label: 'Maintenance',
                    className: 'mg-maintcol',
                  },
                ] as const
              ).map((column) => (
                <th
                  key={column.key}
                  className={column.className}
                  aria-sort={
                    sort?.key === column.key
                      ? sort.dir === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : undefined
                  }
                >
                  <SortHeader
                    label={column.label}
                    active={sort?.key === column.key}
                    dir={sort?.key === column.key ? sort.dir : 'asc'}
                    onToggle={() => toggleSort(column.key)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {active.map((machine) => (
              <ActiveMachineRow
                key={machine.id}
                machine={machine}
                assignments={assignmentsById.get(machine.id) ?? []}
                onOpenEdit={() => setDialog({ kind: 'edit', machine })}
                onToggleMaintenance={() =>
                  setDialog({
                    kind: machine.maintenance
                      ? 'clear-maintenance'
                      : 'start-maintenance',
                    machine,
                  })
                }
              />
            ))}
          </tbody>
        </table>
      )}

      {retired.length > 0 ? (
        <div className="mg-retired">
          <h2>Retired Machines</h2>
          <table className="mg-table">
            <thead>
              <tr>
                {(
                  [
                    { key: 'machine', label: 'Machine', className: undefined },
                    { key: 'retired', label: 'Retired', className: undefined },
                    { key: 'asset', label: 'Asset', className: 'mg-metacol' },
                    { key: 'notes', label: 'Notes', className: undefined },
                  ] as const
                ).map((column) => (
                  <th
                    key={column.key}
                    className={column.className}
                    aria-sort={
                      retiredSort?.key === column.key
                        ? retiredSort.dir === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                  >
                    <SortHeader
                      label={column.label}
                      ariaLabel={`Sort Retired Machines by ${column.label}`}
                      active={retiredSort?.key === column.key}
                      dir={
                        retiredSort?.key === column.key
                          ? retiredSort.dir
                          : 'asc'
                      }
                      onToggle={() => toggleRetiredSort(column.key)}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {retired.map((machine) => (
                // The COMPLETE retired row opens the read-only Retired
                // Machine Details dialog (same pattern as the active
                // rows, v15): the name-cell button is the keyboard and
                // screen-reader entry point. There is no per-row action
                // — Reactivate lives inside the details dialog.
                <tr
                  key={machine.id}
                  className="selrow"
                  onClick={() =>
                    setDialog({ kind: 'retired-details', machine })
                  }
                >
                  <td>
                    <button
                      className="rowbtn"
                      aria-label={`Machine details — ${machine.name}`}
                    >
                      <MachineIdentityCell machine={machine} />
                    </button>
                  </td>
                  <td>
                    <span className="mg-retiredtag">
                      Retired on {machine.retiredOn}
                    </span>
                  </td>
                  <td className="mg-metacol">
                    <AssetMeta machine={machine} />
                  </td>
                  <td className="mg-meta">{machine.notes ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mg-retirednote" role="note">
            <span className="mg-retirednote-tag">Note</span>
            <span className="mg-retirednote-content">
              Retired Machines stay visible here and in history — they accept no
              new work and never appear in assignment choices. A replacement
              Machine may reuse the display name of the floor position; the
              asset tag keeps the physical Machines apart. Reactivate returns
              the SAME physical machine to service on the same record — a
              different physical machine always gets a new Machine record.
            </span>
          </p>
        </div>
      ) : null}

      {dialog?.kind === 'new' ? (
        <MachineEditDialog
          machines={machines}
          assignedQty={0}
          onCancel={() => setDialog(null)}
          onSave={(machine) => {
            setMachines((current) => [...current, machine]);
            setDialog(null);
          }}
          onApplyChanges={() => undefined}
          onRetire={() => undefined}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <MachineEditDialog
          machines={machines}
          machine={dialog.machine}
          assignedQty={assignedQty(dialog.machine)}
          onCancel={() => setDialog(null)}
          onSave={(machine) => {
            update(machine.id, () => machine);
            setDialog(null);
          }}
          onApplyChanges={(machine) => {
            update(machine.id, () => machine);
          }}
          onRetire={() => retireMachine(dialog.machine)}
        />
      ) : null}
      {dialog?.kind === 'start-maintenance' ? (
        <StartMaintenanceDialog
          machine={dialog.machine}
          assignedQty={assignedQty(dialog.machine)}
          onCancel={() => setDialog(null)}
          onConfirm={(note, expectedReturn) => {
            const now = new Date().toISOString();
            update(dialog.machine.id, (m) => ({
              ...m,
              maintenance: {
                since: now,
                note: note || undefined,
                expectedReturn: expectedReturn || undefined,
              },
              stateChangedAt: now,
            }));
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.kind === 'clear-maintenance' ? (
        <ConfirmDialog
          title="Clear maintenance"
          confirmLabel="Clear maintenance"
          cancelLabel="Cancel (Esc)"
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const now = new Date().toISOString();
            update(dialog.machine.id, (m) => ({
              ...m,
              maintenance: undefined,
              stateChangedAt: now,
            }));
            setDialog(null);
          }}
        >
          <b>{dialog.machine.name}</b> returns to{' '}
          <b>{assignedQty(dialog.machine) > 0 ? 'Running' : 'Idle'}</b> —{' '}
          {assignedQty(dialog.machine) > 0
            ? 'quantity is still assigned to it.'
            : 'no quantity is currently assigned to it.'}
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === 'retired-details' ? (
        <RetiredMachineDetailsDialog
          machine={dialog.machine}
          onClose={() => setDialog(null)}
          onReactivate={() =>
            setDialog({ kind: 'reactivate', machine: dialog.machine })
          }
        />
      ) : null}
      {dialog?.kind === 'reactivate' ? (
        // Reactivate is entered from the Retired Machine Details dialog
        // — leaving the workflow (the `‹ Back` action or Escape) pops
        // back to the details instead of closing everything.
        <ReactivateMachineDialog
          machine={dialog.machine}
          machines={machines}
          cancelLabel="‹ Back"
          onCancel={() =>
            setDialog({ kind: 'retired-details', machine: dialog.machine })
          }
          onConfirm={({ name, area, reason }) => {
            const now = new Date().toISOString();
            const moved = area !== dialog.machine.area;
            update(dialog.machine.id, (m) => ({
              ...m,
              retiredOn: undefined,
              name,
              area,
              maintenance: undefined,
              stateChangedAt: now,
              lifecycle: [
                ...(m.lifecycle ?? []),
                {
                  event: 'REACTIVATED' as const,
                  at: now,
                  by: MOCK_MACHINE_ACTOR,
                  reason,
                  fromArea: moved ? dialog.machine.area : undefined,
                  toArea: moved ? area : undefined,
                },
              ],
            }));
            setDialog(null);
          }}
        />
      ) : null}
    </section>
  );
}

function MachineIdentityCell({ machine }: { machine: MockMachine }) {
  const area = areaByKey(machine.area);
  return (
    <>
      <div className="mgname">{machine.name}</div>
      <div className="mgarea">
        <AreaDot colorVar={area?.colorVar ?? 'var(--faint)'} size={9} />
        {area?.name ?? machine.area}
      </div>
    </>
  );
}

function AssetMeta({ machine }: { machine: MockMachine }) {
  return (
    <div className="mg-meta">
      <div className="mg-assetline">
        Asset <span className="tagv">{machine.assetTag}</span>
      </div>
      <div className="mg-makeline">
        {/* Manufacturer and model in two distinct neutral tones (v15)
            — never one merged string tone. */}
        {machine.manufacturer || machine.model ? (
          <>
            {machine.manufacturer ? (
              <span className="mfr">{machine.manufacturer}</span>
            ) : null}
            {machine.manufacturer && machine.model ? ' ' : null}
            {machine.model ? (
              <span className="mdl">{machine.model}</span>
            ) : null}
          </>
        ) : (
          '—'
        )}
      </div>
    </div>
  );
}

/**
 * Maintenance switch — an affordance that STARTS the existing
 * workflows, never a direct state write: switching on opens the Start
 * Maintenance dialog (note + expected return), switching off opens the
 * Clear Maintenance confirmation. `aria-checked` reflects the real
 * state only, so a cancelled dialog leaves the switch untouched.
 */
function MaintenanceSwitch({
  machine,
  onToggle,
}: {
  machine: MockMachine;
  onToggle: () => void;
}) {
  const on = machine.maintenance !== undefined;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={`Maintenance — ${machine.name}`}
      className={`mg-switch${on ? ' on' : ''}`}
      onClick={onToggle}
    >
      <span className="track" aria-hidden="true">
        <span className="knob" />
      </span>
      <span className="swlbl">{on ? 'On' : 'Off'}</span>
    </button>
  );
}

function ActiveMachineRow({
  machine,
  assignments,
  onOpenEdit,
  onToggleMaintenance,
}: {
  machine: MockMachine;
  assignments: { pn: string; qty: number }[];
  onOpenEdit: () => void;
  onToggleMaintenance: () => void;
}) {
  const qty = assignments.reduce((s, a) => s + a.qty, 0);
  const status = effectiveMachineStatus(machine, qty);
  // Shared minute clock: the state age keeps ticking while the table
  // stays open and matches the monitoring cards on every other view.
  const now = useUiClock('minute');
  return (
    // The COMPLETE row opens Edit Machine (v15): the name-cell button
    // is the keyboard (Enter/Space) and screen-reader entry point and
    // its activation bubbles to this row handler. The Maintenance cell
    // is the one interactive island inside the row — it stops
    // propagation so the switch never also opens the dialog.
    <tr className="selrow" onClick={onOpenEdit}>
      <td>
        <button className="rowbtn" aria-label={`Edit ${machine.name}`}>
          <MachineIdentityCell machine={machine} />
        </button>
      </td>
      <td className="mg-statecol">
        <span className={`mg-state ${status}`}>
          {MACHINE_STATE_LABEL[status]}{' '}
          <span className="age">
            · {formatStateAge(machine.stateChangedAt, now)}
          </span>
        </span>
        {machine.maintenance ? (
          <div className="mg-statenote">
            {machine.maintenance.note ? (
              <>{machine.maintenance.note}. </>
            ) : null}
            {machine.maintenance.expectedReturn ? (
              <>Expected back {machine.maintenance.expectedReturn}.</>
            ) : null}
          </div>
        ) : null}
      </td>
      <td>
        {assignments.length === 0 ? (
          <span className="mg-meta">—</span>
        ) : (
          assignments.map((a) => (
            <div className="mg-assign" key={a.pn}>
              {a.pn} <span className="sep">·</span>{' '}
              <span className={`q ${status}`}>{a.qty}</span>{' '}
              <span className="unit">pcs</span>
            </div>
          ))
        )}
      </td>
      <td className="mg-metacol">
        <AssetMeta machine={machine} />
      </td>
      <td className="mg-maintcol" onClick={(event) => event.stopPropagation()}>
        <MaintenanceSwitch machine={machine} onToggle={onToggleMaintenance} />
      </td>
    </tr>
  );
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  // The label text (including any parenthesized qualifier span) is ONE
  // inline flex item — the stacked flex-column label must never place
  // a qualifier on its own row between the text and the control.
  return (
    <label>
      <span>{label}</span>
      {children}
    </label>
  );
}

/**
 * Area select with the selected Area's color previewed as a slim
 * horizontal line filling the label row up to the dialog's right edge
 * (v17) — a native select, never a custom control just for a color.
 * Shared by New Machine and Reactivate Machine.
 */
function AreaSelectField({
  label,
  value,
  choices,
  onChange,
}: {
  label: string;
  value: AreaKey;
  choices: { key: AreaKey; name: string }[];
  onChange: (area: AreaKey) => void;
}) {
  const fieldId = useId();
  const selected = areaByKey(value);
  return (
    <>
      <label className="mg-arealabelrow" htmlFor={fieldId}>
        {label}
        <span
          className="mg-arealine"
          style={{ background: selected?.colorVar ?? 'var(--faint)' }}
          aria-hidden="true"
        />
      </label>
      <select
        id={fieldId}
        className="field"
        value={value}
        onChange={(event) => onChange(event.target.value as AreaKey)}
      >
        {choices.map((choice) => (
          <option key={choice.key} value={choice.key}>
            {choice.name}
          </option>
        ))}
      </select>
    </>
  );
}

/** Key/value recap rows shared by the final confirmation summaries. */
function SummaryList({
  rows,
}: {
  rows: { label: string; value: ReactNode }[];
}) {
  return (
    <div className="mg-summary">
      {rows.map((row) => (
        <div className="srow" key={row.label}>
          <span className="k">{row.label}</span>
          <span className="v">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Append-only lifecycle audit (RETIRED / REACTIVATED) as the ONE
 * shared vertical-timeline presentation — tone-ringed markers on a
 * hairline rail (error for RETIRED, success for REACTIVATED; the event
 * name always renders — color is never the only distinction), with
 * date, actor, reason, and the previous → current Area on a move.
 * Used by Edit Machine, Reactivate Machine and the Retired Machine
 * Details dialog. `compact` keeps the rail and markers but renders
 * each event on ONE line (Edit Machine — the audit stays present
 * without claiming form height). Without events it renders `emptyText`
 * when given, nothing otherwise.
 */
function LifecycleTimeline({
  machine,
  emptyText,
  compact = false,
}: {
  machine: MockMachine;
  emptyText?: string;
  compact?: boolean;
}) {
  const events = machine.lifecycle ?? [];
  if (events.length === 0 && !emptyText) return null;
  return (
    <div className={`mg-timeline${compact ? ' compact' : ''}`}>
      <div className="mg-lifetitle">Lifecycle</div>
      {events.length === 0 ? (
        <p className="tl-empty">{emptyText}</p>
      ) : (
        <ol>
          {events.map((event, index) => (
            <li
              className={`mg-tlevent ${
                event.event === 'RETIRED' ? 'ev-retired' : 'ev-reactivated'
              }`}
              key={`${event.event}-${event.at}-${index}`}
            >
              <span className="dot" aria-hidden="true" />
              {compact ? (
                <div className="tl-line">
                  <span className="ev">{event.event}</span>{' '}
                  <span className="at">{event.at.slice(0, 10)}</span> ·{' '}
                  {event.by}
                  {event.reason ? <> — {event.reason}</> : null}
                  {event.fromArea && event.toArea ? (
                    <>
                      {' '}
                      · {areaByKey(event.fromArea)?.name ??
                        event.fromArea} →{' '}
                      {areaByKey(event.toArea)?.name ?? event.toArea}
                    </>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="tl-head">
                    <span className="ev">{event.event}</span>
                    <span className="at">{event.at.slice(0, 10)}</span>
                  </div>
                  <div className="tl-meta">{event.by}</div>
                  {event.reason ? (
                    <div className="tl-reason">{event.reason}</div>
                  ) : null}
                  {event.fromArea && event.toArea ? (
                    <div className="tl-meta">
                      {areaByKey(event.fromArea)?.name ?? event.fromArea} →{' '}
                      {areaByKey(event.toArea)?.name ?? event.toArea}
                    </div>
                  ) : null}
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * Read-only Machine identity header at the top of the New/Edit dialog:
 * the Asset Tag (assigned automatically at creation, never edited) and
 * the barcode derived from it — one value in the PF:MACHINE:
 * namespace, never an independent identifier. On an existing Machine
 * the header also carries the fixed Area with its service-life
 * explanation, and the entry to the printable barcode label.
 */
function IdentityHeader({
  assetTag,
  machine,
  onOpenLabel,
}: {
  assetTag: string;
  machine?: MockMachine;
  onOpenLabel?: () => void;
}) {
  const area = machine ? areaByKey(machine.area) : undefined;
  return (
    <div className="mg-idhead">
      <div className="idcol">
        <span className="idlabel">Asset Tag</span>
        <span className="idvalue tag">{assetTag}</span>
      </div>
      <div className="idcol grow">
        <span className="idlabel">Barcode</span>
        <span className="idvalue">{machineBarcode(assetTag)}</span>
      </div>
      {machine && onOpenLabel ? (
        <button type="button" className="mg-labelbtn" onClick={onOpenLabel}>
          Barcode label…
        </button>
      ) : null}
      {machine ? (
        <div className="idarea">
          <div className="idarealine">
            <span className="idlabel">Area</span>
            <span
              className="mg-arealine"
              style={{ background: area?.colorVar ?? 'var(--faint)' }}
              aria-hidden="true"
            />
          </div>
          <div className="mg-areafixedvalue">
            <AreaDot colorVar={area?.colorVar ?? 'var(--faint)'} size={11} />
            {area?.name ?? machine.area}
          </div>
          <p className="idareahelp">
            The Area is set when a Machine is created and stays fixed for its
            whole service life. To move capacity to another Area, retire this
            Machine and create a new Machine record there.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Printable Machine barcode label: the display name, the Asset Tag and
 * the Code 128 barcode of the scanned value (`PF:MACHINE:<asset-tag>`).
 * Print Label prints exactly the label area (print styles hide the
 * rest of the page).
 */
function BarcodeLabelDialog({
  machine,
  onClose,
}: {
  machine: MockMachine;
  onClose: () => void;
}) {
  const value = machineBarcode(machine.assetTag);
  const runs = encodeCode128B(value);
  const quiet = 10;
  const moduleWidth = 2;
  const barHeight = 64;
  const totalModules = runs ? code128ModuleCount(runs) + quiet * 2 : 0;
  let x = quiet;
  return (
    <ModalDialog label="Machine barcode label" onClose={onClose}>
      <h3>Machine barcode label</h3>
      <div className="sub">
        The barcode carries the Asset Tag — scanning it selects{' '}
        <b>{machine.name}</b> for Machine workflows.
      </div>
      <div className="mg-label mg-labelprint">
        <div className="lname">{machine.name}</div>
        <div className="ltag">{machine.assetTag}</div>
        {runs ? (
          <svg
            className="lbarcode"
            viewBox={`0 0 ${totalModules * moduleWidth} ${barHeight}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={`Barcode ${value}`}
          >
            {runs.map((run, index) => {
              const rect = run.bar ? (
                <rect
                  key={index}
                  x={x * moduleWidth}
                  y={0}
                  width={run.width * moduleWidth}
                  height={barHeight}
                />
              ) : null;
              x += run.width;
              return rect;
            })}
          </svg>
        ) : null}
        <div className="lvalue">{value}</div>
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={() => window.print()}>
          Print Label
        </button>
      </div>
    </ModalDialog>
  );
}

/**
 * Add or edit one Machine. The Area of an existing Machine is fixed —
 * a Machine belongs to exactly one Area; moving production capacity is
 * a replacement (retire + new record). The ONE exception is the
 * Reactivate workflow, where the same physical machine may return to
 * service in a different Area. The Asset Tag (and with it the barcode)
 * is assigned automatically at creation and is never editable; all
 * other asset metadata stays optional.
 *
 * Editing also hosts the Danger Zone (v15): Retire lives here instead
 * of a table button. Starting Retire with unsaved edits never saves
 * them silently — an explicit Save / Discard / Cancel decision comes
 * first. The decision is only RECORDED (v17): it is applied when the
 * retirement completes, so cancelling the typed confirmation or the
 * final summary returns to the form with the edits intact.
 */
function MachineEditDialog({
  machines,
  machine,
  assignedQty,
  onCancel,
  onSave,
  onApplyChanges,
  onRetire,
}: {
  machines: MockMachine[];
  machine?: MockMachine;
  assignedQty: number;
  onCancel: () => void;
  onSave: (machine: MockMachine) => void;
  /** Persist edits without closing (Save inside the retire flow). */
  onApplyChanges: (machine: MockMachine) => void;
  onRetire: () => void;
}) {
  const initial = {
    name: machine?.name ?? '',
    area: machine?.area ?? ('lathe' as AreaKey),
    manufacturer: machine?.manufacturer ?? '',
    model: machine?.model ?? '',
    serialNumber: machine?.serialNumber ?? '',
    installedOn: machine?.installedOn ?? '',
    notes: machine?.notes ?? '',
    maintenanceNote: machine?.maintenance?.note ?? '',
    maintenanceReturn: machine?.maintenance?.expectedReturn ?? '',
  };
  const [name, setName] = useState(initial.name);
  const [area, setArea] = useState<AreaKey>(initial.area);
  const [manufacturer, setManufacturer] = useState(initial.manufacturer);
  const [model, setModel] = useState(initial.model);
  const [serialNumber, setSerialNumber] = useState(initial.serialNumber);
  const [installedOn, setInstalledOn] = useState(initial.installedOn);
  const [notes, setNotes] = useState(initial.notes);
  // Maintenance context (existing Machine under maintenance only):
  // the note and expected return date are editable here — the state
  // itself is only switched through the existing dialogs (§12.2).
  const [maintenanceNote, setMaintenanceNote] = useState(
    initial.maintenanceNote,
  );
  const [maintenanceReturn, setMaintenanceReturn] = useState(
    initial.maintenanceReturn,
  );
  const [error, setError] = useState<string | null>(null);
  // New-Machine flow (v17 pattern): form → `Confirm new Machine`
  // summary → final add confirmation. Nothing is added before the last
  // confirmation.
  const [addStage, setAddStage] = useState<null | 'summary' | 'confirm'>(null);
  const [labelOpen, setLabelOpen] = useState(false);
  const [retireStage, setRetireStage] = useState<
    null | 'blocked' | 'unsaved' | 'confirm' | 'summary' | 'final'
  >(null);
  // Recorded DECISION for the pending edits inside the retire flow —
  // nothing is saved or discarded until the retirement completes, so
  // cancelling any later step returns to the form with the edits
  // intact.
  const [retireEditsIntent, setRetireEditsIntent] = useState<
    'save' | 'discard' | null
  >(null);
  // Close request while the form holds unsaved input — an explicit
  // decision comes first (§3.10: cancel never silently saves, and
  // entered input is never silently lost either).
  const [leaveConfirm, setLeaveConfirm] = useState(false);
  // A NEW Machine starts with the Display name focused — the one
  // required field. Edit keeps the dialog-root focus (no field claims
  // it: the whole record is equally editable).
  const nameInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!machine) nameInputRef.current?.focus();
    // Initial focus only — `machine` never changes while open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty =
    name !== initial.name ||
    manufacturer !== initial.manufacturer ||
    model !== initial.model ||
    serialNumber !== initial.serialNumber ||
    installedOn !== initial.installedOn ||
    notes !== initial.notes ||
    maintenanceNote !== initial.maintenanceNote ||
    maintenanceReturn !== initial.maintenanceReturn;

  // The Asset Tag shown in the dialog: the Machine's own on edit, the
  // next tag to assign on create (deterministic against the current
  // list — the same value the save assigns).
  const dialogAssetTag = useMemo(
    () =>
      machine?.assetTag ??
      nextAssetTag(
        MOCK_ASSET_TAG_FORMAT,
        machines.map((m) => m.assetTag),
      ),
    [machine, machines],
  );

  /** Pure validation + record assembly (no state changes). */
  const build = (): { machine: MockMachine } | { error: string } => {
    const trimmedName = name.trim();
    if (!trimmedName) return { error: 'A display name is required.' };
    const targetArea = machine?.area ?? area;
    // Display names stay unique among the active Machines of one Area
    // (reuse across time and replacements stays allowed).
    const nameCollision = machines.some(
      (m) =>
        m.id !== machine?.id &&
        m.retiredOn === undefined &&
        m.area === targetArea &&
        m.name === trimmedName,
    );
    if (nameCollision) {
      return {
        error: `An active Machine named “${trimmedName}” already exists in ${
          areaByKey(targetArea)?.name ?? targetArea
        }. Display names stay unique among the active Machines of one Area.`,
      };
    }
    return {
      machine: {
        ...(machine ?? {
          id: `MC-${String(Date.now()).slice(-4)}`,
          stateChangedAt: new Date().toISOString(),
          // Assigned automatically, never entered: the Asset Tag is
          // the Machine's stable physical identity and its barcode.
          assetTag: dialogAssetTag,
          barcode: dialogAssetTag,
        }),
        area: targetArea,
        name: trimmedName,
        manufacturer: manufacturer.trim() || undefined,
        model: model.trim() || undefined,
        serialNumber: serialNumber.trim() || undefined,
        installedOn: installedOn || undefined,
        notes: notes.trim() || undefined,
        // Update the maintenance context in place — `since` and the
        // Maintenance state itself never change here.
        ...(machine?.maintenance
          ? {
              maintenance: {
                ...machine.maintenance,
                note: maintenanceNote.trim() || undefined,
                expectedReturn: maintenanceReturn || undefined,
              },
            }
          : {}),
      },
    };
  };

  /** Edit: save immediately. New: validate, then enter the summary. */
  const save = () => {
    const built = build();
    if ('error' in built) {
      setError(built.error);
      return;
    }
    if (machine) {
      onSave(built.machine);
      return;
    }
    setError(null);
    setAddStage('summary');
  };

  /** Close request: with unsaved input an explicit decision comes
   * first — nothing is saved or discarded by the request itself. */
  const requestClose = () => {
    if (dirty) {
      setLeaveConfirm(true);
      return;
    }
    onCancel();
  };

  // The in-place Danger Zone state disables Retire while quantity is
  // assigned; startRetire keeps the `Cannot retire Machine` dialog as
  // the fallback for a stale row (the real gate stays in the
  // production workflow, never presentation).
  const startRetire = () => {
    if (!machine) return;
    if (assignedQty > 0) {
      setRetireStage('blocked');
      return;
    }
    setRetireEditsIntent(null);
    setRetireStage(dirty ? 'unsaved' : 'confirm');
  };

  /** Leave the retire flow — the form keeps its (unsaved) edits. */
  const cancelRetire = () => {
    setRetireStage(null);
    setRetireEditsIntent(null);
  };

  /** The retirement really happens HERE: apply the recorded edits
   * decision first, then retire. */
  const finalizeRetire = () => {
    if (retireEditsIntent === 'save') {
      const built = build();
      if ('error' in built) return; // validated when the decision was made
      onApplyChanges(built.machine);
    }
    onRetire();
  };

  const builtRecord = build();
  const identifier = machine ? confirmIdentifier(machine) : null;
  const selectedArea = areaByKey(area);
  // The summary shows the record as it will be retired: with the
  // edits when the recorded decision is Save, as last saved otherwise.
  const summaryRecord =
    retireEditsIntent === 'save' && !('error' in builtRecord)
      ? builtRecord.machine
      : machine;

  return (
    <ModalDialog
      label={machine ? 'Edit Machine' : 'New Machine'}
      onClose={requestClose}
      size="wide"
    >
      <div className="mg-dlghead">
        <h3>{machine ? 'Edit Machine' : 'New Machine'}</h3>
        {dirty ? <span className="mg-dirty">● Unsaved changes</span> : null}
      </div>
      <IdentityHeader
        assetTag={dialogAssetTag}
        machine={machine}
        onOpenLabel={() => setLabelOpen(true)}
      />
      <div className="mg-form">
        {/* First row on the three-column form grid: Display name (with
            the Area select on a new Machine), Installed date last — the
            date column matches the width of one metadata column below.
            The native date input shows the browser's own format hint —
            a placeholder would never render. */}
        {machine ? (
          <div className="mg-grid3">
            <div className="mg-span2">
              <Field label="Display name">
                <input
                  className="field"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Lathe 5"
                />
              </Field>
            </div>
            <Field
              label={
                <>
                  Installed date{' '}
                  <span className="field-optional">(optional)</span>
                </>
              }
            >
              <input
                className="field"
                type="date"
                value={installedOn}
                onChange={(e) => setInstalledOn(e.target.value)}
              />
            </Field>
          </div>
        ) : (
          <div className="mg-grid3">
            <Field label="Display name">
              <input
                ref={nameInputRef}
                className="field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Lathe 5"
              />
            </Field>
            <div className="mg-areacell">
              <AreaSelectField
                label="Area"
                value={area}
                choices={MOCK_AREAS.filter((a) => !a.terminal)}
                onChange={setArea}
              />
            </div>
            <Field
              label={
                <>
                  Installed date{' '}
                  <span className="field-optional">(optional)</span>
                </>
              }
            >
              <input
                className="field"
                type="date"
                value={installedOn}
                onChange={(e) => setInstalledOn(e.target.value)}
              />
            </Field>
          </div>
        )}
        <div className="mg-grid3">
          <Field
            label={
              <>
                Manufacturer <span className="field-optional">(optional)</span>
              </>
            }
          >
            <input
              className="field"
              value={manufacturer}
              onChange={(e) => setManufacturer(e.target.value)}
              placeholder="e.g. Mazak"
            />
          </Field>
          <Field
            label={
              <>
                Model <span className="field-optional">(optional)</span>
              </>
            }
          >
            <input
              className="field"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. QT-250"
            />
          </Field>
          <Field
            label={
              <>
                Serial number <span className="field-optional">(optional)</span>
              </>
            }
          >
            <input
              className="field mono"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
              placeholder="e.g. Q25-90412"
            />
          </Field>
        </div>
        <Field
          label={
            <>
              Notes <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Coolant system upgraded 2024"
          />
        </Field>
        {machine?.maintenance ? (
          <div className="mg-maintedit">
            <div className="mg-lifetitle">Maintenance</div>
            <p className="mg-mainteditnote">
              This Machine is under Maintenance. The reason and expected return
              date can be updated here — Maintenance itself is switched off from
              the Machines list.
            </p>
            <div className="mg-grid2">
              <Field
                label={
                  <>
                    Reason / note{' '}
                    <span className="field-optional">(optional)</span>
                  </>
                }
              >
                <input
                  className="field"
                  value={maintenanceNote}
                  onChange={(e) => setMaintenanceNote(e.target.value)}
                  placeholder="e.g. Spindle bearing replacement"
                />
              </Field>
              <Field
                label={
                  <>
                    Expected return date{' '}
                    <span className="field-optional">(optional)</span>
                  </>
                }
              >
                <input
                  className="field"
                  type="date"
                  value={maintenanceReturn}
                  onChange={(e) => setMaintenanceReturn(e.target.value)}
                />
              </Field>
            </div>
          </div>
        ) : null}
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        {machine ? <LifecycleTimeline machine={machine} compact /> : null}
        {!machine ? (
          <div className="mg-note">
            Replacing a physical Machine? Retire the old Machine record and
            create a new one — the new Machine gets its own new Asset Tag and
            barcode and may reuse the familiar display name. History always
            keeps the Machine that really did the work.
          </div>
        ) : null}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={save}>
          {machine ? 'Save changes' : 'Continue'}
        </button>
      </div>
      {machine ? (
        <div className="mg-dangerzone">
          <div className="dz-title">Danger Zone</div>
          <div className="dz-body">
            {/* The zone states the CURRENT truth in place: the
                consequences (soft error reading tone) while retirement
                is possible, the plain blocked explanation (neutral
                muted tone) with Retire disabled while quantity is
                still assigned. */}
            {assignedQty > 0 ? (
              <p>
                <b>{machine.name}</b> cannot be retired while{' '}
                <b>{assignedQty} pcs</b> are still assigned. Complete or
                transfer that quantity through the normal production workflow
                first.
              </p>
            ) : (
              <p className="dz-live">
                Retiring <b>{machine.name}</b> removes it from every assignment
                choice and stops its barcode from accepting assignment scans.
                History is preserved — the Machine is never deleted.
              </p>
            )}
            <button
              className="dz-retire"
              disabled={assignedQty > 0}
              onClick={startRetire}
            >
              Retire…
            </button>
          </div>
        </div>
      ) : null}
      {leaveConfirm && machine ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save changes"
          discardLabel="Discard changes"
          saveDisabledReason={
            'error' in builtRecord
              ? `The edits cannot be saved yet: ${builtRecord.error}`
              : undefined
          }
          onCancel={() => setLeaveConfirm(false)}
          onSave={() => {
            if ('error' in builtRecord) return;
            onSave(builtRecord.machine);
          }}
          onDiscard={onCancel}
        >
          This Machine has unsaved edits. <b>Save changes</b> saves them and
          closes, <b>Discard changes</b> closes without saving them.
        </UnsavedChoiceDialog>
      ) : null}
      {leaveConfirm && !machine ? (
        <ConfirmDialog
          title="Discard new Machine?"
          confirmLabel="Discard input"
          cancelLabel="Keep editing"
          onCancel={() => setLeaveConfirm(false)}
          onConfirm={onCancel}
        >
          Nothing has been added yet — closing now discards the entered input.
        </ConfirmDialog>
      ) : null}
      {retireStage === 'blocked' && machine ? (
        <ModalDialog
          label="Cannot retire Machine"
          onClose={() => setRetireStage(null)}
        >
          <h3>Cannot retire Machine</h3>
          <div className="sub">
            <b>{machine.name}</b> still has <b>{assignedQty} pcs</b> assigned.
            Complete or transfer that quantity through the normal production
            workflow first, then retire the Machine.
          </div>
          <div className="row">
            <button
              className="bigbtn ghost"
              onClick={() => setRetireStage(null)}
            >
              Close
            </button>
          </div>
        </ModalDialog>
      ) : null}
      {retireStage === 'unsaved' && machine ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save changes"
          discardLabel="Discard changes"
          saveDisabledReason={
            'error' in builtRecord
              ? `The edits cannot be saved yet: ${builtRecord.error}`
              : undefined
          }
          onCancel={cancelRetire}
          onSave={() => {
            if ('error' in builtRecord) return;
            setRetireEditsIntent('save');
            setRetireStage('confirm');
          }}
          onDiscard={() => {
            setRetireEditsIntent('discard');
            setRetireStage('confirm');
          }}
        >
          This Machine has unsaved edits. Choose what happens to them when the
          retirement completes: <b>Save changes</b> keeps them on the retired
          record, <b>Discard changes</b> retires the Machine as last saved.
          Nothing is saved or discarded yet — cancelling a later step returns
          here with the edits still in the form.
        </UnsavedChoiceDialog>
      ) : null}
      {retireStage === 'confirm' && machine && identifier ? (
        <TypedConfirmDialog
          title="Retire Machine"
          danger
          expectedValue={identifier.value}
          valueLabel={identifier.label}
          confirmLabel="Continue"
          onCancel={cancelRetire}
          onConfirm={() => setRetireStage('summary')}
        >
          Retiring <b>{machine.name}</b>:
          <ul className="mg-consequences">
            <li>It disappears from Machine assignment choices.</li>
            <li>
              Its barcode (
              <span className="mono">{machineBarcode(machine.assetTag)}</span>)
              no longer accepts assignment scans.
            </li>
            <li>
              All history is preserved and keeps its reference to this Machine —
              nothing is deleted.
            </li>
            <li>
              The record moves to Retired Machines; only this same physical
              machine can later be reactivated.
            </li>
          </ul>
        </TypedConfirmDialog>
      ) : null}
      {(retireStage === 'summary' || retireStage === 'final') &&
      machine &&
      summaryRecord ? (
        <ModalDialog
          label="Confirm retirement"
          onClose={cancelRetire}
          className="dangerdlg"
        >
          <h3>Confirm retirement</h3>
          <div className="sub">
            Final check — nothing has changed yet. <b>{machine.name}</b> is
            retired only when you confirm below.
          </div>
          <SummaryList
            rows={[
              { label: 'Machine', value: summaryRecord.name },
              {
                label: 'Area',
                value: (
                  <>
                    <AreaDot
                      colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
                      size={10}
                    />
                    {areaByKey(summaryRecord.area)?.name ?? summaryRecord.area}
                  </>
                ),
              },
              {
                label: 'Asset Tag',
                value: <span className="mono">{summaryRecord.assetTag}</span>,
              },
              {
                label: 'Barcode',
                value: (
                  <span className="mono">
                    {machineBarcode(summaryRecord.assetTag)}
                  </span>
                ),
              },
              ...(retireEditsIntent
                ? [
                    {
                      label: 'Unsaved edits',
                      value:
                        retireEditsIntent === 'save'
                          ? 'Saved with the retirement'
                          : 'Discarded — retires as last saved',
                    },
                  ]
                : []),
            ]}
          />
          <div className="mg-note">
            The Machine leaves all assignment choices and its barcode stops
            accepting assignment scans. All history is preserved — the record
            moves to Retired Machines and is never deleted.
          </div>
          <div className="row">
            <button className="bigbtn ghost" onClick={cancelRetire}>
              Cancel (Esc)
            </button>
            <button
              className="bigbtn danger"
              onClick={() => setRetireStage('final')}
            >
              Retire Machine
            </button>
          </div>
          {/* Last safeguard: retiring writes a permanent lifecycle
              record — an explicit yes/no question, never a silent
              action from the summary alone. */}
          {retireStage === 'final' ? (
            <ConfirmDialog
              title="Retire this Machine?"
              confirmLabel="Retire Machine"
              cancelLabel="Cancel (Esc)"
              tone="danger"
              onCancel={() => setRetireStage('summary')}
              onConfirm={finalizeRetire}
            >
              <b>{machine.name}</b> will be retired. This action{' '}
              <b>cannot be undone</b> — the retirement is recorded permanently
              in the Machine&apos;s lifecycle, and even a later return to
              service keeps it in the record.
            </ConfirmDialog>
          ) : null}
        </ModalDialog>
      ) : null}
      {!machine && addStage !== null && !('error' in builtRecord) ? (
        <ModalDialog
          label="Confirm new Machine"
          onClose={() => setAddStage(null)}
        >
          <h3>Confirm new Machine</h3>
          <div className="sub">
            Final check — nothing has been added yet.{' '}
            <b>{builtRecord.machine.name}</b> is added only when you confirm
            below.
          </div>
          <SummaryList
            rows={[
              { label: 'Machine', value: builtRecord.machine.name },
              {
                label: 'Area',
                value: (
                  <>
                    <AreaDot
                      colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
                      size={10}
                    />
                    {areaByKey(builtRecord.machine.area)?.name ??
                      builtRecord.machine.area}
                  </>
                ),
              },
              {
                label: 'Asset Tag',
                value: (
                  <span className="mono">{builtRecord.machine.assetTag}</span>
                ),
              },
              {
                label: 'Barcode',
                value: (
                  <span className="mono">
                    {machineBarcode(builtRecord.machine.assetTag)}
                  </span>
                ),
              },
              ...(builtRecord.machine.manufacturer
                ? [
                    {
                      label: 'Manufacturer',
                      value: builtRecord.machine.manufacturer,
                    },
                  ]
                : []),
              ...(builtRecord.machine.model
                ? [{ label: 'Model', value: builtRecord.machine.model }]
                : []),
              ...(builtRecord.machine.serialNumber
                ? [
                    {
                      label: 'Serial number',
                      value: (
                        <span className="mono">
                          {builtRecord.machine.serialNumber}
                        </span>
                      ),
                    },
                  ]
                : []),
              ...(builtRecord.machine.installedOn
                ? [
                    {
                      label: 'Installed',
                      value: builtRecord.machine.installedOn,
                    },
                  ]
                : []),
              ...(builtRecord.machine.notes
                ? [{ label: 'Notes', value: builtRecord.machine.notes }]
                : []),
            ]}
          />
          <div className="mg-note">
            The Asset Tag and barcode are assigned when the Machine is added and
            never change afterwards. A Machine record is permanent — it can be
            retired, never deleted.
          </div>
          <div className="row">
            <button className="bigbtn ghost" onClick={() => setAddStage(null)}>
              Back
            </button>
            <button
              className="bigbtn primary"
              onClick={() => setAddStage('confirm')}
            >
              Add Machine
            </button>
          </div>
          {/* Last safeguard: a Machine record can never be deleted —
              adding one is an explicit yes/no question. */}
          {addStage === 'confirm' ? (
            <ConfirmDialog
              title="Add this Machine?"
              confirmLabel="Add Machine"
              cancelLabel="Cancel (Esc)"
              tone="warning"
              onCancel={() => setAddStage('summary')}
              onConfirm={() => onSave(builtRecord.machine)}
            >
              Add <b>{builtRecord.machine.name}</b> with Asset Tag{' '}
              <b>{builtRecord.machine.assetTag}</b>? A Machine record is
              permanent and <b>cannot be deleted or undone</b> once added — it
              can only be retired later.
            </ConfirmDialog>
          ) : null}
        </ModalDialog>
      ) : null}
      {labelOpen && machine ? (
        <BarcodeLabelDialog
          machine={machine}
          onClose={() => setLabelOpen(false)}
        />
      ) : null}
    </ModalDialog>
  );
}

/**
 * Read-only Retired Machine Details dialog, opened from a Retired
 * Machines row: the record's identity (name, Area, retirement badge),
 * the untouched Asset Tag and derived barcode, the asset metadata, the
 * notes, and the append-only lifecycle as a timeline. Nothing here is
 * editable — a retired record is historical evidence; the only action
 * besides Close is the existing staged Reactivate workflow.
 */
function RetiredMachineDetailsDialog({
  machine,
  onClose,
  onReactivate,
}: {
  machine: MockMachine;
  onClose: () => void;
  onReactivate: () => void;
}) {
  const area = areaByKey(machine.area);
  return (
    <ModalDialog label="Retired Machine Details" onClose={onClose} size="wide">
      <h3>Retired Machine Details</h3>
      <div className="mg-rdhead">
        <div className="rdid">
          <span className="nm">{machine.name}</span>
          <span className="rdarea">
            <AreaDot colorVar={area?.colorVar ?? 'var(--faint)'} size={9} />
            {area?.name ?? machine.area}
          </span>
        </div>
        <span className="mg-retiredtag">Retired on {machine.retiredOn}</span>
      </div>
      <div className="mg-idhead">
        <div className="idcol">
          <span className="idlabel">Asset Tag</span>
          <span className="idvalue tag">{machine.assetTag}</span>
        </div>
        <div className="idcol grow">
          <span className="idlabel">Barcode</span>
          <span className="idvalue">{machineBarcode(machine.assetTag)}</span>
        </div>
      </div>
      <SummaryList
        rows={[
          { label: 'Manufacturer', value: machine.manufacturer ?? '—' },
          { label: 'Model', value: machine.model ?? '—' },
          {
            label: 'Serial number',
            value: machine.serialNumber ? (
              <span className="mono">{machine.serialNumber}</span>
            ) : (
              '—'
            ),
          },
          { label: 'Installed', value: machine.installedOn ?? '—' },
          { label: 'Retired on', value: machine.retiredOn ?? '—' },
        ]}
      />
      {machine.notes ? (
        <div className="mg-rdnotes">
          <div className="mg-lifetitle">Notes</div>
          <p>{machine.notes}</p>
        </div>
      ) : null}
      <LifecycleTimeline
        machine={machine}
        emptyText="No lifecycle events recorded."
      />
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Close (Esc)
        </button>
        <button className="bigbtn primary" onClick={onReactivate}>
          Reactivate
        </button>
      </div>
    </ModalDialog>
  );
}

/**
 * Starting maintenance is an explicit override and nothing more: the
 * assigned quantity stays exactly where it is — it is never moved,
 * released, completed, or transferred by this action.
 */
function StartMaintenanceDialog({
  machine,
  assignedQty,
  onCancel,
  onConfirm,
}: {
  machine: MockMachine;
  assignedQty: number;
  onCancel: () => void;
  onConfirm: (note: string, expectedReturn: string) => void;
}) {
  const [note, setNote] = useState('');
  const [expectedReturn, setExpectedReturn] = useState('');
  return (
    <ModalDialog label="Start maintenance" onClose={onCancel}>
      <h3>Start maintenance</h3>
      <div className="sub">
        <b>{machine.name}</b> switches to <b>Maintenance</b>.{' '}
        {assignedQty > 0
          ? `${assignedQty} pcs stay assigned to it — nothing is moved or released by this action.`
          : 'No quantity is currently assigned to it.'}
      </div>
      <div className="mg-form">
        <Field
          label={
            <>
              Reason / note <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Spindle bearing replacement"
          />
        </Field>
        <Field
          label={
            <>
              Expected return date{' '}
              <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            type="date"
            value={expectedReturn}
            onChange={(e) => setExpectedReturn(e.target.value)}
          />
        </Field>
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          onClick={() => onConfirm(note.trim(), expectedReturn)}
        >
          Start maintenance
        </button>
      </div>
    </ModalDialog>
  );
}

/**
 * Return-to-service of the SAME physical machine (v15): the record,
 * identity, barcode, asset metadata and history stay untouched;
 * `retiredOn` clears and one REACTIVATED lifecycle event is appended.
 * The machine normally returns as Idle (running stays derived from
 * assigned quantity — reactivation never invents an assignment).
 * Blocked while its Asset Tag (which is also its barcode) or serial
 * number has been reissued to another active Machine, and while the display name would
 * collide with an active Machine in the target Area (names must stay
 * unique among active Machines of one Area — assignment displays rely
 * on them); a rename inside this dialog resolves the collision. If the
 * physical machine moved while retired, a new active Area may be
 * chosen here — forward-looking only, historical Movements keep their
 * recorded Areas.
 */
function ReactivateMachineDialog({
  machine,
  machines,
  cancelLabel = 'Cancel (Esc)',
  onCancel,
  onConfirm,
}: {
  machine: MockMachine;
  machines: MockMachine[];
  /** Label of the leave-the-workflow action — `‹ Back` when the dialog
   * is entered from the Retired Machine Details dialog. */
  cancelLabel?: string;
  onCancel: () => void;
  onConfirm: (result: { name: string; area: AreaKey; reason: string }) => void;
}) {
  const [name, setName] = useState(machine.name);
  const [area, setArea] = useState<AreaKey>(machine.area);
  const [reason, setReason] = useState('');
  const [samePhysical, setSamePhysical] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Staged confirmation (v17): the form validates on Continue, a
  // summary recap follows, and a final explicit question confirms
  // before the Machine really reactivates.
  const [stage, setStage] = useState<'form' | 'summary' | 'final'>('form');

  const activeOnes = machines.filter(
    (m) => m.retiredOn === undefined && m.id !== machine.id,
  );
  // Hard blockers: identity conflicts that make a safe reactivation
  // impossible without fixing other records first. The Asset Tag check
  // also covers the barcode — the barcode IS the Asset Tag.
  const blockers: string[] = [];
  if (activeOnes.some((m) => m.assetTag === machine.assetTag)) {
    blockers.push(
      `Asset Tag ${machine.assetTag} is used by another active Machine.`,
    );
  }
  if (
    machine.serialNumber &&
    activeOnes.some((m) => m.serialNumber === machine.serialNumber)
  ) {
    blockers.push(
      `Serial number ${machine.serialNumber} is used by another active Machine.`,
    );
  }

  const areaChoices = MOCK_AREAS.filter((a) => !a.terminal);
  const selectedArea = areaByKey(area);
  const nameCollision = activeOnes.some(
    (m) => m.area === area && m.name === name.trim(),
  );
  const moved = area !== machine.area;

  const continueToSummary = () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('A display name is required.');
      return;
    }
    if (nameCollision) {
      setError(
        `An active Machine named “${name.trim()}” already exists in ${
          selectedArea?.name ?? area
        }. Display names stay unique among active Machines of one Area — rename this Machine to continue.`,
      );
      return;
    }
    if (!reason.trim()) {
      setError('A reason is required — it becomes part of the audit record.');
      return;
    }
    if (!samePhysical) {
      setError(
        'Confirm that this is the same physical machine. A different physical machine needs a new Machine record.',
      );
      return;
    }
    setError(null);
    setStage('summary');
  };

  if (stage === 'summary' || stage === 'final') {
    return (
      <ModalDialog label="Confirm reactivation" onClose={onCancel} size="wide">
        <h3>Confirm reactivation</h3>
        <div className="sub">
          Final check — nothing has changed yet. <b>{machine.name}</b> returns
          to service only when you confirm below.
        </div>
        <SummaryList
          rows={[
            { label: 'Machine', value: name.trim() },
            {
              label: 'Area',
              value: (
                <>
                  <AreaDot
                    colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
                    size={10}
                  />
                  {moved ? (
                    <>
                      {areaByKey(machine.area)?.name ?? machine.area} →{' '}
                      {selectedArea?.name ?? area}
                    </>
                  ) : (
                    (selectedArea?.name ?? area)
                  )}
                </>
              ),
            },
            {
              label: 'Asset Tag',
              value: <span className="mono">{machine.assetTag}</span>,
            },
            {
              label: 'Barcode',
              value: (
                <span className="mono">{machineBarcode(machine.assetTag)}</span>
              ),
            },
            {
              label: 'Physical identity',
              value: 'Same physical machine confirmed',
            },
            { label: 'Reason', value: reason.trim() },
            { label: 'Returns as', value: 'Idle' },
          ]}
        />
        <div className="mg-note">
          Lifecycle: <b>Retired → Active</b> on the same record — identity,
          barcode, asset metadata and history stay untouched; one REACTIVATED
          audit event is added
          {moved ? ' with the previous and current Area' : ''}.
        </div>
        <div className="row">
          <button className="bigbtn ghost" onClick={() => setStage('form')}>
            Back
          </button>
          <button className="bigbtn primary" onClick={() => setStage('final')}>
            Reactivate Machine
          </button>
        </div>
        {/* Last safeguard: reactivating writes a permanent lifecycle
            record — an explicit yes/no question, never a silent action
            from the summary alone. */}
        {stage === 'final' ? (
          <ConfirmDialog
            title="Reactivate this Machine?"
            confirmLabel="Reactivate Machine"
            cancelLabel="Cancel (Esc)"
            tone="warning"
            onCancel={() => setStage('summary')}
            onConfirm={() =>
              onConfirm({ name: name.trim(), area, reason: reason.trim() })
            }
          >
            <b>{machine.name}</b> returns to service. This action{' '}
            <b>cannot be undone</b> — the reactivation is recorded permanently
            in the Machine&apos;s lifecycle, and the record of when it was out
            of service stays part of its history.
          </ConfirmDialog>
        ) : null}
      </ModalDialog>
    );
  }

  return (
    <ModalDialog label="Reactivate Machine" onClose={onCancel} size="wide">
      <h3>Reactivate Machine</h3>
      <div className="sub">
        <b>{machine.name}</b> (retired on {machine.retiredOn}) returns to
        service on the SAME record — identity, barcode, asset metadata and
        history stay untouched. It returns as <b>Idle</b>; running stays derived
        from assigned quantity.
      </div>
      {blockers.length > 0 ? (
        <div className="mg-blockers" role="alert">
          <div className="bt">Reactivation is blocked:</div>
          <ul>
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
          <p>
            Resolve the conflicting record first — or create a new Machine
            record if the physical machine was replaced.
          </p>
        </div>
      ) : null}
      <div className="mg-form">
        {/* Display name and Return Area share one row; each column
            carries its own feedback directly under the input — the
            collision error (or the availability confirmation) stays
            inside the name column, the move note inside the Area
            column. */}
        <div className="mg-grid2">
          <div className="mg-fieldcol">
            <Field label="Display name">
              <input
                className="field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Lathe 1"
              />
            </Field>
            {nameCollision ? (
              <div className="err" role="alert">
                An active Machine named “{name.trim()}” already exists in{' '}
                {selectedArea?.name ?? area} — rename one of them to continue.
              </div>
            ) : name.trim() ? (
              <div className="mg-fieldok">
                ✓ “{name.trim()}” is available in {selectedArea?.name ?? area}.
              </div>
            ) : null}
          </div>
          <div className="mg-areacell">
            <AreaSelectField
              label="Return Area"
              value={area}
              choices={areaChoices}
              onChange={setArea}
            />
            <p className="mg-areahelp">
              Change only if the Machine physically moved while retired.
              Historical Movements remain unchanged.
            </p>
          </div>
        </div>
        <Field
          label={
            <>
              Reason <span className="field-required">(required)</span>
            </>
          }
        >
          <input
            className="field"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Returned from overhaul"
          />
        </Field>
        {/* The same-physical-machine acknowledgement sits on a calm
            warning panel — "be sure of this before continuing", never
            an error: only the Important marker carries the warning
            tone, the sentence keeps the normal text tone. Continuing
            without the check is what becomes a validation error. */}
        <div className="mg-confirmpanel">
          <div className="cp-head">
            <span className="cp-icon" aria-hidden="true">
              ⚠
            </span>
            Important <span className="cp-req">(required)</span>
          </div>
          <label className="mg-check">
            <input
              type="checkbox"
              checked={samePhysical}
              onChange={(e) => setSamePhysical(e.target.checked)}
            />
            <span>
              This is the same physical machine returning to service — not a
              replacement.
            </span>
          </label>
        </div>
        <LifecycleTimeline machine={machine} />
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          {cancelLabel}
        </button>
        <button
          className="bigbtn primary"
          disabled={blockers.length > 0}
          onClick={continueToSummary}
        >
          Continue
        </button>
      </div>
    </ModalDialog>
  );
}
