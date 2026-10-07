import './planned-routes.css';

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, DragEvent, ReactNode } from 'react';

import { ApiError } from '../../api/client';
import { isoDurationToMinutes, minutesToIsoDuration } from '../../api/duration';
import { areaColor, listAreas, listOperations } from '../../api/environment';
import type { Area, Operation } from '../../api/environment';
import { listMachines } from '../../api/machines';
import type { Machine } from '../../api/machines';
import {
  archiveRouteTemplate,
  createRouteTemplate,
  deleteRouteTemplate,
  getRouteTemplateUsage,
  listRouteTemplateRecords,
  replaceRouteTemplate,
} from '../../api/route-templates';
import type {
  RouteStepInput,
  RouteTemplateInput,
  RouteTemplateRecord,
} from '../../api/route-templates';
import { writeOutcomeUnknown } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { MANAGEMENT_WRITE_ACCESS } from '../../app/management-access';
import { useSession } from '../../app/session-context';
import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { TypedConfirmDialog } from '../../components/TypedConfirmDialog';
import { UnsavedChoiceDialog } from '../../components/UnsavedChoiceDialog';
import { ViewOnlyPageNote } from '../../components/ViewOnlyPageNote';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { operationLabel } from '../area-presentation';
import { formatIsoDate } from '../dates';
import { formatEstimate, parseEstimate } from './route-duration';

// Management → Planned Routes: reusable route definitions (internal
// name: RouteTemplate) owned by authorized production roles — full
// Administrator access is deliberately NOT required. A REAL view on the
// `/api/route-templates` surface since Phase 13. The name `Planned
// Routes` keeps it clearly apart from the Floating actual route traces
// shown in Tracking. Editing a route affects FUTURE assignments only:
// a released Quantity Flow keeps its independent Assigned Route
// snapshot, and any intentional change to an in-production Assigned
// Route happens in its own audited workflow (Tracking → Edit assigned
// Route), never here. A route that has ever been used is archived
// instead of deleted (a never-used route may still be deleted
// outright); existing snapshots preserve historical route definitions,
// so no separate template-versioning system exists.
//
// Step references (Area, Operation, preferred Machine) are advisory
// configuration: a stored value that is no longer offered stays
// visible as `(unavailable)` — never silently replaced or cleared —
// and must be chosen again before the route can be saved; the server
// validates every step of every save.

const UNKNOWN_SAVE_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the route before trying again.';
const UNKNOWN_ARCHIVE_MESSAGE =
  'The server did not answer — the route may or may not have been archived. Close this window to refresh the list, then check the route.';
const UNKNOWN_DELETE_MESSAGE =
  'The server did not answer — the route may or may not have been deleted. Close this window to refresh the list, then check the route.';

/** The configuration a route editor offers its choices from. */
interface Catalog {
  areas: Area[];
  operations: Operation[];
  machines: Machine[];
}

interface PlannedRoutesData extends Catalog {
  records: RouteTemplateRecord[];
}

async function loadPlannedRoutes(): Promise<PlannedRoutesData> {
  const [records, areas, operations, machines] = await Promise.all([
    listRouteTemplateRecords(),
    listAreas(),
    listOperations(),
    listMachines(),
  ]);
  return { records, areas, operations, machines };
}

/** Open dialogs carry the catalog they were opened with, so a list
 * refresh (or a failing one) never unmounts an open editor. */
type PendingDialog =
  | {
      kind: 'new';
      catalog: Catalog;
      initial?: RouteTemplateInput;
      initialError?: string;
    }
  | { kind: 'edit'; catalog: Catalog; record: RouteTemplateRecord }
  | { kind: 'usage'; record: RouteTemplateRecord }
  | { kind: 'notice'; message: string };

const byId = <T extends { id: number }>(a: T, b: T): number => a.id - b.id;

function findById<T extends { id: number }>(
  items: readonly T[],
  id: number | null,
): T | undefined {
  return id === null ? undefined : items.find((item) => item.id === id);
}

/** The Area's active Operations, by id. */
function offeredOperations(catalog: Catalog, areaId: number): Operation[] {
  return catalog.operations
    .filter((op) => op.areaId === areaId && op.isActive)
    .sort(byId);
}

/** The Area's non-retired Machines, by id. */
function offeredMachines(catalog: Catalog, areaId: number): Machine[] {
  return catalog.machines
    .filter((m) => m.areaId === areaId && m.retiredOn === undefined)
    .sort(byId);
}

function activeAreas(catalog: Catalog): Area[] {
  return catalog.areas.filter((area) => area.isActive);
}

/** The template copied as a write request — every step field, the
 * stored ISO durations verbatim. */
function recordInput(record: RouteTemplateRecord): RouteTemplateInput {
  return {
    name: record.name,
    description: record.description,
    steps: record.steps.map((step) => ({
      areaId: step.areaId,
      operationId: step.operationId,
      expectedDuration: step.expectedDuration,
      preferredMachineId: step.preferredMachineId,
      instructions: step.instructions,
    })),
  };
}

