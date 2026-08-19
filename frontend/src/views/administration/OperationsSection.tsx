import { useState } from 'react';

import { errorMessage } from '../../api/client';
import { isoDurationToMinutes, minutesToIsoDuration } from '../../api/duration';
import {
  areaColor,
  createOperation,
  listAreas,
  listOperations,
  updateOperation,
} from '../../api/environment';
import type { Area, Operation } from '../../api/environment';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
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

// Administration → Operations (Phase 3.5): work performed within an
// Area (PROJECT_PROFILE §8.5). Each Operation belongs to exactly one
// Area — the binding is fixed after creation because Movement history
// references Operations in their Area context. Codes stay unique
// within one Area; the expected duration is optional guidance.

type PendingDialog = { kind: 'new' } | { kind: 'edit'; operation: Operation };

export function OperationsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const operationsData = useApiData(listOperations);
  const areasData = useApiData(listAreas);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  const header = (ready: boolean, canCreate: boolean) => (
    <SectionHeader
      title="Operations"
      subtitle="Work performed within an Area (§8.5)"
      action={
        <button
          className="btn primary"
          disabled={!ready || !canCreate || writeBlocked}
          title={
            ready && !canCreate
              ? 'Operations need an active Area first'
              : undefined
          }
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Operation
        </button>
      }
    />
  );

  if (
    operationsData.state.status === 'loading' ||
    areasData.state.status === 'loading'
  ) {
    return (
      <>
        {header(false, false)}
        <LoadingState label="Loading Operations" />
      </>
    );
  }
  if (
    operationsData.state.status === 'error' ||
    areasData.state.status === 'error'
  ) {
    const message =
      operationsData.state.status === 'error'
        ? operationsData.state.message
        : areasData.state.status === 'error'
          ? areasData.state.message
          : undefined;
    return (
      <>
        {header(false, false)}
        <ErrorState
          message="Operation data could not be loaded."
          detail={message}
          onRetry={() => {
            operationsData.reload();
            areasData.reload();
          }}
        />
      </>
    );
  }

  const operations = operationsData.state.data;
  const areas = areasData.state.data;
  const areaById = new Map(areas.map((area) => [area.id, area]));
  const activeAreas = areas.filter((area) => area.isActive);

  const completeWrite = () => {
    operationsData.reload();
    setDialog(null);
  };

  const durationLabel = (operation: Operation): string => {
    if (operation.defaultExpectedDuration === null) return '—';
    const minutes = isoDurationToMinutes(operation.defaultExpectedDuration);
    return minutes === null
      ? operation.defaultExpectedDuration
      : `${minutes} min`;
  };

  return (
    <>
      {header(true, activeAreas.length > 0)}
      {operations.length === 0 ? (
        <EmptyState message="No Operations configured yet." />
      ) : (
        <table className="ad-table">
          <thead>
            <tr>
              <th>Operation</th>
              <th>Area</th>
              <th>Expected duration</th>
              <th>External</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {operations.map((operation) => {
              const area = areaById.get(operation.areaId);
              return (
                <tr
                  key={operation.id}
                  className="selrow"
                  onClick={() => setDialog({ kind: 'edit', operation })}
                >
                  <td>
                    <button
                      className="rowbtn"
                      aria-label={`Edit ${operation.name ?? operation.code}`}
                    >
                      <b>{operation.name ?? operation.code}</b>{' '}
                      <span className="mono ad-opcode">{operation.code}</span>
                    </button>
                  </td>
                  <td data-label="Area">
                    <AreaDot colorVar={areaColor(area)} size={11} />{' '}
                    {area?.name ?? '—'}
                  </td>
                  <td className="mono" data-label="Expected duration">
                    {durationLabel(operation)}
                  </td>
                  <td data-label="External">
                    {operation.isExternal ? (
                      <span className="pillnav term">External</span>
                    ) : null}
                  </td>
                  <td>
                    <StatusPill active={operation.isActive} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="ad-notice">
        An Operation belongs to exactly one Area — the binding is fixed after
        creation, because Movement history references Operations in their Area
        context. Codes stay unique within one Area. External Operations mark
        work performed at outside vendors.
      </div>
      {dialog?.kind === 'new' ? (
        <OperationDialog
          areas={activeAreas}
          areaById={areaById}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await createOperation({
              areaId: input.areaId,
              code: input.code,
              name: input.name,
              description: input.description,
              defaultExpectedDuration: input.defaultExpectedDuration,
              isExternal: input.isExternal,
            });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <OperationDialog
          operation={dialog.operation}
          areas={activeAreas}
          areaById={areaById}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await updateOperation(dialog.operation.id, {
              code: input.code,
              name: input.name,
              description: input.description,
              defaultExpectedDuration: input.defaultExpectedDuration,
              isExternal: input.isExternal,
              isActive: input.isActive,
            });
            completeWrite();
          }}
        />
      ) : null}
    </>
  );
}

function OperationDialog({
  operation,
  areas,
  areaById,
  writeBlocked,
  onCancel,
  onSave,
}: {
  operation?: Operation;
  areas: Area[];
  areaById: Map<number, Area>;
  writeBlocked: boolean;
  onCancel: () => void;
  /** Persist the entry. Rejects with the server's message. */
  onSave: (input: {
    areaId: number;
    code: string;
    name: string | null;
    description: string | null;
    defaultExpectedDuration: string | null;
    isExternal: boolean;
    isActive: boolean;
  }) => Promise<void>;
}) {
  const [areaId, setAreaId] = useState(operation?.areaId ?? areas[0]?.id ?? 0);
  const [code, setCode] = useState(operation?.code ?? '');
  const [name, setName] = useState(operation?.name ?? '');
  const [description, setDescription] = useState(operation?.description ?? '');
  const [minutesText, setMinutesText] = useState(() => {
    if (!operation?.defaultExpectedDuration) return '';
    const minutes = isoDurationToMinutes(operation.defaultExpectedDuration);
    return minutes === null ? '' : String(minutes);
  });
  const [isExternal, setIsExternal] = useState(operation?.isExternal ?? false);
  const [isActive, setIsActive] = useState(operation?.isActive ?? true);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const trimmedCode = code.trim();
  // The COMPLETE entered value must be a whole minute count above zero
  // — a fractional or otherwise malformed entry is rejected, never
  // silently truncated to some other number (blank stays valid and
  // persists no duration).
  const trimmedMinutes = minutesText.trim();
  const parsedMinutes = trimmedMinutes === '' ? null : Number(trimmedMinutes);
  const minutesInvalid =
    parsedMinutes !== null &&
    (!Number.isInteger(parsedMinutes) || parsedMinutes <= 0);
  const fixedArea = operation ? areaById.get(operation.areaId) : undefined;

  const submit = async () => {
    if (!trimmedCode || minutesInvalid) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      await onSave({
        areaId,
        code: trimmedCode,
        name: name.trim() || null,
        description: description.trim() || null,
        defaultExpectedDuration:
          parsedMinutes === null ? null : minutesToIsoDuration(parsedMinutes),
        isExternal,
        isActive,
      });
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalDialog
      label={operation ? 'Edit Operation' : 'New Operation'}
      onClose={onCancel}
    >
      <h3>{operation ? 'Edit Operation' : 'New Operation'}</h3>
      <div className="ad-form">
        {operation ? (
          <div className="ad-identity">
            <div className="idrow">
              <span className="k">Area</span>
              <span className="v">
                <AreaDot colorVar={areaColor(fixedArea)} size={11} />{' '}
                {fixedArea?.name ?? '—'}
              </span>
            </div>
            <p className="ad-idnote">
              The Area binding is fixed — Movement history references this
              Operation in its Area context.
            </p>
          </div>
        ) : (
          <AdminField label="Area">
            <select
              className="field"
              value={String(areaId)}
              onChange={(event) => setAreaId(Number(event.target.value))}
            >
              {areas.map((area) => (
                <option key={area.id} value={String(area.id)}>
                  {area.name}
                </option>
              ))}
            </select>
          </AdminField>
        )}
        <AdminField label="Code">
          <input
            className="field mono"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="e.g. TURN"
          />
        </AdminField>
        {!trimmedCode && attempted ? (
          <div className="err" role="alert">
            A code is required.
          </div>
        ) : null}
        <AdminField
          label={
            <>
              Name <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Turning"
          />
        </AdminField>
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
            placeholder="e.g. CNC turning of shaft blanks"
          />
        </AdminField>
        <AdminField
          label={
            <>
              Expected duration in minutes{' '}
              <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field mono"
            type="number"
            min={1}
            value={minutesText}
            onChange={(event) => setMinutesText(event.target.value)}
            placeholder="e.g. 30"
          />
        </AdminField>
        {minutesInvalid ? (
          <div className="err" role="alert">
            The expected duration must be a whole number of minutes above zero.
          </div>
        ) : null}
        <ActiveField
          label="External — performed at an outside vendor"
          checked={isExternal}
          onChange={setIsExternal}
        />
        {operation ? (
          <ActiveField
            label="Active — the Operation is selectable"
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
          {operation ? 'Save changes' : 'Add Operation'}
        </button>
      </div>
    </ModalDialog>
  );
}
