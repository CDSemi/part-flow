import './machines.css';

import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { DevNotice } from '../../components/DevNotice';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { MOCK_AREA_CARDS } from '../../mocks/area-board';
import { MOCK_AREAS, areaByKey } from '../../mocks/areas';
import { MOCK_MACHINES } from '../../mocks/machines';
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
  | { kind: 'retire'; machine: MockMachine };

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
              <th>State</th>
              <th>Assigned now</th>
              <th className="mg-metacol">Asset</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {active.map((machine) => (
              <ActiveMachineRow
                key={machine.id}
                machine={machine}
                assignments={assignmentsById.get(machine.id) ?? []}
                onAction={(kind) => setDialog({ kind, machine })}
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
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mg-sub">
            Retired Machines stay visible here and in history — they accept no
            new work and never appear in assignment choices. A replacement
            Machine may reuse the display name of the floor position; the asset
            tag keeps the physical Machines apart.
          </p>
        </div>
      ) : null}

      {dialog?.kind === 'new' ? (
        <MachineEditDialog
          machines={machines}
          onCancel={() => setDialog(null)}
          onSave={(machine) => {
            setMachines((current) => [...current, machine]);
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <MachineEditDialog
          machines={machines}
          machine={dialog.machine}
          onCancel={() => setDialog(null)}
          onSave={(machine) => {
            update(machine.id, () => machine);
            setDialog(null);
          }}
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
          cancelLabel="Cancel"
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
      {dialog?.kind === 'retire' ? (
        <RetireDialog
          machine={dialog.machine}
          assignedQty={assignedQty(dialog.machine)}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const today = new Date().toISOString().slice(0, 10);
            update(dialog.machine.id, (m) => ({
              ...m,
              retiredOn: today,
              maintenance: undefined,
              stateChangedAt: new Date().toISOString(),
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
        <div>
          Asset <span className="tagv">{machine.assetTag}</span>
        </div>
      ) : null}
      <div>
        {[machine.manufacturer, machine.model].filter(Boolean).join(' ') || '—'}
      </div>
    </div>
  );
}

function ActiveMachineRow({
  machine,
  assignments,
  onAction,
}: {
  machine: MockMachine;
  assignments: { pn: string; qty: number }[];
  onAction: (
    kind: 'edit' | 'start-maintenance' | 'clear-maintenance' | 'retire',
  ) => void;
}) {
  const qty = assignments.reduce((s, a) => s + a.qty, 0);
  const status = effectiveMachineStatus(machine, qty);
  return (
    <tr>
      <td>
        <MachineIdentityCell machine={machine} />
      </td>
      <td>
        <span className={`mg-state ${status}`}>
          {MACHINE_STATE_LABEL[status]}{' '}
          <span className="age">
            · {formatStateAge(machine.stateChangedAt)}
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
              {a.pn} · <span className="q">{a.qty}</span> pcs
            </div>
          ))
        )}
      </td>
      <td className="mg-metacol">
        <AssetMeta machine={machine} />
      </td>
      <td>
        <div className="mg-actions">
          {machine.maintenance ? (
            <button onClick={() => onAction('clear-maintenance')}>
              Clear maintenance
            </button>
          ) : (
            <button
              className="warn"
              onClick={() => onAction('start-maintenance')}
            >
              Start maintenance
            </button>
          )}
          <button onClick={() => onAction('edit')}>Edit…</button>
          <button className="danger" onClick={() => onAction('retire')}>
            Retire…
          </button>
        </div>
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

/**
 * Add or edit one Machine. The Area of an existing Machine is fixed —
 * a Machine belongs to exactly one Area; moving production capacity is
 * a replacement (retire + new record), never an edit that would make
 * history ambiguous. All asset metadata stays optional.
 */
function MachineEditDialog({
  machines,
  machine,
  onCancel,
  onSave,
}: {
  machines: MockMachine[];
  machine?: MockMachine;
  onCancel: () => void;
  onSave: (machine: MockMachine) => void;
}) {
  const [name, setName] = useState(machine?.name ?? '');
  const [area, setArea] = useState<AreaKey>(machine?.area ?? 'lathe');
  const [barcode, setBarcode] = useState(machine?.barcode ?? '');
  const [manufacturer, setManufacturer] = useState(machine?.manufacturer ?? '');
  const [model, setModel] = useState(machine?.model ?? '');
  const [assetTag, setAssetTag] = useState(machine?.assetTag ?? '');
  const [serialNumber, setSerialNumber] = useState(machine?.serialNumber ?? '');
  const [installedOn, setInstalledOn] = useState(machine?.installedOn ?? '');
  const [notes, setNotes] = useState(machine?.notes ?? '');
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const trimmedBarcode = barcode.trim();
    if (!trimmedName) {
      setError('A display name is required.');
      return;
    }
    if (!trimmedBarcode) {
      setError('A barcode value is required.');
      return;
    }
    const duplicate = machines.some(
      (m) => m.id !== machine?.id && m.barcode === trimmedBarcode,
    );
    if (duplicate) {
      setError('That barcode is already used by another Machine.');
      return;
    }
    onSave({
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
    });
  };

  return (
    <ModalDialog
      label={machine ? 'Edit Machine' : 'New Machine'}
      onClose={onCancel}
      size="wide"
    >
      <h3>{machine ? 'Edit Machine' : 'New Machine'}</h3>
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
              {areaByKey(machine.area)?.name ?? machine.area} — fixed; a Machine
              belongs to one Area
            </div>
          </>
        ) : (
          <Field label="Area">
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
          Cancel
        </button>
        <button className="bigbtn primary" onClick={save}>
          {machine ? 'Save changes' : 'Add Machine'}
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
          Cancel
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
 * Retirement ends the lifecycle of one physical Machine record. It is
 * blocked while active quantity is still assigned — the quantity must
 * first be completed or transferred through the normal production
 * workflow. History is never rewritten; the record stays visible under
 * Retired Machines.
 */
function RetireDialog({
  machine,
  assignedQty,
  onCancel,
  onConfirm,
}: {
  machine: MockMachine;
  assignedQty: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (assignedQty > 0) {
    return (
      <ModalDialog label="Cannot retire Machine" onClose={onCancel}>
        <h3>Cannot retire Machine</h3>
        <div className="sub">
          <b>{machine.name}</b> still has <b>{assignedQty} pcs</b> assigned.
          Complete or transfer that quantity through the normal production
          workflow first, then retire the Machine.
        </div>
        <div className="row">
          <button className="bigbtn ghost" onClick={onCancel}>
            Close
          </button>
        </div>
      </ModalDialog>
    );
  }
  return (
    <ConfirmDialog
      title="Retire Machine"
      confirmLabel="Retire Machine"
      cancelLabel="Cancel"
      danger
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <b>{machine.name}</b>
      {machine.assetTag ? (
        <>
          {' '}
          (asset <b>{machine.assetTag}</b>)
        </>
      ) : null}{' '}
      stops accepting new work and moves to Retired Machines. All history keeps
      its reference to this Machine, and a replacement Machine may reuse the
      display name with its own new barcode.
    </ConfirmDialog>
  );
}
