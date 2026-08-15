import './planned-routes.css';

import { Fragment, useState } from 'react';
import type { CSSProperties, DragEvent } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { DevNotice } from '../../components/DevNotice';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { TypedConfirmDialog } from '../../components/TypedConfirmDialog';
import { UnsavedChoiceDialog } from '../../components/UnsavedChoiceDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { MOCK_AREAS, areaByKey } from '../../mocks/areas';
import { MOCK_MACHINES, activeMachines } from '../../mocks/machines';
import { MOCK_ROUTE_TEMPLATES } from '../../mocks/planned-routes';
import type { AreaKey, MockRouteStep, MockRouteTemplate } from '../view-models';

// Management → Planned Routes: reusable route definitions (internal
// name: RouteTemplate) owned by authorized production roles — full
// Administrator access is deliberately NOT required. The name `Planned
// Routes` keeps it clearly apart from the Floating actual route traces
// shown in Tracking. Editing a route affects FUTURE assignments only:
// a released Quantity Flow keeps its independent Assigned Route
// snapshot, and any intentional change to an in-production Assigned
// Route happens in its own audited workflow (Tracking → Edit assigned
// Route), never here. A route that has ever been used is archived
// instead of deleted (a never-used route may still be deleted
// outright); existing snapshots preserve historical route definitions,
// so no separate template-versioning system exists.

type PendingDialog =
  | { kind: 'new' }
  | { kind: 'edit'; template: MockRouteTemplate }
  | { kind: 'usage'; template: MockRouteTemplate };

const today = (): string => new Date().toISOString().slice(0, 10);

// Long-data preview routes (?state=long): many routes plus one route
// with an over-long name/description and a long step chain, to
// exercise dense-table and step-chip wrapping behavior.
const LONG_PREVIEW_ROUTE_TEMPLATES: MockRouteTemplate[] = [
  ...Array.from({ length: 12 }, (_, i): MockRouteTemplate => {
    const n = i + 1;
    return {
      id: `RT-long-${n}`,
      name: `Long preview route variant ${n} — extended qualification cell`,
      description:
        'Auto-generated long-data preview route for layout testing only.',
      steps: [
        { area: 'material', operation: 'Receiving' },
        { area: 'cut', operation: 'Cutting', expectedDuration: '2h' },
        { area: 'lathe', operation: 'Turning', expectedDuration: '4h' },
        { area: 'deburr', operation: 'Deburring' },
        { area: 'stockroom', operation: 'Receiving' },
      ],
      usedBy: [],
      createdOn: '2026-07-01',
      updatedOn: '2026-07-01',
    };
  }),
  {
    id: 'RT-long-supplemental',
    name: 'Supplemental long-preview route — multi-stage housing assembly with outside plating, secondary deburr, and final inspection rework loop',
    description:
      'Long-data preview: an over-long route name and description plus many ordered steps, to exercise step-chip wrapping and the full-width dense table layout.',
    steps: [
      { area: 'material', operation: 'Receiving' },
      {
        area: 'cut',
        operation: 'Cutting',
        expectedDuration: '2h',
        preferredMachineId: 'MC-201',
      },
      {
        area: 'lathe',
        operation: 'Turning',
        expectedDuration: '6h',
        instructions:
          'Face and turn per drawing; check shoulder depth and verify runout before handoff to Mill.',
      },
      {
        area: 'mill',
        operation: 'Milling',
        expectedDuration: '8h',
        preferredMachineId: 'MC-302',
        instructions: 'Rough and finish mill; verify bore location.',
      },
      { area: 'manual', operation: 'Manual work', expectedDuration: '1h' },
      { area: 'deburr', operation: 'Deburring', expectedDuration: '1h' },
      { area: 'external', operation: 'Plating', expectedDuration: '3d' },
      { area: 'external', operation: 'Testing', expectedDuration: '1d' },
      { area: 'deburr', operation: 'Deburring', expectedDuration: '1h' },
      { area: 'stockroom', operation: 'Receiving' },
    ],
    usedBy: [],
    createdOn: '2026-07-28',
    updatedOn: '2026-07-28',
  },
];

/** Editor-facing route data — what Save/Duplicate carry back out. */
interface RouteDraft {
  name: string;
  description: string;
  steps: MockRouteStep[];
}

