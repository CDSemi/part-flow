import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  areaColor,
  createArea,
  listAreas,
  listDepartments,
  listOperations,
  updateArea,
} from '../../api/environment';
import type {
  Area,
  Department,
  Operation,
  WorkerIdentificationMode,
} from '../../api/environment';
import { listMachines } from '../../api/machines';
import type { Machine } from '../../api/machines';
import { listWorkers } from '../../api/workers';
import type { Worker } from '../../api/workers';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  ActiveField,
  AdminField,
  SectionHeader,
  ServerErrorNote,
  StatusPill,
} from './section-widgets';
import { WORKER_ID_MODE_LABELS } from './worker-id-modes';

// Administration → Areas (Phase 3.5): the reference table of the
// standard table + editor pattern (GUI_DESIGN §9). Area identity and
// barcode are stable — display name, description and color may change
// without affecting historical Movements. The Machine-assignment mode
// column follows from the Area's Machines (Direct processing / Queue
// → assign), never from a per-count configuration. The Worker ID mode
// (Phase 13) is Disabled, Fixed Worker with its configured Fixed Worker,
// or Scanned session (badge sign-in, Worker Sessions and the
// badge-confirmation gates). The per-Area Worker session timeout
// override is edited in Administration → Worker sessions. The server
// judges every rule (an inactive Fixed Worker, deactivating a Worker who
// is still fixed).

type PendingDialog = { kind: 'new' } | { kind: 'edit'; area: Area };

export function AreasSection() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const areasData = useApiData(listAreas);
  const departmentsData = useApiData(listDepartments);
  const operationsData = useApiData(listOperations);
  const machinesData = useApiData(listMachines);
  const workersData = useApiData(listWorkers);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  const header = (ready: boolean, canCreate: boolean) => (
    <SectionHeader
      title="Areas"
      subtitle="Physical production locations"
      action={
        <button
          className="btn primary"
          disabled={!ready || !canCreate || writeBlocked}
          title={
            ready && !canCreate
              ? 'Areas need an active Department first'
              : undefined
          }
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Area
        </button>
      }
    />
  );

  const reloadAll = () => {
    areasData.reload();
    departmentsData.reload();
    operationsData.reload();
    machinesData.reload();
    workersData.reload();
  };

  if (
    areasData.state.status === 'loading' ||
    departmentsData.state.status === 'loading' ||
    operationsData.state.status === 'loading' ||
    machinesData.state.status === 'loading' ||
    workersData.state.status === 'loading'
  ) {
    return (
      <>
        {header(false, false)}
        <LoadingState label="Loading Areas" />
      </>
    );
  }
  const failed =
    areasData.state.status === 'error'
      ? areasData.state
      : departmentsData.state.status === 'error'
        ? departmentsData.state
        : operationsData.state.status === 'error'
          ? operationsData.state
          : machinesData.state.status === 'error'
            ? machinesData.state
            : workersData.state.status === 'error'
              ? workersData.state
              : null;
  if (
    failed !== null ||
    areasData.state.status !== 'ready' ||
    departmentsData.state.status !== 'ready' ||
    operationsData.state.status !== 'ready' ||
    machinesData.state.status !== 'ready' ||
    workersData.state.status !== 'ready'
  ) {
    return (
      <>
        {header(false, false)}
        <ErrorState
          message="Area data could not be loaded."
          detail={failed?.message}
          onRetry={reloadAll}
        />
      </>
    );
  }

  const areas = areasData.state.data;
  const departments = departmentsData.state.data;
  const operations = operationsData.state.data;
  const machines = machinesData.state.data;
  const workers = workersData.state.data;
  const activeDepartments = departments.filter((d) => d.isActive);

  const completeWrite = () => {
    areasData.reload();
    setDialog(null);
  };

  return (
    <>
      {header(true, activeDepartments.length > 0)}
      {areas.length === 0 || preview === 'empty' ? (
        <EmptyState message="No Areas configured yet." />
      ) : (
        <AreasTable
          areas={areas}
          operations={operations}
          machines={machines}
          onOpenEdit={(area) => setDialog({ kind: 'edit', area })}
        />
      )}
      <div className="ad-notice">
        Area <b>identity and barcode are stable</b> — display name, description
        and color may change without affecting historical Movements. An Area
        supporting multiple Operations (e.g. External) requires Operation
        resolution or confirmation at scan time. Deactivating an Area that still
        holds quantity is blocked with an explanation. In Fixed Worker mode
        every production action at the Area's Scan Stations records the Fixed
        Worker; a Worker who is an Area's Fixed Worker cannot be deactivated.
      </div>
      {dialog?.kind === 'new' ? (
        <AreaDialog
          departments={activeDepartments}
          workers={workers}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await createArea({
              departmentId: input.departmentId,
              name: input.name,
              description: input.description,
              color: input.color ?? null,
              isTerminal: input.isTerminal,
              workerIdentificationMode: input.workerIdentificationMode,
              fixedWorkerId: input.fixedWorkerId,
            });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <AreaDialog
          area={dialog.area}
          departments={departments}
          workers={workers}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await updateArea(dialog.area.id, {
              name: input.name,
              description: input.description,
              ...(input.color !== undefined ? { color: input.color } : {}),
              isTerminal: input.isTerminal,
              isActive: input.isActive,
              workerIdentificationMode: input.workerIdentificationMode,
              fixedWorkerId: input.fixedWorkerId,
            });
            completeWrite();
          }}
        />
      ) : null}
    </>
  );
}

