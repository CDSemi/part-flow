import './planned-routes.css';

import { useState } from 'react';

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
import { MOCK_AREAS, areaByKey } from '../../mocks/areas';
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
// instead of deleted; existing snapshots preserve historical route
// definitions, so no separate template-versioning system exists.

type PendingDialog =
  | { kind: 'new' }
  | { kind: 'edit'; template: MockRouteTemplate }
  | { kind: 'usage'; template: MockRouteTemplate }
  | { kind: 'archive'; template: MockRouteTemplate }
  | { kind: 'delete'; template: MockRouteTemplate };

const today = (): string => new Date().toISOString().slice(0, 10);

export function PlannedRoutesView() {
  const preview = getViewStatePreview();
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
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const query = search.trim().toLowerCase();
  const visible = (preview === 'empty' ? [] : templates).filter(
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

  const duplicate = (template: MockRouteTemplate) => {
    const copy: MockRouteTemplate = {
      ...template,
      id: `RT-${String(Date.now()).slice(-4)}`,
      name: `${template.name} (variant)`,
      steps: template.steps.map((s) => ({ ...s })),
      archivedOn: undefined,
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
      ) : (
        <table className="rt-table">
          <thead>
            <tr>
              <th>Planned Route</th>
              <th>Steps</th>
              <th>Status</th>
              <th>Used by</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {[...activeTemplates, ...archivedTemplates].map((template) => (
              <TemplateRow
                key={template.id}
                template={template}
                onAction={(kind) => {
                  if (kind === 'duplicate') duplicate(template);
                  else setDialog({ kind, template });
                }}
              />
            ))}
          </tbody>
        </table>
      )}
      {archivedTemplates.length > 0 ? (
        <p className="rt-sub">
          Archived routes stay here for historical context — they are never
          offered when a new Quantity Flow is released.
        </p>
      ) : null}

      {dialog?.kind === 'new' ? (
        <RouteEditDialog
          onCancel={() => setDialog(null)}
          onSave={(name, description, steps) => {
            setTemplates((current) => [
              ...current,
              {
                id: `RT-${String(Date.now()).slice(-4)}`,
                name,
                description: description || undefined,
                steps,
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
          template={dialog.template}
          onCancel={() => setDialog(null)}
          onSave={(name, description, steps) => {
            update(dialog.template.id, (t) => ({
              ...t,
              name,
              description: description || undefined,
              steps,
              updatedOn: today(),
            }));
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
      {dialog?.kind === 'archive' ? (
        <ConfirmDialog
          title="Archive Planned Route"
          confirmLabel="Archive route"
          cancelLabel="Cancel"
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            update(dialog.template.id, (t) => ({
              ...t,
              archivedOn: today(),
              updatedOn: today(),
            }));
            setDialog(null);
          }}
        >
          <b>{dialog.template.name}</b> stops appearing as a choice for new
          route assignments. The {dialog.template.usedBy.length} Quantity Flow
          {dialog.template.usedBy.length === 1 ? '' : 's'} released with it keep
          {dialog.template.usedBy.length === 1 ? 's' : ''} the assigned route
          unchanged, and the route stays visible here for historical context.
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === 'delete' ? (
        <ConfirmDialog
          title="Delete Planned Route"
          confirmLabel="Delete route"
          cancelLabel="Cancel"
          danger
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setTemplates((current) =>
              current.filter((t) => t.id !== dialog.template.id),
            );
            setDialog(null);
          }}
        >
          <b>{dialog.template.name}</b> has never been used by a released
          Quantity Flow, so it can be removed completely. A route that has been
          used is archived instead.
        </ConfirmDialog>
      ) : null}
    </section>
  );
}

function TemplateRow({
  template,
  onAction,
}: {
  template: MockRouteTemplate;
  onAction: (
    kind: 'edit' | 'duplicate' | 'usage' | 'archive' | 'delete',
  ) => void;
}) {
  const archived = template.archivedOn !== undefined;
  const used = template.usedBy.length > 0;
  return (
    <tr className={archived ? 'archived' : undefined}>
      <td>
        <div className="rtname">{template.name}</div>
        {template.description ? (
          <div className="rtdesc">{template.description}</div>
        ) : null}
      </td>
      <td>
        <div className="rt-steps">
          {template.steps.map((step, i) => (
            <span key={`${step.area}-${i}`} className="stp">
              {i > 0 ? (
                <span className="arw" aria-hidden="true">
                  →
                </span>
              ) : null}
              <AreaDot
                colorVar={areaByKey(step.area)?.colorVar ?? 'var(--faint)'}
                size={9}
              />
              {areaByKey(step.area)?.name ?? step.area}
            </span>
          ))}
        </div>
      </td>
      <td>
        {archived ? (
          <>
            <span className="rt-status archived">Archived</span>
            <div className="rt-statusdate">since {template.archivedOn}</div>
          </>
        ) : (
          <>
            <span className="rt-status active">Active</span>
            <div className="rt-statusdate">updated {template.updatedOn}</div>
          </>
        )}
      </td>
      <td className="rt-usage">
        {used ? (
          <button onClick={() => onAction('usage')}>
            {template.usedBy.length} Quantity Flow
            {template.usedBy.length === 1 ? '' : 's'}…
          </button>
        ) : (
          <span className="never">Never used</span>
        )}
      </td>
      <td>
        <div className="rt-actions">
          {!archived ? (
            <>
              <button onClick={() => onAction('edit')}>Edit…</button>
              <button onClick={() => onAction('duplicate')}>Duplicate</button>
              {used ? (
                <button onClick={() => onAction('archive')}>Archive…</button>
              ) : (
                <button className="danger" onClick={() => onAction('delete')}>
                  Delete…
                </button>
              )}
            </>
          ) : (
            <button onClick={() => onAction('duplicate')}>Duplicate</button>
          )}
        </div>
      </td>
    </tr>
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

/**
 * Create or edit one Planned Route: name, description, and the ordered
 * steps (Area, Operation, advisory expected duration, optional
 * preferred Machine, optional instructions). Steps reorder with
 * explicit up/down controls — no drag requirement for shop-office use.
 */
function RouteEditDialog({
  template,
  onCancel,
  onSave,
}: {
  template?: MockRouteTemplate;
  onCancel: () => void;
  onSave: (name: string, description: string, steps: MockRouteStep[]) => void;
}) {
  const [name, setName] = useState(template?.name ?? '');
  const [description, setDescription] = useState(template?.description ?? '');
  const [steps, setSteps] = useState<EditableStep[]>(() =>
    (
      template?.steps ?? [
        { area: 'material' as AreaKey, operation: 'Receiving' },
      ]
    ).map((step, i) => ({ ...step, key: i })),
  );
  const [error, setError] = useState<string | null>(null);

  const setStep = (key: number, change: Partial<MockRouteStep>) =>
    setSteps((current) =>
      current.map((s) => (s.key === key ? { ...s, ...change } : s)),
    );
  const move = (index: number, delta: -1 | 1) =>
    setSteps((current) => {
      const next = [...current];
      const [step] = next.splice(index, 1);
      next.splice(index + delta, 0, step);
      return next;
    });

  const save = () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('A route name is required.');
      return;
    }
    if (steps.length === 0) {
      setError('A Planned Route needs at least one step.');
      return;
    }
    if (steps.some((s) => !s.operation.trim())) {
      setError('Every step needs an Operation.');
      return;
    }
    onSave(
      trimmedName,
      description.trim(),
      // Explicit projection back to MockRouteStep — the editor-only
      // `key` never leaves the dialog.
      steps.map((step) => ({
        area: step.area,
        operation: step.operation.trim(),
        expectedDuration: step.expectedDuration?.trim() || undefined,
        preferredMachine: step.preferredMachine?.trim() || undefined,
        instructions: step.instructions?.trim() || undefined,
      })),
    );
  };

  return (
    <ModalDialog
      label={template ? 'Edit Planned Route' : 'New Planned Route'}
      onClose={onCancel}
      size="xwide"
    >
      <h3>{template ? 'Edit Planned Route' : 'New Planned Route'}</h3>
      <div className="rt-form">
        <label>Route name</label>
        <input
          className="field"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Bracket std v4"
        />
        <label>Description (optional)</label>
        <input
          className="field"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <label>
          Steps — Area · Operation · expected duration · preferred Machine
        </label>
        <div className="rt-steplist">
          {steps.map((step, index) => (
            <div className="rt-steprow" key={step.key}>
              <span className="idx">{index + 1}</span>
              <select
                aria-label={`Step ${index + 1} Area`}
                value={step.area}
                onChange={(e) =>
                  setStep(step.key, { area: e.target.value as AreaKey })
                }
              >
                {MOCK_AREAS.map((a) => (
                  <option key={a.key} value={a.key}>
                    {a.name}
                  </option>
                ))}
              </select>
              <input
                aria-label={`Step ${index + 1} Operation`}
                placeholder="Operation"
                value={step.operation}
                onChange={(e) =>
                  setStep(step.key, { operation: e.target.value })
                }
              />
              <input
                aria-label={`Step ${index + 1} expected duration`}
                placeholder="e.g. 4h"
                value={step.expectedDuration ?? ''}
                onChange={(e) =>
                  setStep(step.key, { expectedDuration: e.target.value })
                }
              />
              <input
                aria-label={`Step ${index + 1} preferred Machine`}
                placeholder="Preferred Machine"
                value={step.preferredMachine ?? ''}
                onChange={(e) =>
                  setStep(step.key, { preferredMachine: e.target.value })
                }
              />
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
              <input
                className="instr"
                aria-label={`Step ${index + 1} instructions`}
                placeholder="Instructions (optional)"
                value={step.instructions ?? ''}
                onChange={(e) =>
                  setStep(step.key, { instructions: e.target.value })
                }
              />
            </div>
          ))}
        </div>
        <button
          className="rt-addstep"
          onClick={() =>
            setSteps((current) => [
              ...current,
              {
                area: 'lathe',
                operation: '',
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
        {template && template.usedBy.length > 0 ? (
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
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="bigbtn primary" onClick={save}>
          {template ? 'Save route' : 'Create route'}
        </button>
      </div>
    </ModalDialog>
  );
}