/** Resolve a Machine id to its display presentation (v15: preferred
 * Machines are referenced by id — display names are reusable across
 * physical replacements and cannot identify the preference). */
function machineLabel(id: string): string {
  const machine = MOCK_MACHINES.find((m) => m.id === id);
  if (!machine) return `${id} (unknown)`;
  return machine.retiredOn !== undefined
    ? `${machine.name} — retired`
    : machine.name;
}

/** Area-colored step chips — the same reading direction as the
 * Tracking route visualization, each block tinted with its Area color
 * (tinted surface + Area dot; the text keeps the normal color). */
function StepChips({ steps }: { steps: MockRouteStep[] }) {
  return (
    <div className="rt-steps">
      {steps.map((step, i) => {
        const area = areaByKey(step.area);
        const colorVar = area?.colorVar ?? 'var(--faint)';
        return (
          <Fragment key={`${step.area}-${i}`}>
            {i > 0 ? (
              <span className="arw" aria-hidden="true">
                →
              </span>
            ) : null}
            <span
              className="rt-stepchip"
              style={{ '--acol': colorVar } as CSSProperties}
              title={`${area?.name ?? step.area} — ${step.operation}`}
            >
              <AreaDot colorVar={colorVar} size={8} />
              {area?.name ?? step.area}
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

export function PlannedRoutesView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const [templates, setTemplates] =
    useState<MockRouteTemplate[]>(MOCK_ROUTE_TEMPLATES);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  if (preview === 'loading') {
    return (
      <section className="rt" aria-label="Planned Routes">
        <LoadingState label="Loading Planned Routes" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="rt" aria-label="Planned Routes">
        <ErrorState
          message="Planned Route data could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  const query = search.trim().toLowerCase();
  const baseTemplates =
    preview === 'long'
      ? [...templates, ...LONG_PREVIEW_ROUTE_TEMPLATES]
      : templates;
  const visible = (preview === 'empty' ? [] : baseTemplates).filter(
    (t) =>
      !query ||
      [t.name, t.description ?? '', ...t.steps.map((s) => s.operation)]
        .join(' ')
        .toLowerCase()
        .includes(query),
  );
  const activeTemplates = visible.filter((t) => t.archivedOn === undefined);
  const archivedTemplates = visible.filter((t) => t.archivedOn !== undefined);

  const update = (
    id: string,
    change: (t: MockRouteTemplate) => MockRouteTemplate,
  ) =>
    setTemplates((current) =>
      current.map((t) => (t.id === id ? change(t) : t)),
    );

  /** Create an active copy from route data and open it for editing. */
  const duplicateFrom = (draft: RouteDraft) => {
    const copy: MockRouteTemplate = {
      id: `RT-${String(Date.now()).slice(-4)}`,
      name: `${draft.name} (variant)`,
      description: draft.description || undefined,
      steps: draft.steps.map((s) => ({ ...s })),
      usedBy: [],
      createdOn: today(),
      updatedOn: today(),
    };
    setTemplates((current) => [...current, copy]);
    setDialog({ kind: 'edit', template: copy });
  };

  return (
    <section className="rt" aria-label="Planned Routes">
      <h1>Planned Routes</h1>
      <p className="rt-sub">
        Reusable route definitions assigned to Quantity Flows at release, for
        authorized production roles. Editing a route changes future assignments
        only — quantity already in production keeps the route it was released
        with, and actual Movement history stays authoritative.
      </p>
      <DevNotice>
        Development preview — routes shown are sample data and changes affect
        only this preview.
      </DevNotice>
      <div className="rt-toolbar">
        <input
          type="search"
          placeholder="Search: route name, Operation…"
          aria-label="Search Planned Routes"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="spacer" />
        <button
          className="btn primary"
          disabled={writeBlocked}
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Planned Route
        </button>
      </div>

      {activeTemplates.length === 0 && archivedTemplates.length === 0 ? (
        <EmptyState
          message={
            query
              ? `No Planned Routes match “${search.trim()}”.`
              : 'No Planned Routes defined yet.'
          }
        />
      ) : null}

      {activeTemplates.length > 0 ? (
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
            {activeTemplates.map((template) => (
              // The COMPLETE row opens Edit Planned Route (v15): the
              // name-cell button is the keyboard and screen-reader
              // entry point; the usage cell is the one interactive
              // island and stops propagation.
              <tr
                key={template.id}
                className="selrow"
                onClick={() => setDialog({ kind: 'edit', template })}
              >
                <td>
                  <button
                    className="rowbtn"
                    aria-label={`Edit ${template.name}`}
                  >
                    <div className="rtname">{template.name}</div>
                    {template.description ? (
                      <div className="rtdesc">{template.description}</div>
                    ) : null}
                  </button>
                </td>
                <td>
                  <StepChips steps={template.steps} />
                </td>
                <td>
                  <span className="rt-status active">Active</span>
                  <div className="rt-statusdate">
                    updated {template.updatedOn}
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
                  {template.usedBy.length > 0 ? (
                    <button
                      onClick={() => setDialog({ kind: 'usage', template })}
                    >
                      {template.usedBy.length} Quantity Flow
                      {template.usedBy.length === 1 ? '' : 's'}…
                    </button>
                  ) : (
                    <span className="never">Never used</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {archivedTemplates.length > 0 ? (
        <div className="rt-archived">
          <h2>Archived Routes</h2>
          <table className="rt-table">
            <thead>
              <tr>
                <th>Planned Route</th>
                <th>Steps</th>
                <th>Archived</th>
                <th>Used by</th>
                <th>
                  <span className="rt-visuallyquiet">Duplicate</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {archivedTemplates.map((template) => (
                <tr key={template.id} className="archived">
                  <td>
                    <div className="rtname">{template.name}</div>
                    {template.description ? (
                      <div className="rtdesc">{template.description}</div>
                    ) : null}
                  </td>
                  <td>
                    <StepChips steps={template.steps} />
                  </td>
                  <td>
                    <span className="rt-status archived">Archived</span>
                    <div className="rt-statusdate">
                      since {template.archivedOn}
                    </div>
                  </td>
                  <td className="rt-usage" data-label="Used by">
                    {template.usedBy.length > 0 ? (
                      <button
                        onClick={() => setDialog({ kind: 'usage', template })}
                      >
                        {template.usedBy.length} Quantity Flow
                        {template.usedBy.length === 1 ? '' : 's'}…
                      </button>
                    ) : (
                      <span className="never">Never used</span>
                    )}
                  </td>
                  <td>
                    <button
                      className="rt-duplicate"
                      disabled={writeBlocked}
                      onClick={() =>
                        duplicateFrom({
                          name: template.name,
                          description: template.description ?? '',
                          steps: template.steps,
                        })
                      }
                    >
                      Duplicate
                    </button>
                  </td>
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

      {dialog?.kind === 'new' ? (
        <RouteEditDialog
          key="new"
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={(draft) => {
            setTemplates((current) => [
              ...current,
              {
                id: `RT-${String(Date.now()).slice(-4)}`,
                name: draft.name,
                description: draft.description || undefined,
                steps: draft.steps,
                usedBy: [],
                createdOn: today(),
                updatedOn: today(),
              },
            ]);
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <RouteEditDialog
          key={dialog.template.id}
          template={dialog.template}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={(draft) => {
            update(dialog.template.id, (t) => ({
              ...t,
              name: draft.name,
              description: draft.description || undefined,
              steps: draft.steps,
              updatedOn: today(),
            }));
            setDialog(null);
          }}
          onApplyChanges={(draft) => {
            update(dialog.template.id, (t) => ({
              ...t,
              name: draft.name,
              description: draft.description || undefined,
              steps: draft.steps,
              updatedOn: today(),
            }));
          }}
          onDuplicate={duplicateFrom}
          onArchive={() => {
            update(dialog.template.id, (t) => ({
              ...t,
              archivedOn: today(),
              updatedOn: today(),
            }));
            setDialog(null);
          }}
          onDelete={() => {
            setTemplates((current) =>
              current.filter((t) => t.id !== dialog.template.id),
            );
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.kind === 'usage' ? (
        <UsageDialog
          template={dialog.template}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </section>
  );
}

/**
 * Where a route has been used: the Quantity Flows released with an
 * Assigned Route snapshot copied from it. Those snapshots are
 * independent — changing or archiving the route never touches them.
 */
function UsageDialog({
  template,
  onClose,
}: {
  template: MockRouteTemplate;
  onClose: () => void;
}) {
  return (
    <ModalDialog label={`Usage of ${template.name}`} onClose={onClose}>
      <h3>Route usage</h3>
      <div className="sub">
        <b>{template.name}</b> was assigned to <b>{template.usedBy.length}</b>{' '}
        released Quantity Flow
        {template.usedBy.length === 1 ? '' : 's'}. Each keeps its own route
        snapshot from release time.
      </div>
      <ul className="rt-usagelist">
        {template.usedBy.map((usage) => (
          <li key={usage.flow}>
            <span className="mono">{usage.flow}</span>
            <span className="mono">{usage.pn}</span>
            <span className="when">released {usage.releasedOn}</span>
          </li>
        ))}
      </ul>
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Close
        </button>
      </div>
    </ModalDialog>
  );
}

interface EditableStep extends MockRouteStep {
  key: number;
}

/** Normalize editor steps back to MockRouteStep (drops editor keys). */
function projectSteps(steps: EditableStep[]): MockRouteStep[] {
  return steps.map((step) => ({
    area: step.area,
    operation: step.operation.trim(),
    expectedDuration: step.expectedDuration?.trim() || undefined,
    preferredMachineId: step.preferredMachineId || undefined,
    instructions: step.instructions?.trim() || undefined,
  }));
}

/**
 * Create or edit one Planned Route: name, description, and the ordered
 * steps (Area, Operation from the Area's Operations, advisory expected
 * duration, optional preferred Machine from the Area's active
 * Machines, optional instructions). Steps reorder by drag-and-drop
 * (v15) with the explicit up/down controls kept as the keyboard and
 * touch path — drag is never the only way. Duplicate and Archive /
 * Delete live in this dialog; starting Archive or Duplicate with
 * unsaved edits never saves them silently — an explicit Save / Discard
 * / Cancel decision comes first, and archiving requires typing the
 * exact route name.
 */
function RouteEditDialog({
  template,
  writeBlocked = false,
  onCancel,
  onSave,
  onApplyChanges,
  onDuplicate,
  onArchive,
  onDelete,
}: {
  template?: MockRouteTemplate;
  /** Disables Save/Duplicate/Archive/Delete while the backend is
   * unreachable (Management → Planned Routes offline write-block). */
  writeBlocked?: boolean;
  onCancel: () => void;
  onSave: (draft: RouteDraft) => void;
  /** Persist edits without closing (Save inside Archive/Duplicate). */
  onApplyChanges?: (draft: RouteDraft) => void;
  onDuplicate?: (draft: RouteDraft) => void;
  onArchive?: () => void;
  onDelete?: () => void;
}) {
  const initialSteps = (
    template?.steps ?? [{ area: 'material' as AreaKey, operation: 'Receiving' }]
  ).map((step, i) => ({ ...step, key: i }));
  const [name, setName] = useState(template?.name ?? '');
  const [description, setDescription] = useState(template?.description ?? '');
  const [steps, setSteps] = useState<EditableStep[]>(initialSteps);
  const [error, setError] = useState<string | null>(null);
  const [dragKey, setDragKey] = useState<number | null>(null);
  const [baseline, setBaseline] = useState<RouteDraft>({
    name: template?.name ?? '',
    description: template?.description ?? '',
    steps: projectSteps(initialSteps),
  });
  const [stage, setStage] = useState<
    | null
    | 'close-discard'
    | 'dup-unsaved'
    | 'archive-unsaved'
    | 'archive-confirm'
    | 'delete-confirm'
  >(null);

  const used = (template?.usedBy.length ?? 0) > 0;
  const draft: RouteDraft = {
    name: name.trim(),
    description: description.trim(),
    steps: projectSteps(steps),
  };
  const dirty =
    JSON.stringify(draft) !==
    JSON.stringify({
      name: baseline.name.trim(),
      description: baseline.description.trim(),
      steps: baseline.steps,
    });

  /** Pure validation (no state changes). */
  const validate = (): string | null => {
    if (!draft.name) return 'A route name is required.';
    if (draft.steps.length === 0) {
      return 'A Planned Route needs at least one step.';
    }
    if (draft.steps.some((s) => !s.operation)) {
      return 'Every step needs an Operation.';
    }
    return null;
  };

  const setStep = (key: number, change: Partial<MockRouteStep>) =>
    setSteps((current) =>
      current.map((s) => (s.key === key ? { ...s, ...change } : s)),
    );

  /** Area change resets/revalidates Operation and preferred Machine. */
  const changeArea = (key: number, areaKey: AreaKey) =>
    setSteps((current) =>
      current.map((s) => {
        if (s.key !== key) return s;
        const ops = areaByKey(areaKey)?.operations ?? [];
        const machineStillValid =
          s.preferredMachineId !== undefined &&
          activeMachines().some(
            (m) => m.id === s.preferredMachineId && m.area === areaKey,
          );
        return {
          ...s,
          area: areaKey,
          operation: ops.includes(s.operation) ? s.operation : (ops[0] ?? ''),
          preferredMachineId: machineStillValid
            ? s.preferredMachineId
            : undefined,
        };
      }),
    );

  const move = (index: number, delta: -1 | 1) =>
    setSteps((current) => {
      const target = index + delta;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      const [step] = next.splice(index, 1);
      next.splice(target, 0, step);
      return next;
    });

  const handleDrop = (targetKey: number) => {
    if (dragKey === null || dragKey === targetKey) return;
    setSteps((current) => {
      const from = current.findIndex((s) => s.key === dragKey);
      const to = current.findIndex((s) => s.key === targetKey);
      if (from < 0 || to < 0) return current;
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  };

  const save = () => {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    onSave(draft);
  };

  /** Save-then-continue used by the unsaved-changes decision. */
  const applyAndContinue = (next: 'archive-confirm' | 'duplicate') => {
    const problem = validate();
    if (problem) return;
    onApplyChanges?.(draft);
    setBaseline(draft);
    if (next === 'duplicate') {
      onDuplicate?.(draft);
    } else {
      setStage(next);
    }
  };

  /** Discard-then-continue: reset the form to the saved baseline. */
  const discardAndContinue = (next: 'archive-confirm' | 'duplicate') => {
    setName(baseline.name);
    setDescription(baseline.description);
    setSteps(baseline.steps.map((step, i) => ({ ...step, key: i })));
    setError(null);
    if (next === 'duplicate') {
      onDuplicate?.(baseline);
    } else {
      setStage(next);
    }
  };

  const requestClose = () => {
    if (dirty) setStage('close-discard');
    else onCancel();
  };

  const validationProblem = validate();

  return (
    <ModalDialog
      label={template ? 'Edit Planned Route' : 'New Planned Route'}
      onClose={requestClose}
      size="xwide"
    >
      <h3>{template ? 'Edit Planned Route' : 'New Planned Route'}</h3>
      {dirty ? <div className="rt-dirty">● Unsaved changes</div> : null}
      <div className="rt-form">
        <label htmlFor="rt-name">Route name</label>
        <input
          id="rt-name"
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
            const ops = areaByKey(step.area)?.operations ?? [];
            const opUnavailable =
              step.operation !== '' && !ops.includes(step.operation);
            const areaMachines = activeMachines().filter(
              (m) => m.area === step.area,
            );
            const machineUnavailable =
              step.preferredMachineId !== undefined &&
              !areaMachines.some((m) => m.id === step.preferredMachineId);
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
                  value={step.area}
                  onChange={(e) =>
                    changeArea(step.key, e.target.value as AreaKey)
                  }
                >
                  {MOCK_AREAS.map((a) => (
                    <option key={a.key} value={a.key}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`Step ${index + 1} Operation`}
                  value={step.operation}
                  onChange={(e) =>
                    setStep(step.key, { operation: e.target.value })
                  }
                >
                  {step.operation === '' ? (
                    <option value="" disabled>
                      Select an Operation…
                    </option>
                  ) : null}
                  {opUnavailable ? (
                    // An off-list Operation stays visible as an
                    // explicit unavailable value — never silently
                    // cleared.
                    <option value={step.operation}>
                      {step.operation} (unavailable)
                    </option>
                  ) : null}
                  {ops.map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>
                <input
                  className="uline"
                  aria-label={`Step ${index + 1} expected duration`}
                  placeholder="e.g. 4h"
                  value={step.expectedDuration ?? ''}
                  onChange={(e) =>
                    setStep(step.key, { expectedDuration: e.target.value })
                  }
                />
                <select
                  aria-label={`Step ${index + 1} preferred Machine`}
                  value={step.preferredMachineId ?? ''}
                  onChange={(e) =>
                    setStep(step.key, {
                      preferredMachineId: e.target.value || undefined,
                    })
                  }
                >
                  <option value="">— no preferred Machine</option>
                  {machineUnavailable && step.preferredMachineId ? (
                    // A retired/missing Machine stays visible as an
                    // explicit unavailable value — never silently
                    // cleared; choosing another value replaces it.
                    <option value={step.preferredMachineId}>
                      {machineLabel(step.preferredMachineId)} (unavailable)
                    </option>
                  ) : null}
                  {areaMachines.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
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
                      setSteps((current) =>
                        current.filter((s) => s.key !== step.key),
                      )
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
                    value={step.instructions ?? ''}
                    onChange={(e) =>
                      setStep(step.key, { instructions: e.target.value })
                    }
                  />
                </label>
              </div>
            );
          })}
        </div>
        <button
          className="rt-addstep"
          onClick={() =>
            setSteps((current) => [
              ...current,
              {
                area: 'lathe',
                operation: areaByKey('lathe')?.operations[0] ?? '',
                key: 1 + Math.max(0, ...current.map((s) => s.key)),
              },
            ])
          }
        >
          + Add step
        </button>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        {template && used ? (
          <div className="rt-editnote">
            Changes apply to <b>future assignments only</b>. The{' '}
            {template.usedBy.length} Quantity Flow
            {template.usedBy.length === 1 ? '' : 's'} already released with this
            route keep{template.usedBy.length === 1 ? 's' : ''} the assigned
            route unchanged — an in-production route is changed in its own
            audited workflow, with a reason.
          </div>
        ) : null}
      </div>
      {template ? (
        <div className="rt-dlgactions">
          <button
            className="rt-dlgbtn"
            disabled={writeBlocked}
            onClick={() => {
              if (dirty) setStage('dup-unsaved');
              else onDuplicate?.(draft);
            }}
          >
            Duplicate
          </button>
          {used ? (
            <button
              className="rt-dlgbtn warn"
              disabled={writeBlocked}
              onClick={() => {
                setStage(dirty ? 'archive-unsaved' : 'archive-confirm');
              }}
            >
              Archive…
            </button>
          ) : (
            <button
              className="rt-dlgbtn danger"
              disabled={writeBlocked}
              onClick={() => setStage('delete-confirm')}
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
          disabled={writeBlocked}
          onClick={save}
        >
          {template ? 'Save route' : 'Create route'}
        </button>
      </div>
      {stage === 'close-discard' ? (
        <ConfirmDialog
          title="Discard unsaved route changes?"
          confirmLabel="Discard changes"
          cancelLabel="Keep editing"
          danger
          onCancel={() => setStage(null)}
          onConfirm={onCancel}
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
          saveDisabled={writeBlocked}
          discardDisabled={writeBlocked}
          onCancel={() => setStage(null)}
          onSave={() => applyAndContinue('duplicate')}
          onDiscard={() => discardAndContinue('duplicate')}
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
          saveDisabled={writeBlocked}
          onCancel={() => setStage(null)}
          onSave={() => applyAndContinue('archive-confirm')}
          onDiscard={() => discardAndContinue('archive-confirm')}
        >
          This route still has unsaved edits. Archiving never saves them
          silently — choose what happens to the edits before the archive
          confirmation opens.
        </UnsavedChoiceDialog>
      ) : null}
      {stage === 'archive-confirm' && template ? (
        <TypedConfirmDialog
          title="Archive Planned Route"
          expectedValue={baseline.name || template.name}
          valueLabel="route name"
          confirmLabel="Archive route"
          confirmDisabled={writeBlocked}
          onCancel={() => setStage(null)}
          onConfirm={() => onArchive?.()}
        >
          Archiving <b>{baseline.name || template.name}</b>:
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
        </TypedConfirmDialog>
      ) : null}
      {stage === 'delete-confirm' && template ? (
        <ConfirmDialog
          title="Delete Planned Route"
          confirmLabel="Delete route"
          cancelLabel="Cancel (Esc)"
          danger
          confirmDisabled={writeBlocked}
          onCancel={() => setStage(null)}
          onConfirm={() => onDelete?.()}
        >
          <b>{template.name}</b> has never been used by a released Quantity
          Flow, so it can be removed completely
          {dirty ? ' (unsaved edits are discarded with it)' : ''}. A route that
          has been used is archived instead.
        </ConfirmDialog>
      ) : null}
    </ModalDialog>
  );
}