function AreasTable({
  areas,
  operations,
  machines,
  onOpenEdit,
}: {
  areas: Area[];
  operations: Operation[];
  machines: Machine[];
  onOpenEdit: (area: Area) => void;
}) {
  const operationNames = (area: Area): string => {
    const names = operations
      .filter((operation) => operation.areaId === area.id && operation.isActive)
      .map((operation) => operation.name ?? operation.code);
    return names.length > 0 ? names.join(' · ') : '—';
  };
  const activeMachineNames = (area: Area): string[] =>
    machines
      .filter(
        (machine) =>
          machine.areaId === area.id && machine.retiredOn === undefined,
      )
      .map((machine) => machine.name);
  return (
    <table className="ad-table">
      <thead>
        <tr>
          <th>Area</th>
          <th>Operations</th>
          <th>Machine assignment</th>
          <th>Machines</th>
          <th>Worker ID mode</th>
          <th>Terminal</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {areas.map((area) => {
          const machineNames = activeMachineNames(area);
          return (
            <tr
              key={area.id}
              className="selrow"
              onClick={() => onOpenEdit(area)}
            >
              <td>
                <button className="rowbtn" aria-label={`Edit ${area.name}`}>
                  <AreaDot colorVar={areaColor(area)} size={14} />{' '}
                  <b>{area.name}</b>
                </button>
              </td>
              {/* data-label: inline column captions in the collapsed
                  stacked layout (GUI_DESIGN §2.5) — mode values and a
                  bare Machine count are not self-evident without the
                  header row. */}
              <td data-label="Operations">{operationNames(area)}</td>
              <td className="modecell" data-label="Machine assignment">
                {machineNames.length > 0
                  ? 'Queue → assign (one-shot)'
                  : 'Direct processing (no Machines)'}
              </td>
              <td className="mono" data-label="Machines">
                {machineNames.length > 0 ? machineNames.join(' · ') : '—'}
              </td>
              <td className="modecell" data-label="Worker ID mode">
                {WORKER_ID_MODE_LABELS[area.workerIdentificationMode]}
              </td>
              <td>
                {area.isTerminal ? (
                  <span className="pillnav term">Terminal</span>
                ) : null}
              </td>
              <td>
                <StatusPill active={area.isActive} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function AreaDialog({
  area,
  departments,
  workers,
  writeBlocked,
  onCancel,
  onSave,
}: {
  area?: Area;
  departments: Department[];
  /** Every Worker; only active ones can be chosen as Fixed Worker. */
  workers: Worker[];
  writeBlocked: boolean;
  onCancel: () => void;
  /** Persist the entry (`color` undefined = untouched, keep as-is).
   * Rejects with the server's message. */
  onSave: (input: {
    departmentId: number;
    name: string;
    description: string | null;
    color?: string | null;
    isTerminal: boolean;
    isActive: boolean;
    workerIdentificationMode: WorkerIdentificationMode;
    /** The chosen Worker in Fixed Worker mode; null in Disabled mode;
     * absent in Scanned session mode (the server clears a Fixed Worker
     * when the Area leaves Fixed Worker mode). */
    fixedWorkerId?: number | null;
  }) => Promise<void>;
}) {
  const [departmentId, setDepartmentId] = useState(
    area?.departmentId ?? departments[0]?.id ?? 0,
  );
  const [name, setName] = useState(area?.name ?? '');
  const [description, setDescription] = useState(area?.description ?? '');
  const [color, setColor] = useState(area?.color ?? '#8899aa');
  const [colorTouched, setColorTouched] = useState(false);
  const [isTerminal, setIsTerminal] = useState(area?.isTerminal ?? false);
  const [isActive, setIsActive] = useState(area?.isActive ?? true);
  const [workerIdMode, setWorkerIdMode] = useState<WorkerIdentificationMode>(
    area?.workerIdentificationMode ?? 'DISABLED',
  );
  const [fixedWorkerId, setFixedWorkerId] = useState<number | null>(
    area?.fixedWorkerId ?? null,
  );
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const trimmedName = name.trim();
  const departmentName =
    departments.find(
      (department) => department.id === (area?.departmentId ?? departmentId),
    )?.name ?? '—';
  const activeWorkers = workers.filter((worker) => worker.isActive);
  // An inactive current Fixed Worker (impossible through the API, only
  // from old fixtures) stays visible as a disabled option.
  const inactiveFixedWorker = workers.find(
    (worker) => worker.id === fixedWorkerId && !worker.isActive,
  );
  const fixedWorkerMissing = workerIdMode === 'FIXED' && fixedWorkerId === null;
  const submit = async () => {
    if (!trimmedName || fixedWorkerMissing) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      await onSave({
        departmentId,
        name: trimmedName,
        description: description.trim() || null,
        // Only a touched color input changes the stored color — an
        // untouched preview never overwrites it on save.
        ...(area === undefined
          ? { color: colorTouched ? color : null }
          : colorTouched
            ? { color }
            : {}),
        isTerminal,
        isActive,
        workerIdentificationMode: workerIdMode,
        ...(workerIdMode === 'SCANNED'
          ? {}
          : { fixedWorkerId: workerIdMode === 'FIXED' ? fixedWorkerId : null }),
      });
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalDialog label={area ? 'Edit Area' : 'New Area'} onClose={onCancel}>
      <h3>{area ? 'Edit Area' : 'New Area'}</h3>
      {area ? (
        <div className="ad-identity">
          <div className="idrow">
            <span className="k">Barcode</span>
            <span className="v barcodeval">{area.barcodeValue ?? '—'}</span>
          </div>
          <div className="idrow">
            <span className="k">Department</span>
            <span className="v">{departmentName}</span>
          </div>
          <p className="ad-idnote">
            Area identity and barcode are stable — display properties may change
            without affecting historical Movements.
          </p>
        </div>
      ) : null}
      <div className="ad-form">
        {!area ? (
          <AdminField label="Department">
            <select
              className="field"
              value={String(departmentId)}
              onChange={(event) => setDepartmentId(Number(event.target.value))}
            >
              {departments.map((department) => (
                <option key={department.id} value={String(department.id)}>
                  {department.name}
                </option>
              ))}
            </select>
          </AdminField>
        ) : null}
        <AdminField label="Name">
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Lathe"
          />
        </AdminField>
        {!trimmedName && attempted ? (
          <div className="err" role="alert">
            A name is required.
          </div>
        ) : null}
        <AdminField
          label={
            <>
              Description <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="e.g. Turning cell"
          />
        </AdminField>
        <AdminField label="Identity color">
          <input
            className="field ad-color"
            type="color"
            value={color}
            onChange={(event) => {
              setColor(event.target.value);
              setColorTouched(true);
            }}
          />
        </AdminField>
        <AdminField label="Worker ID mode">
          <select
            className="field"
            value={workerIdMode}
            onChange={(event) =>
              setWorkerIdMode(event.target.value as WorkerIdentificationMode)
            }
          >
            <option value="DISABLED">{WORKER_ID_MODE_LABELS.DISABLED}</option>
            <option value="FIXED">{WORKER_ID_MODE_LABELS.FIXED}</option>
            <option value="SCANNED">{WORKER_ID_MODE_LABELS.SCANNED}</option>
          </select>
        </AdminField>
        <div className="ad-fieldnotes">
          <p className="ad-fieldhelp">
            {workerIdMode === 'FIXED'
              ? "Every production action at this Area's Scan Stations records the Fixed Worker."
              : workerIdMode === 'SCANNED'
                ? "Workers sign in at this Area's Scan Stations by scanning their badge. Every production action records the signed-in Worker."
                : "No Worker is recorded for this Area's production activity."}
          </p>
        </div>
        {workerIdMode === 'FIXED' ? (
          <>
            <AdminField label="Fixed Worker">
              <select
                className="field"
                value={fixedWorkerId === null ? '' : String(fixedWorkerId)}
                disabled={activeWorkers.length === 0}
                onChange={(event) =>
                  setFixedWorkerId(
                    event.target.value === ''
                      ? null
                      : Number(event.target.value),
                  )
                }
              >
                <option value="">Choose a Worker</option>
                {inactiveFixedWorker ? (
                  <option value={String(inactiveFixedWorker.id)} disabled>
                    {inactiveFixedWorker.name} (inactive)
                  </option>
                ) : null}
                {activeWorkers.map((worker) => (
                  <option key={worker.id} value={String(worker.id)}>
                    {worker.name} · {worker.badgeBarcode}
                  </option>
                ))}
              </select>
            </AdminField>
            {activeWorkers.length === 0 ? (
              <div className="ad-fieldnotes">
                <p className="ad-fieldhelp">
                  No active Workers — add one in Administration → Workers.
                </p>
              </div>
            ) : null}
            {fixedWorkerMissing && attempted ? (
              <div className="err" role="alert">
                Choose the Fixed Worker.
              </div>
            ) : null}
          </>
        ) : null}
        <ActiveField
          label="Terminal Area — holds completed quantity (e.g. Stockroom)"
          checked={isTerminal}
          onChange={setIsTerminal}
        />
        {area ? (
          <ActiveField
            label="Active — the Area accepts production activity"
            checked={isActive}
            onChange={setIsActive}
          />
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={() => void submit()}
        >
          {area ? 'Save changes' : 'Add Area'}
        </button>
      </div>
    </ModalDialog>
  );
}