// Long-data preview routes (?state=long, development builds only): many
// routes plus one with an over-long name/description and a long step
// chain, built from the loaded Areas and Operations to exercise
// dense-table and step-chip wrapping. Never part of the server data —
// negative ids never match a server row, and the editor opens them
// read-only (nothing is ever written for them).
const longPreviewRoutes: ((catalog: Catalog) => RouteTemplateRecord[]) | null =
  import.meta.env.DEV
    ? (catalog: Catalog) => {
        const areas = activeAreas(catalog);
        if (areas.length === 0) return [];
        const chain = (length: number) =>
          Array.from({ length }, (_, i) => {
            const area = areas[i % areas.length];
            return {
              id: -(i + 1),
              sequence: i + 1,
              areaId: area.id,
              operationId: offeredOperations(catalog, area.id)[0]?.id ?? null,
              expectedDuration: i % 2 === 0 ? 'PT4H' : null,
              preferredMachineId: null,
              instructions:
                i === 2
                  ? 'Face and turn per drawing; check shoulder depth and verify runout before handoff.'
                  : null,
            };
          });
        const preview = (
          id: number,
          name: string,
          description: string,
          length: number,
        ) => ({
          id,
          name,
          description,
          steps: chain(length),
          archivedAt: null,
          archivedOn: null,
          updatedOn: '2026-07-01',
          everUsed: false,
          usageCount: 0,
        });
        return [
          ...Array.from({ length: 12 }, (_, i) =>
            preview(
              -(i + 1),
              `Long preview route variant ${i + 1} — extended qualification cell`,
              'Auto-generated long-data preview route for layout testing only.',
              5,
            ),
          ),
          preview(
            -13,
            'Supplemental long-preview route — multi-stage housing assembly with outside plating, secondary deburr, and final inspection rework loop',
            'Long-data preview: an over-long route name and description plus many ordered steps, to exercise step-chip wrapping and the full-width dense table layout.',
            10,
          ),
        ];
      }
    : null;

/** Area-colored step chips — the same reading direction as the
 * Tracking route visualization, each block tinted with its Area color
 * (tinted surface + Area dot; the text keeps the normal color). */
