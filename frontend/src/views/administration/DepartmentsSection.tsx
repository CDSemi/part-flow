import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  createDepartment,
  listAreas,
  listDepartments,
  updateDepartment,
} from '../../api/environment';
import type { Department } from '../../api/environment';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
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

// Administration → Departments (Phase 3.5): organizational production
// units. The standard table + editor pattern — Departments are
// created and edited here and deactivated, never deleted. The server
// owns the hierarchy rule: a Department with active Areas cannot be
// deactivated (the rejected save shows its explanation).

type PendingDialog = { kind: 'new' } | { kind: 'edit'; department: Department };

export function DepartmentsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const departmentsData = useApiData(listDepartments);
  const areasData = useApiData(listAreas);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  const header = (ready: boolean) => (
    <SectionHeader
      title="Departments"
      subtitle="Organizational production units"
      action={
        <button
          className="btn primary"
          disabled={!ready || writeBlocked}
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Department
        </button>
      }
    />
  );

  if (
    departmentsData.state.status === 'loading' ||
    areasData.state.status === 'loading'
  ) {
    return (
      <>
        {header(false)}
        <LoadingState label="Loading Departments" />
      </>
    );
  }
  if (departmentsData.state.status === 'error') {
    return (
      <>
        {header(false)}
        <ErrorState
          message="Department data could not be loaded."
          detail={departmentsData.state.message}
          onRetry={departmentsData.reload}
        />
      </>
    );
  }
  if (areasData.state.status === 'error') {
    return (
      <>
        {header(false)}
        <ErrorState
          message="Department data could not be loaded."
          detail={areasData.state.message}
          onRetry={areasData.reload}
        />
      </>
    );
  }

  const departments = departmentsData.state.data;
  const areas = areasData.state.data;
  const areaCount = (departmentId: number) =>
    areas.filter((area) => area.departmentId === departmentId).length;

  const completeWrite = () => {
    departmentsData.reload();
    setDialog(null);
  };

  return (
    <>
      {header(true)}
      {departments.length === 0 ? (
        <EmptyState message="No Departments configured yet." />
      ) : (
        <table className="ad-table">
          <thead>
            <tr>
              <th>Department</th>
              <th>Areas</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {departments.map((department) => (
              <tr
                key={department.id}
                className="selrow"
                onClick={() => setDialog({ kind: 'edit', department })}
              >
                <td>
                  <button
                    className="rowbtn"
                    aria-label={`Edit ${department.name}`}
                  >
                    <b>{department.name}</b>
                  </button>
                </td>
                <td className="mono" data-label="Areas">
                  {areaCount(department.id)}
                </td>
                <td>
                  <StatusPill active={department.isActive} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="ad-notice">
        A Department groups Areas for organization and display settings. A
        Department with active Areas cannot be deactivated — deactivate its
        Areas first.
      </div>
      {dialog?.kind === 'new' ? (
        <DepartmentDialog
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await createDepartment({ name: input.name });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <DepartmentDialog
          department={dialog.department}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await updateDepartment(dialog.department.id, {
              name: input.name,
              isActive: input.isActive,
            });
            completeWrite();
          }}
        />
      ) : null}
    </>
  );
}

function DepartmentDialog({
  department,
  writeBlocked,
  onCancel,
  onSave,
}: {
  department?: Department;
  writeBlocked: boolean;
  onCancel: () => void;
  /** Persist the entry. Rejects with the server's message. */
  onSave: (input: { name: string; isActive: boolean }) => Promise<void>;
}) {
  const [name, setName] = useState(department?.name ?? '');
  const [isActive, setIsActive] = useState(department?.isActive ?? true);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const trimmedName = name.trim();
  const submit = async () => {
    if (!trimmedName) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      await onSave({ name: trimmedName, isActive });
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalDialog
      label={department ? 'Edit Department' : 'New Department'}
      onClose={onCancel}
    >
      <h3>{department ? 'Edit Department' : 'New Department'}</h3>
      <div className="ad-form">
        <AdminField label="Name">
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Machine Shop"
          />
        </AdminField>
        {!trimmedName && attempted ? (
          <div className="err" role="alert">
            A name is required.
          </div>
        ) : null}
        {department ? (
          <ActiveField
            label="Active — the Department's Areas can operate"
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
          {department ? 'Save changes' : 'Add Department'}
        </button>
      </div>
    </ModalDialog>
  );
}
