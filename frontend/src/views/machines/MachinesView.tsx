import './machines.css';

import { useMemo, useState } from 'react';
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
import { MOCK_AREAS, areaByKey } from '../../mocks/areas';
import { MOCK_MACHINE_ACTOR, MOCK_MACHINES } from '../../mocks/machines';
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
  | { kind: 'reactivate'; machine: MockMachine };

/** Typed-confirmation identifier: Asset Tag preferred, barcode always
 * present as the fallback — never the reusable display name. */
function confirmIdentifier(machine: MockMachine): {
  value: string;
  label: string;
} {
  return machine.assetTag
    ? { value: machine.assetTag, label: 'Asset Tag' }
    : { value: machine.barcode, label: 'Machine barcode' };
}

export function MachinesView() {
  const preview = getViewStatePreview();
  const [machines, setMachines] = useState<MockMachine[]>(MOCK_MACHINES);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

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
      machine.assetTag ?? '',
      machine.model ?? '',
      machine.manufacturer ?? '',
    ]
      .join(' ')
      .toLowerCase()
      .includes(query);

  const visible = preview === 'empty' ? [] : machines.filter(matches);
  const active = visible.filter((m) => m.retiredOn === undefined);
  const retired = visible.filter((m) => m.retiredOn !== undefined);

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
              <th>Machine</th>
              <th className="mg-statecol">State</th>
              <th>Assigned now</th>
              <th className="mg-metacol">Asset</th>
              <th className="mg-maintcol">Maintenance</th>
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
                <th>Machine</th>
                <th>Retired</th>
                <th className="mg-metacol">Asset</th>
                <th>Notes</th>
                <th>
                  <span className="mg-visuallyquiet">Reactivate</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {retired.map((machine) => (
                <tr key={machine.id}>
                  <td>
                    <MachineIdentityCell machine={machine} />
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
                  <td>
                    <button
                      className="mg-reactivate"
                      onClick={() => setDialog({ kind: 'reactivate', machine })}
                    >
                      Reactivate…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mg-sub">
            Retired Machines stay visible here and in history — they accept no
            new work and never appear in assignment choices. A replacement
            Machine may reuse the display name of the floor position; the asset
            tag keeps the physical Machines apart. Reactivate returns the SAME
            physical machine to service on the same record — a different
            physical machine always gets a new Machine record.
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
      {dialog?.kind === 'reactivate' ? (
        <ReactivateMachineDialog
          machine={dialog.machine}
          machines={machines}
          onCancel={() => setDialog(null)}
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
      {machine.assetTag ? (
        <div className="mg-assetline">
          Asset <span className="tagv">{machine.assetTag}</span>
        </div>
      ) : null}
      <div>
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label>
      {label}
      {children}
    </label>
  );
}

/** Quiet append-only lifecycle audit list (RETIRED / REACTIVATED). */
function LifecycleList({ machine }: { machine: MockMachine }) {
  const events = machine.lifecycle ?? [];
  if (events.length === 0) return null;
  return (
    <div className="mg-lifecycle">
      <div className="mg-lifetitle">Lifecycle</div>
      {events.map((event, index) => (
        <div
          className="mg-lifeevent"
          key={`${event.event}-${event.at}-${index}`}
        >
          <span className="ev">{event.event}</span>{' '}
          <span className="at">{event.at.slice(0, 10)}</span> · {event.by}
          {event.reason ? <> — {event.reason}</> : null}
          {event.fromArea && event.toArea ? (
            <>
              {' '}
              · {areaByKey(event.fromArea)?.name ?? event.fromArea} →{' '}
              {areaByKey(event.toArea)?.name ?? event.toArea}
            </>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/**
 * Add or edit one Machine. The Area of an existing Machine is fixed —
 * a Machine belongs to exactly one Area; moving production capacity is
 * a replacement (retire + new record). The ONE exception is the
 * Reactivate workflow, where the same physical machine may return to
 * service in a different Area. All asset metadata stays optional.
 *
 * Editing also hosts the Danger Zone (v15): Retire lives here instead
 * of a table button. Starting Retire with unsaved edits never saves
 * them silently — an explicit Save / Discard / Cancel decision comes
 * first.
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
    barcode: machine?.barcode ?? '',
    manufacturer: machine?.manufacturer ?? '',
    model: machine?.model ?? '',
    assetTag: machine?.assetTag ?? '',
    serialNumber: machine?.serialNumber ?? '',
    installedOn: machine?.installedOn ?? '',
    notes: machine?.notes ?? '',
  };
  const [name, setName] = useState(initial.name);
  const [area, setArea] = useState<AreaKey>(initial.area);
  const [barcode, setBarcode] = useState(initial.barcode);
  const [manufacturer, setManufacturer] = useState(initial.manufacturer);
  const [model, setModel] = useState(initial.model);
  const [assetTag, setAssetTag] = useState(initial.assetTag);
  const [serialNumber, setSerialNumber] = useState(initial.serialNumber);
  const [installedOn, setInstalledOn] = useState(initial.installedOn);
  const [notes, setNotes] = useState(initial.notes);
  const [error, setError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState(initial);
  const [retireStage, setRetireStage] = useState<
    null | 'blocked' | 'unsaved' | 'confirm'
  >(null);

  const dirty =
    name !== baseline.name ||
    barcode !== baseline.barcode ||
    manufacturer !== baseline.manufacturer ||
    model !== baseline.model ||
    assetTag !== baseline.assetTag ||
    serialNumber !== baseline.serialNumber ||
    installedOn !== baseline.installedOn ||
    notes !== baseline.notes;

  /** Pure validation + record assembly (no state changes). */
  const build = (): { machine: MockMachine } | { error: string } => {
    const trimmedName = name.trim();
    const trimmedBarcode = barcode.trim();
    if (!trimmedName) return { error: 'A display name is required.' };
    if (!trimmedBarcode) return { error: 'A barcode value is required.' };
    const duplicate = machines.some(
      (m) => m.id !== machine?.id && m.barcode === trimmedBarcode,
    );
    if (duplicate) {
      return { error: 'That barcode is already used by another Machine.' };
    }
    return {
      machine: {
        ...(machine ?? {
          id: `MC-${String(Date.now()).slice(-4)}`,
          stateChangedAt: new Date().toISOString(),
        }),
        area: machine?.area ?? area,
        name: trimmedName,
        barcode: trimmedBarcode,
        manufacturer: manufacturer.trim() || undefined,
        model: model.trim() || undefined,
        assetTag: assetTag.trim() || undefined,
        serialNumber: serialNumber.trim() || undefined,
        installedOn: installedOn || undefined,
        notes: notes.trim() || undefined,
      },
    };
  };

  const save = () => {
    const built = build();
    if ('error' in built) {
      setError(built.error);
      return;
    }
    onSave(built.machine);
  };

  const startRetire = () => {
    if (!machine) return;
    if (assignedQty > 0) {
      setRetireStage('blocked');
      return;
    }
    setRetireStage(dirty ? 'unsaved' : 'confirm');
  };

  const buildForRetire = build();
  const identifier = machine ? confirmIdentifier(machine) : null;
  const selectedArea = areaByKey(area);

  return (
    <ModalDialog
      label={machine ? 'Edit Machine' : 'New Machine'}
      onClose={onCancel}
      size="wide"
    >
      <h3>{machine ? 'Edit Machine' : 'New Machine'}</h3>
      {dirty ? <div className="mg-dirty">● Unsaved changes</div> : null}
      <div className="mg-form">
        <div className="mg-grid2">
          <Field label="Display name">
            <input
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Lathe 5"
            />
          </Field>
          <Field label="Barcode value">
            <input
              className="field mono"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              placeholder="e.g. L5"
            />
          </Field>
        </div>
        {machine ? (
          <>
            <label>Area</label>
            <div className="mg-fixed">
              <AreaDot
                colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
                size={11}
              />
              {areaByKey(machine.area)?.name ?? machine.area} — fixed; a Machine
              belongs to one Area
            </div>
          </>
        ) : (
          <Field label="Area">
            {/* Native select + selected-Area color preview beside it —
                never a custom select just for a color (v15). */}
            <div className="mg-areapick">
              <AreaDot
                colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
                size={12}
              />
              <select
                className="field"
                value={area}
                onChange={(e) => setArea(e.target.value as AreaKey)}
              >
                {MOCK_AREAS.filter((a) => !a.terminal).map((a) => (
                  <option key={a.key} value={a.key}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          </Field>
        )}
        <div className="mg-grid2">
          <Field label="Manufacturer (optional)">
            <input
              className="field"
              value={manufacturer}
              onChange={(e) => setManufacturer(e.target.value)}
            />
          </Field>
          <Field label="Model (optional)">
            <input
              className="field"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </Field>
          <Field label="Asset tag (optional)">
            <input
              className="field mono"
              value={assetTag}
              onChange={(e) => setAssetTag(e.target.value)}
            />
          </Field>
          <Field label="Serial number (optional)">
            <input
              className="field mono"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
            />
          </Field>
          <Field label="Installed date (optional)">
            <input
              className="field"
              type="date"
              value={installedOn}
              onChange={(e) => setInstalledOn(e.target.value)}
            />
          </Field>
          <Field label="Notes (optional)">
            <input
              className="field"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </Field>
        </div>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        {machine ? <LifecycleList machine={machine} /> : null}
        {!machine ? (
          <div className="mg-note">
            Replacing a physical Machine? Retire the old Machine record and
            create a new one — the new Machine gets its own barcode and may
            reuse the familiar display name. History always keeps the Machine
            that really did the work.
          </div>
        ) : null}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={save}>
          {machine ? 'Save changes' : 'Add Machine'}
        </button>
      </div>
      {machine ? (
        <div className="mg-dangerzone">
          <div className="dz-title">Danger Zone</div>
          <div className="dz-body">
            <p>
              Retiring removes <b>{machine.name}</b> from every assignment
              choice and stops its barcode from accepting assignment scans.
              History is preserved — the Machine is never deleted.
              {assignedQty > 0 ? (
                <>
                  {' '}
                  Retirement is blocked while <b>{assignedQty} pcs</b> are still
                  assigned.
                </>
              ) : null}
            </p>
            <button className="dz-retire" onClick={startRetire}>
              Retire…
            </button>
          </div>
        </div>
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
          saveLabel="Save changes, then retire"
          discardLabel="Discard changes"
          saveDisabledReason={
            'error' in buildForRetire
              ? `The edits cannot be saved yet: ${buildForRetire.error}`
              : undefined
          }
          onCancel={() => setRetireStage(null)}
          onSave={() => {
            if ('error' in buildForRetire) return;
            onApplyChanges(buildForRetire.machine);
            setBaseline({
              name: buildForRetire.machine.name,
              area: buildForRetire.machine.area,
              barcode: buildForRetire.machine.barcode,
              manufacturer: buildForRetire.machine.manufacturer ?? '',
              model: buildForRetire.machine.model ?? '',
              assetTag: buildForRetire.machine.assetTag ?? '',
              serialNumber: buildForRetire.machine.serialNumber ?? '',
              installedOn: buildForRetire.machine.installedOn ?? '',
              notes: buildForRetire.machine.notes ?? '',
            });
            setRetireStage('confirm');
          }}
          onDiscard={() => {
            setName(baseline.name);
            setBarcode(baseline.barcode);
            setManufacturer(baseline.manufacturer);
            setModel(baseline.model);
            setAssetTag(baseline.assetTag);
            setSerialNumber(baseline.serialNumber);
            setInstalledOn(baseline.installedOn);
            setNotes(baseline.notes);
            setError(null);
            setRetireStage('confirm');
          }}
        >
          This form still has unsaved edits. Retiring never saves them silently
          — choose what happens to the edits before the retirement confirmation
          opens.
        </UnsavedChoiceDialog>
      ) : null}
      {retireStage === 'confirm' && machine && identifier ? (
        <TypedConfirmDialog
          title="Retire Machine"
          expectedValue={identifier.value}
          valueLabel={identifier.label}
          confirmLabel="Retire Machine"
          onCancel={() => setRetireStage(null)}
          onConfirm={onRetire}
        >
          Retiring <b>{machine.name}</b>:
          <ul className="mg-consequences">
            <li>It disappears from Machine assignment choices.</li>
            <li>
              Its barcode (<span className="mono">{machine.barcode}</span>) no
              longer accepts assignment scans.
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
        <Field label="Reason / note (optional)">
          <input
            className="field"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Spindle bearing replacement"
          />
        </Field>
        <Field label="Expected return date (optional)">
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
 * Blocked while its barcode, asset tag or serial number has been
 * reissued to another active Machine, and while the display name would
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
  onCancel,
  onConfirm,
}: {
  machine: MockMachine;
  machines: MockMachine[];
  onCancel: () => void;
  onConfirm: (result: { name: string; area: AreaKey; reason: string }) => void;
}) {
  const [name, setName] = useState(machine.name);
  const [area, setArea] = useState<AreaKey>(machine.area);
  const [reason, setReason] = useState('');
  const [samePhysical, setSamePhysical] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeOnes = machines.filter(
    (m) => m.retiredOn === undefined && m.id !== machine.id,
  );
  // Hard blockers: identity conflicts that make a safe reactivation
  // impossible without fixing other records first.
  const blockers: string[] = [];
  if (activeOnes.some((m) => m.barcode === machine.barcode)) {
    blockers.push(
      `Barcode ${machine.barcode} has been reissued to another active Machine.`,
    );
  }
  if (
    machine.assetTag &&
    activeOnes.some((m) => m.assetTag === machine.assetTag)
  ) {
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

  const confirm = () => {
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
    onConfirm({ name: trimmedName, area, reason: reason.trim() });
  };

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
        <Field label="Display name">
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        {nameCollision ? (
          <div className="err" role="alert">
            An active Machine named “{name.trim()}” already exists in{' '}
            {selectedArea?.name ?? area} — rename one of them to continue.
          </div>
        ) : null}
        <Field label="Current Area after reactivation">
          <div className="mg-areapick">
            <AreaDot
              colorVar={selectedArea?.colorVar ?? 'var(--faint)'}
              size={12}
            />
            <select
              className="field"
              value={area}
              onChange={(e) => setArea(e.target.value as AreaKey)}
            >
              {areaChoices.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
        </Field>
        <div className="mg-note">
          Change the Area only if this physical machine was moved while retired.
          The change applies from reactivation onward — historical Movements
          keep the Areas they were recorded with.
        </div>
        <Field label="Reason (required)">
          <input
            className="field"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Returned from overhaul"
          />
        </Field>
        <label className="mg-check">
          <input
            type="checkbox"
            checked={samePhysical}
            onChange={(e) => setSamePhysical(e.target.checked)}
          />
          This is the same physical machine returning to service — not a
          replacement.
        </label>
        <LifecycleList machine={machine} />
        <div className="mg-recap">
          <div>
            Lifecycle: <b>Retired → Active</b>
            {moved ? (
              <>
                {' '}
                · Area: <b>
                  {areaByKey(machine.area)?.name ?? machine.area}
                </b> → <b>{selectedArea?.name ?? area}</b>
              </>
            ) : null}
          </div>
          <div>
            Recorded for audit: who, when, reason, the state before and after
            {moved ? ', and the previous and current Area' : ''}.
          </div>
        </div>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={blockers.length > 0}
          onClick={confirm}
        >
          Reactivate Machine
        </button>
      </div>
    </ModalDialog>
  );
}