function StepChips({
  record,
  catalog,
}: {
  record: RouteTemplateRecord;
  catalog: Catalog;
}) {
  return (
    <div className="rt-steps">
      {record.steps.map((step, i) => {
        const area = findById(catalog.areas, step.areaId);
        const areaName = area?.name ?? `Area ${step.areaId}`;
        const operation = findById(catalog.operations, step.operationId);
        const opText =
          step.operationId === null
            ? '—'
            : operation
              ? operationLabel(operation)
              : `Operation ${step.operationId}`;
        const colorVar = areaColor(area);
        return (
          <Fragment key={step.id}>
            {i > 0 ? (
              <span className="arw" aria-hidden="true">
                →
              </span>
            ) : null}
            <span
              className="rt-stepchip"
              style={{ '--acol': colorVar } as CSSProperties}
              title={`${areaName} — ${opText}`}
            >
              <AreaDot colorVar={colorVar} size={8} />
              {areaName}
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

function UsageButton({
  record,
  onOpen,
}: {
  record: RouteTemplateRecord;
  onOpen: () => void;
}) {
  if (record.usageCount === 0) return <span className="never">Never used</span>;
  return (
    <button onClick={onOpen}>
      {record.usageCount} Quantity Flow
      {record.usageCount === 1 ? '' : 's'}…
    </button>
  );
}

export function PlannedRoutesView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  // Without Manage Planned Routes every change is hidden (Phase 14
  // slice 3): active rows open nothing (Duplicate, Archive and Delete
  // live in their editor), archived rows offer no Duplicate; Used by
  // stays. The server checks every write itself.
  const canManage = useSession().can('MANAGE_ROUTE_TEMPLATES');
  const { state, reload } = useApiData(loadPlannedRoutes);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  // A new key per opened dialog: a Duplicate replaces the open editor
  // with a fresh one even when it is the same kind of dialog.
  const [dialogKey, setDialogKey] = useState(0);
  const [duplicating, setDuplicating] = useState(false);

  const openDialog = (next: PendingDialog | null) => {
    setDialog(next);
    setDialogKey((key) => key + 1);
  };

  /**
   * Duplicate (client-side read + server create): `{name} (variant)`
   * with every step copied. Success opens the new never-used route for
   * editing; a refusal opens `New Planned Route` prefilled with the
   * copy and the server's message (nothing was created). Returns the
   * unknown-outcome message when the server did not answer, timed out
   * or failed (408 / 5xx — the copy may have been created) — the
   * caller shows it; nothing is retried.
   */
  const duplicate = async (
    source: RouteTemplateInput,
    catalog: Catalog,
  ): Promise<string | null> => {
    const copy: RouteTemplateInput = {
      ...source,
      name: `${source.name} (variant)`,
    };
    try {
      const created = await createRouteTemplate(copy);
      reload();
      openDialog({ kind: 'edit', catalog, record: created });
      return null;
    } catch (error) {
      if (error instanceof ApiError && !writeOutcomeUnknown(error)) {
        reload();
        openDialog({
          kind: 'new',
          catalog,
          initial: copy,
          initialError: error.message,
        });
        return null;
      }
      return UNKNOWN_SAVE_MESSAGE;
    }
  };

  const duplicateRow = async (
    record: RouteTemplateRecord,
    catalog: Catalog,
  ) => {
    if (duplicating) return;
    setDuplicating(true);
    const problem = await duplicate(recordInput(record), catalog);
    setDuplicating(false);
    if (problem !== null) openDialog({ kind: 'notice', message: problem });
  };

  const closeEditor = (result: { wrote: boolean }) => {
    openDialog(null);
    if (result.wrote) reload();
  };

  const data = state.status === 'ready' ? state.data : null;
  const query = search.trim().toLowerCase();

  let body: ReactNode;
  if (preview === 'loading' || state.status === 'loading') {
    body = <LoadingState label="Loading Planned Routes" />;
  } else if (preview === 'error') {
    body = (
      <ErrorState
        message="Planned Route data could not be loaded."
        detail="Check the backend connection and try again."
      />
    );
  } else if (state.status === 'error') {
    body = (
      <ErrorState
        message="Planned Route data could not be loaded."
        detail={state.message}
        onRetry={reload}
      />
    );
  } else {
    const catalog: Catalog = state.data;
    const longRows =
      preview === 'long' && longPreviewRoutes ? longPreviewRoutes(catalog) : [];
    const records =
      preview === 'empty' ? [] : [...state.data.records, ...longRows];
    const visible = records.filter(
      (record) =>
        !query ||
        [
          record.name,
          record.description ?? '',
          ...record.steps.map((step) => {
            const operation = findById(catalog.operations, step.operationId);
            return operation ? operationLabel(operation) : '';
          }),
        ]
          .join(' ')
          .toLowerCase()
          .includes(query),
    );
    const activeRecords = visible.filter((r) => r.archivedAt === null);
    const archivedRecords = visible.filter((r) => r.archivedAt !== null);
    body = (
      <>
        {visible.length === 0 ? (
          <EmptyState
            message={
              query
                ? `No Planned Routes match “${search.trim()}”.`
                : 'No Planned Routes defined yet.'
            }
          />
        ) : null}

        {activeRecords.length > 0 ? (
          <table className="rt-table">
            <thead>
              <tr>
                <th>Planned Route</th>
                <th>Steps</th>
                <th>Status</th>
                <th>Used by</th>
              </tr>
            </thead>
            <tbody>
              {activeRecords.map((record) => (
                // The COMPLETE row opens Edit Planned Route (v15): the
                // name-cell button is the keyboard and screen-reader
                // entry point; the usage cell is the one interactive
                // island and stops propagation.
                <tr
                  key={record.id}
                  className={canManage ? 'selrow' : undefined}
                  onClick={
                    canManage
                      ? () => openDialog({ kind: 'edit', catalog, record })
                      : undefined
                  }
                >
                  <td>
                    {canManage ? (
                      <button
                        className="rowbtn"
                        aria-label={`Edit ${record.name}`}
                      >
                        <div className="rtname">{record.name}</div>
                        {record.description ? (
                          <div className="rtdesc">{record.description}</div>
                        ) : null}
                      </button>
                    ) : (
                      <>
                        <div className="rtname">{record.name}</div>
                        {record.description ? (
                          <div className="rtdesc">{record.description}</div>
                        ) : null}
                      </>
                    )}
                  </td>
                  <td>
                    <StepChips record={record} catalog={catalog} />
                  </td>
                  <td>
                    <span className="rt-status active">Active</span>
                    <div className="rt-statusdate">
                      updated {formatIsoDate(record.updatedOn)}
                    </div>
                  </td>
                  {/* data-label: inline column caption in the collapsed
                      stacked layout (GUI_DESIGN §2.5) — a bare flow
                      count is not self-evident without the header row. */}
                  <td
                    className="rt-usage"
                    data-label="Used by"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <UsageButton
                      record={record}
                      onOpen={() => openDialog({ kind: 'usage', record })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        {archivedRecords.length > 0 ? (
          <div className="rt-archived">
            <h2>Archived Routes</h2>
            <table className="rt-table">
              <thead>
                <tr>
                  <th>Planned Route</th>
                  <th>Steps</th>
                  <th>Archived</th>
                  <th>Used by</th>
                  {canManage ? (
                    <th>
                      <span className="rt-visuallyquiet">Duplicate</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {archivedRecords.map((record) => (
                  <tr key={record.id} className="archived">
                    <td>
                      <div className="rtname">{record.name}</div>
                      {record.description ? (
                        <div className="rtdesc">{record.description}</div>
                      ) : null}
                    </td>
                    <td>
                      <StepChips record={record} catalog={catalog} />
                    </td>
                    <td>
                      <span className="rt-status archived">Archived</span>
                      <div className="rt-statusdate">
                        since {formatIsoDate(record.archivedOn)}
                      </div>
                    </td>
                    <td className="rt-usage" data-label="Used by">
                      <UsageButton
                        record={record}
                        onOpen={() => openDialog({ kind: 'usage', record })}
                      />
                    </td>
                    {canManage ? (
                      <td>
                        <button
                          className="rt-duplicate"
                          disabled={writeBlocked || duplicating}
                          onClick={() => void duplicateRow(record, catalog)}
                        >
                          Duplicate
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
            <PageNote>
              Archived routes stay here for historical context — they are never
              offered when a new Quantity Flow is released. Duplicate creates a
              new active route from one.
            </PageNote>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section className="rt" aria-label="Planned Routes">
      <h1>Planned Routes</h1>
      <p className="rt-sub">
        Reusable route definitions assigned to Quantity Flows at release, for
        authorized production roles. Editing a route changes future assignments
        only — quantity already in production keeps the route it was released
        with, and actual Movement history stays authoritative.
      </p>
      {canManage ? null : (
        <ViewOnlyPageNote
          permissions={MANAGEMENT_WRITE_ACCESS['planned-routes']}
        />
      )}
      <div className="rt-toolbar">
        <input
          type="search"
          placeholder="Search: route name, Operation…"
          aria-label="Search Planned Routes"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="spacer" />
        {canManage ? (
          <button
            className="btn primary"
            disabled={writeBlocked || data === null}
            onClick={() => {
              if (data) openDialog({ kind: 'new', catalog: data });
            }}
          >
            + New Planned Route
          </button>
        ) : null}
      </div>

      {body}

      {dialog?.kind === 'new' ? (
        <RouteEditDialog
          key={dialogKey}
          catalog={dialog.catalog}
          initial={dialog.initial}
          initialError={dialog.initialError}
          writeBlocked={writeBlocked}
          onClose={closeEditor}
          onDuplicate={(source) => duplicate(source, dialog.catalog)}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <RouteEditDialog
          key={dialogKey}
          record={dialog.record}
          catalog={dialog.catalog}
          writeBlocked={writeBlocked}
          onClose={closeEditor}
          onDuplicate={(source) => duplicate(source, dialog.catalog)}
        />
      ) : null}
      {dialog?.kind === 'usage' ? (
        <UsageDialog
          key={dialogKey}
          record={dialog.record}
          onClose={() => openDialog(null)}
        />
      ) : null}
      {dialog?.kind === 'notice' ? (
        <ModalDialog
          key={dialogKey}
          label="Duplicate Planned Route"
          onClose={() => closeEditor({ wrote: true })}
        >
          <h3>Duplicate Planned Route</h3>
          <div className="sub" role="alert">
            {dialog.message}
          </div>
          <div className="row">
            <button
              className="bigbtn ghost"
              onClick={() => closeEditor({ wrote: true })}
            >
              Close
            </button>
          </div>
        </ModalDialog>
      ) : null}
    </section>
  );
}

/**
 * Where a route has been used: the Quantity Flows released with an
 * Assigned Route snapshot copied from it, newest first. Those
 * snapshots are independent — changing or archiving the route never
 * touches them.
 */
function UsageDialog({
  record,
  onClose,
}: {
  record: RouteTemplateRecord;
  onClose: () => void;
}) {
  const load = useCallback(() => getRouteTemplateUsage(record.id), [record.id]);
  const usage = useApiData(load);
  return (
    <ModalDialog label={`Usage of ${record.name}`} onClose={onClose}>
      <h3>Route usage</h3>
      {usage.state.status === 'loading' ? (
        <LoadingState label="Loading route usage" />
      ) : usage.state.status === 'error' ? (
        <ErrorState
          message="Route usage could not be loaded."
          detail={usage.state.message}
          onRetry={usage.reload}
        />
      ) : (
        <>
          <div className="sub">
            <b>{record.name}</b> was assigned to <b>{usage.state.data.total}</b>{' '}
            released Quantity Flow
            {usage.state.data.total === 1 ? '' : 's'}. Each keeps its own route
            snapshot from release time.
          </div>
          <ul className="rt-usagelist">
            {usage.state.data.flows.map((flow) => (
              <li key={flow.quantityFlowId}>
                <span className="mono">#{flow.quantityFlowId}</span>
                <span className="mono">{flow.partNumber}</span>
                <span className="when">
                  released {formatIsoDate(flow.releasedOn)}
                </span>
              </li>
            ))}
          </ul>
          {usage.state.data.total > usage.state.data.flows.length ? (
            <div className="rt-usagelimit">
              Showing the {usage.state.data.flows.length} most recent.
            </div>
          ) : null}
        </>
      )}
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Close
        </button>
      </div>
    </ModalDialog>
  );
}

/** One step as the editor holds it. */
interface EditableStep {
  /** Render identity (stable across reorders). */
  key: number;
  areaId: number;
  operationId: number | null;
  /** The step was stored without an Operation (legacy) and still has
   * none chosen — rendered `—`, never `Select an Operation…`. */
  legacyNullOperation: boolean;
  durationText: string;
  /** The stored ISO value, sent verbatim while its text is unchanged. */
  storedDuration: string | null;
  storedDurationText: string;
  preferredMachineId: number | null;
  instructions: string;
}

interface EditorState {
  name: string;
  description: string;
  steps: EditableStep[];
}

function estimateText(iso: string | null): string {
  if (iso === null) return '';
  const minutes = isoDurationToMinutes(iso);
  return minutes === null ? iso : formatEstimate(minutes);
}

function editorState(source: RouteTemplateInput): EditorState {
  return {
    name: source.name,
    description: source.description ?? '',
    steps: source.steps.map((step, index) => {
      const text = estimateText(step.expectedDuration);
      return {
        key: index,
        areaId: step.areaId,
        operationId: step.operationId,
        legacyNullOperation: step.operationId === null,
        durationText: text,
        storedDuration: step.expectedDuration,
        storedDurationText: text,
        preferredMachineId: step.preferredMachineId,
        instructions: step.instructions ?? '',
      };
    }),
  };
}

/** The ISO duration a step saves, or false when its text is invalid:
 * unchanged text sends the stored value verbatim (no rounding of a
 * legacy sub-minute value), cleared text sends none. */
function stepDuration(step: EditableStep): string | null | false {
  const text = step.durationText.trim();
  if (text === step.storedDurationText) return step.storedDuration;
  if (!text) return null;
  const minutes = parseEstimate(text);
  return minutes === null ? false : minutesToIsoDuration(minutes);
}

function toInput(state: EditorState): RouteTemplateInput {
  return {
    name: state.name.trim(),
    description: state.description.trim() || null,
    steps: state.steps.map((step): RouteStepInput => {
      const duration = stepDuration(step);
      return {
        areaId: step.areaId,
        operationId: step.operationId,
        expectedDuration: duration === false ? null : duration,
        preferredMachineId: step.preferredMachineId,
        instructions: step.instructions.trim() || null,
      };
    }),
  };
}

/** Normalized comparison form of an editor state (dirty tracking). */
function stateKey(state: EditorState): string {
  return JSON.stringify({
    name: state.name.trim(),
    description: state.description.trim(),
    steps: state.steps.map((step) => {
      const duration = stepDuration(step);
      return [
        step.areaId,
        step.operationId,
        duration === false ? `invalid:${step.durationText.trim()}` : duration,
        step.preferredMachineId,
        step.instructions.trim(),
      ];
    }),
  });
}

function areaUnavailable(catalog: Catalog, step: EditableStep): boolean {
  return !findById(catalog.areas, step.areaId)?.isActive;
}

function operationUnavailable(catalog: Catalog, step: EditableStep): boolean {
  if (step.operationId === null) return false;
  const operation = findById(catalog.operations, step.operationId);
  return !operation || !operation.isActive || operation.areaId !== step.areaId;
}

function machineUnavailable(catalog: Catalog, step: EditableStep): boolean {
  if (step.preferredMachineId === null) return false;
  const machine = findById(catalog.machines, step.preferredMachineId);
  return (
    !machine ||
    machine.retiredOn !== undefined ||
    machine.areaId !== step.areaId
  );
}

/** Client validation — blocks Save; the server stays the authority. */
function validateRoute(state: EditorState, catalog: Catalog): string | null {
  if (!state.name.trim()) return 'A route name is required.';
  if (state.steps.length === 0) {
    return 'A Planned Route needs at least one step.';
  }
  if (state.steps.some((step) => step.operationId === null)) {
    return 'Every step needs an Operation.';
  }
  for (const [index, step] of state.steps.entries()) {
    const n = index + 1;
    if (areaUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Area.`;
    }
    if (operationUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Operation.`;
    }
    if (machineUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Machine.`;
    }
    if (stepDuration(step) === false) {
      return `Step ${n}: enter the estimated time like 45m, 4h or 2d 03h.`;
    }
  }
  const first = findById(catalog.areas, state.steps[0].areaId);
  if (first?.isTerminal) {
    return `Step 1: Area '${first.name}' is a terminal Area and never starts production. Choose a starting Area for the first step.`;
  }
  return null;
}

/** A fresh step in `area` with its first active Operation. */
function newStep(catalog: Catalog, areaId: number, key: number): EditableStep {
  return {
    key,
    areaId,
    operationId: offeredOperations(catalog, areaId)[0]?.id ?? null,
    legacyNullOperation: false,
    durationText: '',
    storedDuration: null,
    storedDurationText: '',
    preferredMachineId: null,
    instructions: '',
  };
}

/** The empty New state: one step in the first active non-terminal
 * Area (a terminal Area never starts production). */
function emptyState(catalog: Catalog): EditorState {
  const start = activeAreas(catalog).find((area) => !area.isTerminal);
  return {
    name: '',
    description: '',
    steps: start ? [newStep(catalog, start.id, 0)] : [],
  };
}

function areaOptionLabel(catalog: Catalog, areaId: number): string {
  const area = findById(catalog.areas, areaId);
  return area ? `${area.name} (unavailable)` : `Area ${areaId} (unavailable)`;
}

function operationOptionLabel(catalog: Catalog, operationId: number): string {
  const operation = findById(catalog.operations, operationId);
  return operation
    ? `${operationLabel(operation)} (unavailable)`
    : `Operation ${operationId} (unavailable)`;
}

function machineOptionLabel(catalog: Catalog, machineId: number): string {
  const machine = findById(catalog.machines, machineId);
  if (!machine) return `Machine ${machineId} (unavailable)`;
  return machine.retiredOn !== undefined
    ? `${machine.name} — retired (unavailable)`
    : `${machine.name} (unavailable)`;
}

type EditorStage =
  | null
  | 'close-discard'
  | 'dup-unsaved'
  | 'archive-unsaved'
  | 'archive-confirm'
  | 'delete-confirm';

/**
 * Create or edit one Planned Route: name, description, and the ordered
 * steps (Area, Operation from the Area's active Operations, advisory
 * Est. time, optional preferred Machine from the Area's active
 * Machines, optional instructions). Steps reorder by drag-and-drop
 * (v15) with the explicit up/down controls kept as the keyboard and
 * touch path — drag is never the only way. Duplicate and Archive /
 * Delete live in this dialog; starting Archive or Duplicate with
 * unsaved edits never saves them silently — an explicit Save / Discard
 * / Cancel decision comes first, and archiving requires typing the
 * route name.
 *
 * Every write keeps the entered input on failure. Nothing is retried
 * automatically: when the server does not answer, the dialog says the
 * outcome is unknown and closing it refreshes the list.
 */
function RouteEditDialog({
  record,
  initial,
  initialError,
  catalog,
  writeBlocked,
  onClose,
  onDuplicate,
}: {
  /** The route being edited; absent = New Planned Route. */
  record?: RouteTemplateRecord;
  /** Prefilled New state (a refused Duplicate). */
  initial?: RouteTemplateInput;
  initialError?: string;
  catalog: Catalog;
  /** Disables every write while the backend is unreachable; reading,
   * editing fields and closing stay available. */
  writeBlocked: boolean;
  /** `wrote`: something may have changed on the server — the list
   * reloads. */
  onClose: (result: { wrote: boolean }) => void;
  /** Duplicate the given route data; resolves to the unknown-outcome
   * message when the server did not answer (otherwise the view opens
   * the next dialog in place of this one). */
  onDuplicate: (source: RouteTemplateInput) => Promise<string | null>;
}) {
  const [saved, setSaved] = useState(record);
  const [baseline, setBaseline] = useState<EditorState>(() =>
    record
      ? editorState(recordInput(record))
      : initial
        ? editorState(initial)
        : emptyState(catalog),
  );
  const [name, setName] = useState(baseline.name);
  const [description, setDescription] = useState(baseline.description);
  const [steps, setSteps] = useState<EditableStep[]>(baseline.steps);
  const [everUsed, setEverUsed] = useState(record?.everUsed ?? false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [busy, setBusy] = useState(false);
  const [dragKey, setDragKey] = useState<number | null>(null);
  const [stage, setStage] = useState<EditorStage>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  // After an unknown outcome (or the route vanished), closing the
  // confirmation closes the editor and refreshes the list.
  const [confirmEndsEditor, setConfirmEndsEditor] = useState(false);
  const wrote = useRef(false);

  // New → the route name input; Edit → the dialog root (ModalDialog's
  // own initial focus). This effect runs after ModalDialog's mount
  // effect, so the opener capture for focus restoration stays intact.
  const nameRef = useRef<HTMLInputElement>(null);
  const [focusName] = useState(record === undefined);
  useEffect(() => {
    if (focusName) nameRef.current?.focus();
  }, [focusName]);

  // Preview rows (negative ids, development only) are read-only.
  const readOnly = saved !== undefined && saved.id < 0;
  const current: EditorState = { name, description, steps };
  const dirty = stateKey(current) !== stateKey(baseline);
  const validationProblem = validateRoute(current, catalog);

  const resetTo = (next: EditorState) => {
    setBaseline(next);
    setName(next.name);
    setDescription(next.description);
    setSteps(next.steps);
  };

  const applySaved = (result: RouteTemplateRecord) => {
    setSaved(result);
    setEverUsed(result.everUsed);
    resetTo(editorState(recordInput(result)));
  };

  const finish = () => onClose({ wrote: wrote.current });

  const requestClose = () => {
    if (busy) return;
    if (dirty) setStage('close-discard');
    else finish();
  };

  /** Show a refused or unanswered save in the dialog, input kept. */
  const reportSaveError = (failure: unknown) => {
    // No answer, a timeout or a 5xx: the save may have committed.
    if (!(failure instanceof ApiError) || writeOutcomeUnknown(failure)) {
      wrote.current = true;
      setError(UNKNOWN_SAVE_MESSAGE);
      return;
    }
    // Deleted (404) or archived / changed references (409) meanwhile:
    // closing refreshes the list.
    if (saved && (failure.status === 404 || failure.status === 409)) {
      wrote.current = true;
    }
    setError(failure.message);
  };

  const save = async () => {
    if (busy || readOnly) return;
    if (validationProblem) {
      setError(validationProblem);
      return;
    }
    if (saved && !dirty) {
      finish();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (saved) await replaceRouteTemplate(saved.id, toInput(current));
      else await createRouteTemplate(toInput(current));
      wrote.current = true;
      onClose({ wrote: true });
    } catch (failure) {
      reportSaveError(failure);
      setBusy(false);
    }
  };

  const runDuplicate = async (source: RouteTemplateInput) => {
    setBusy(true);
    setError(null);
    const problem = await onDuplicate(source);
    if (problem !== null) {
      wrote.current = true;
      setStage(null);
      setError(problem);
      setBusy(false);
    }
  };

  /** `Save changes, then archive` / `Save, then duplicate`. */
  const saveThen = async (next: 'archive' | 'duplicate') => {
    if (!saved || busy || validationProblem) return;
    setBusy(true);
    setError(null);
    let result: RouteTemplateRecord;
    try {
      result = await replaceRouteTemplate(saved.id, toInput(current));
    } catch (failure) {
      setStage(null);
      reportSaveError(failure);
      setBusy(false);
      return;
    }
    wrote.current = true;
    applySaved(result);
    if (next === 'archive') {
      setBusy(false);
      setStage('archive-confirm');
    } else {
      await runDuplicate(recordInput(result));
    }
  };

  /** Discard the edits (back to the saved route), then continue. */
  const discardThen = (next: 'archive' | 'duplicate') => {
    resetTo(baseline);
    setError(null);
    if (next === 'archive') setStage('archive-confirm');
    else void runDuplicate(toInput(baseline));
  };

  const closeConfirm = () => {
    if (busy) return;
    if (confirmEndsEditor) {
      onClose({ wrote: true });
      return;
    }
    setConfirmError(null);
    setStage(null);
  };

  const confirmArchive = async () => {
    if (!saved || busy || readOnly) return;
    setBusy(true);
    setConfirmError(null);
    try {
      await archiveRouteTemplate(saved.id);
      onClose({ wrote: true });
    } catch (failure) {
      setBusy(false);
      if (!(failure instanceof ApiError)) {
        setConfirmEndsEditor(true);
        setConfirmError(UNKNOWN_ARCHIVE_MESSAGE);
      } else if (failure.status === 404) {
        setConfirmEndsEditor(true);
        setConfirmError(failure.message);
      } else if (failure.status === 409) {
        // Never used after all: Delete… replaces Archive….
        wrote.current = true;
        setEverUsed(false);
        setStage(null);
        setError(failure.message);
      } else {
        setConfirmError(failure.message);
      }
    }
  };

  const confirmDelete = async () => {
    if (!saved || busy || readOnly) return;
    setBusy(true);
    setConfirmError(null);
    try {
      await deleteRouteTemplate(saved.id);
      onClose({ wrote: true });
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) {
        // Already gone (a retried delete, or deleted elsewhere).
        onClose({ wrote: true });
        return;
      }
      setBusy(false);
      if (!(failure instanceof ApiError)) {
        setConfirmEndsEditor(true);
        setConfirmError(UNKNOWN_DELETE_MESSAGE);
      } else if (failure.status === 409) {
        // Used meanwhile: Archive… replaces Delete….
        wrote.current = true;
        setEverUsed(true);
        setStage(null);
        setError(failure.message);
      } else {
        setConfirmError(failure.message);
      }
    }
  };

  const setStep = (key: number, change: Partial<EditableStep>) =>
    setSteps((list) =>
      list.map((step) => (step.key === key ? { ...step, ...change } : step)),
    );

  /** Area change keeps the Operation / Machine only when the new Area
   * still offers them; otherwise its first active Operation and no
   * preferred Machine. */
  const changeArea = (key: number, areaId: number) =>
    setSteps((list) =>
      list.map((step) => {
        if (step.key !== key) return step;
        const operations = offeredOperations(catalog, areaId);
        const machines = offeredMachines(catalog, areaId);
        return {
          ...step,
          areaId,
          operationId: operations.some((op) => op.id === step.operationId)
            ? step.operationId
            : (operations[0]?.id ?? null),
          legacyNullOperation: false,
          preferredMachineId: machines.some(
            (m) => m.id === step.preferredMachineId,
          )
            ? step.preferredMachineId
            : null,
        };
      }),
    );

  const addStep = () =>
    setSteps((list) => {
      const last = list[list.length - 1];
      const areaId =
        last && !areaUnavailable(catalog, last)
          ? last.areaId
          : activeAreas(catalog)[0]?.id;
      if (areaId === undefined) return list;
      const key = 1 + Math.max(-1, ...list.map((step) => step.key));
      return [...list, newStep(catalog, areaId, key)];
    });

  const move = (index: number, delta: -1 | 1) =>
    setSteps((list) => {
      const target = index + delta;
      if (target < 0 || target >= list.length) return list;
      const next = [...list];
      const [step] = next.splice(index, 1);
      next.splice(target, 0, step);
      return next;
    });

  const handleDrop = (targetKey: number) => {
    if (dragKey === null || dragKey === targetKey) return;
    setSteps((list) => {
      const from = list.findIndex((step) => step.key === dragKey);
      const to = list.findIndex((step) => step.key === targetKey);
      if (from < 0 || to < 0) return list;
      const next = [...list];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  };

  const title = saved ? 'Edit Planned Route' : 'New Planned Route';
  const writeDisabled = writeBlocked || readOnly || busy;
  const usageCount = saved?.usageCount ?? 0;

  return (
    <ModalDialog label={title} onClose={requestClose} size="xwide">
      <h3>{title}</h3>
      {dirty ? <div className="rt-dirty">● Unsaved changes</div> : null}
      <div className="rt-form">
        <label htmlFor="rt-name">Route name</label>
        <input
          id="rt-name"
          ref={nameRef}
          className="field"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Bracket std v4"
        />
        <label htmlFor="rt-desc">
          Description <span className="field-optional">(optional)</span>
        </label>
        <input
          id="rt-desc"
          className="field"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <label>Steps</label>
        {/* Column labels for the step fields — the bottom-border-only
            instruction/duration inputs stay labelled (v15). */}
        <div className="rt-stephead" aria-hidden="true">
          <span />
          <span />
          <span>Area</span>
          <span>Operation</span>
          <span>Est. time</span>
          <span>Preferred Machine</span>
          <span />
        </div>
        <div className="rt-steplist">
          {steps.map((step, index) => {
            const operations = offeredOperations(catalog, step.areaId);
            const machines = offeredMachines(catalog, step.areaId);
            return (
              // Drag-and-drop reorder (HTML5 DnD, same pattern as the
              // Priority list) with ↑/↓ as the keyboard/touch path —
              // drag is never the only way to reorder.
              <div
                className={`rt-steprow${dragKey === step.key ? ' dragging' : ''}`}
                key={step.key}
                draggable
                onDragStart={() => setDragKey(step.key)}
                onDragEnd={() => setDragKey(null)}
                onDragOver={(event: DragEvent) => event.preventDefault()}
                onDrop={(event: DragEvent) => {
                  event.preventDefault();
                  handleDrop(step.key);
                }}
              >
                <span className="grip" aria-hidden="true">
                  ⠿
                </span>
                <span className="idx">{index + 1}</span>
                <select
                  aria-label={`Step ${index + 1} Area`}
                  value={String(step.areaId)}
                  onChange={(e) => changeArea(step.key, Number(e.target.value))}
                >
                  {areaUnavailable(catalog, step) ? (
                    // A stored Area that is no longer offered stays
                    // visible as an explicit unavailable value — never
                    // silently replaced.
                    <option value={String(step.areaId)}>
                      {areaOptionLabel(catalog, step.areaId)}
                    </option>
                  ) : null}
                  {activeAreas(catalog).map((area) => (
                    <option key={area.id} value={String(area.id)}>
                      {area.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`Step ${index + 1} Operation`}
                  value={
                    step.operationId === null ? '' : String(step.operationId)
                  }
                  onChange={(e) => {
                    if (e.target.value) {
                      setStep(step.key, {
                        operationId: Number(e.target.value),
                        legacyNullOperation: false,
                      });
                    }
                  }}
                >
                  {step.operationId === null ? (
                    <option value="" disabled>
                      {step.legacyNullOperation ? '—' : 'Select an Operation…'}
                    </option>
                  ) : null}
                  {step.operationId !== null &&
                  operationUnavailable(catalog, step) ? (
                    <option value={String(step.operationId)}>
                      {operationOptionLabel(catalog, step.operationId)}
                    </option>
                  ) : null}
                  {operations.map((operation) => (
                    <option key={operation.id} value={String(operation.id)}>
                      {operationLabel(operation)}
                    </option>
                  ))}
                </select>
                <input
                  className="uline"
                  aria-label={`Step ${index + 1} expected duration`}
                  placeholder="e.g. 4h"
                  value={step.durationText}
                  onChange={(e) =>
                    setStep(step.key, { durationText: e.target.value })
                  }
                />
                <select
                  aria-label={`Step ${index + 1} preferred Machine`}
                  value={
                    step.preferredMachineId === null
                      ? ''
                      : String(step.preferredMachineId)
                  }
                  onChange={(e) =>
                    setStep(step.key, {
                      preferredMachineId: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                >
                  <option value="">— no preferred Machine</option>
                  {step.preferredMachineId !== null &&
                  machineUnavailable(catalog, step) ? (
                    // A retired / moved / missing Machine stays visible
                    // as an explicit unavailable value — never silently
                    // cleared; choosing another value replaces it.
                    <option value={String(step.preferredMachineId)}>
                      {machineOptionLabel(catalog, step.preferredMachineId)}
                    </option>
                  ) : null}
                  {machines.map((machine) => (
                    <option key={machine.id} value={String(machine.id)}>
                      {machine.name}
                    </option>
                  ))}
                </select>
                <span className="steppbtns">
                  <button
                    aria-label={`Move step ${index + 1} up`}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    aria-label={`Move step ${index + 1} down`}
                    disabled={index === steps.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                  <button
                    aria-label={`Remove step ${index + 1}`}
                    disabled={steps.length === 1}
                    onClick={() =>
                      setSteps((list) => list.filter((s) => s.key !== step.key))
                    }
                  >
                    ✕
                  </button>
                </span>
                <label className="instrwrap">
                  <span className="flbl">Instructions</span>
                  <input
                    className="instr uline"
                    placeholder="optional"
                    value={step.instructions}
                    onChange={(e) =>
                      setStep(step.key, { instructions: e.target.value })
                    }
                  />
                </label>
              </div>
            );
          })}
        </div>
        <button className="rt-addstep" onClick={addStep}>
          + Add step
        </button>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        {saved && everUsed ? (
          <div className="rt-editnote">
            Changes apply to <b>future assignments only</b>.{' '}
            {usageCount > 0
              ? `The ${usageCount} Quantity Flow${usageCount === 1 ? '' : 's'} already released with this route keep${usageCount === 1 ? 's' : ''}`
              : 'Quantity Flows already released with this route keep'}{' '}
            the assigned route unchanged — an in-production route is changed in
            its own audited workflow, with a reason.
          </div>
        ) : null}
      </div>
      {saved ? (
        <div className="rt-dlgactions">
          <button
            className="rt-dlgbtn"
            disabled={writeDisabled}
            onClick={() => {
              if (dirty) setStage('dup-unsaved');
              else void runDuplicate(toInput(baseline));
            }}
          >
            Duplicate
          </button>
          {everUsed ? (
            <button
              className="rt-dlgbtn warn"
              disabled={writeDisabled}
              onClick={() => {
                setConfirmError(null);
                setStage(dirty ? 'archive-unsaved' : 'archive-confirm');
              }}
            >
              Archive…
            </button>
          ) : (
            <button
              className="rt-dlgbtn danger"
              disabled={writeDisabled}
              onClick={() => {
                setConfirmError(null);
                setStage('delete-confirm');
              }}
            >
              Delete…
            </button>
          )}
        </div>
      ) : null}
      <div className="row">
        <button className="bigbtn ghost" onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeDisabled}
          onClick={() => void save()}
        >
          {saved ? 'Save route' : 'Create route'}
        </button>
      </div>
      {stage === 'close-discard' ? (
        <ConfirmDialog
          title="Discard unsaved route changes?"
          confirmLabel="Discard changes"
          cancelLabel="Keep editing"
          danger
          onCancel={() => setStage(null)}
          onConfirm={finish}
        >
          The changes to this route have not been saved and will be lost.
        </ConfirmDialog>
      ) : null}
      {stage === 'dup-unsaved' ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save, then duplicate"
          discardLabel="Duplicate the saved route"
          saveDisabledReason={
            validationProblem
              ? `The edits cannot be saved yet: ${validationProblem}`
              : undefined
          }
          saveDisabled={writeBlocked || busy}
          discardDisabled={writeBlocked || busy}
          onCancel={() => {
            if (!busy) setStage(null);
          }}
          onSave={() => void saveThen('duplicate')}
          onDiscard={() => discardThen('duplicate')}
        >
          This route still has unsaved edits. Duplicating never saves them
          silently — choose what the duplicate is based on.
        </UnsavedChoiceDialog>
      ) : null}
      {stage === 'archive-unsaved' ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save changes, then archive"
          discardLabel="Discard changes"
          saveDisabledReason={
            validationProblem
              ? `The edits cannot be saved yet: ${validationProblem}`
              : undefined
          }
          saveDisabled={writeBlocked || busy}
          discardDisabled={busy}
          onCancel={() => {
            if (!busy) setStage(null);
          }}
          onSave={() => void saveThen('archive')}
          onDiscard={() => discardThen('archive')}
        >
          This route still has unsaved edits. Archiving never saves them
          silently — choose what happens to the edits before the archive
          confirmation opens.
        </UnsavedChoiceDialog>
      ) : null}
      {stage === 'archive-confirm' && saved ? (
        <TypedConfirmDialog
          title="Archive Planned Route"
          expectedValue={saved.name}
          valueLabel="route name"
          confirmLabel="Archive route"
          confirmDisabled={writeBlocked || busy || confirmEndsEditor}
          onCancel={closeConfirm}
          onConfirm={() => void confirmArchive()}
        >
          Archiving <b>{saved.name}</b>:
          <ul className="rt-consequences">
            <li>
              The route no longer appears as a choice for future assignments.
            </li>
            <li>
              Quantity Flows already released with it keep their Assigned Route
              snapshot unchanged.
            </li>
            <li>Actual Movement history is not changed.</li>
            <li>The route stays visible for historical context.</li>
          </ul>
          {confirmError ? (
            <div className="rt-confirmerr" role="alert">
              {confirmError}
            </div>
          ) : null}
        </TypedConfirmDialog>
      ) : null}
      {stage === 'delete-confirm' && saved ? (
        <ConfirmDialog
          title="Delete Planned Route"
          confirmLabel="Delete route"
          cancelLabel="Cancel (Esc)"
          danger
          confirmDisabled={writeBlocked || busy || confirmEndsEditor}
          onCancel={closeConfirm}
          onConfirm={() => void confirmDelete()}
        >
          <b>{saved.name}</b> has never been used by a released Quantity Flow,
          so it can be removed completely
          {dirty ? ' (unsaved edits are discarded with it)' : ''}. A route that
          has been used is archived instead.
          {confirmError ? (
            <div className="rt-confirmerr" role="alert">
              {confirmError}
            </div>
          ) : null}
        </ConfirmDialog>
      ) : null}
    </ModalDialog>
  );
}
