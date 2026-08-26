import './machines.css';

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';

import { errorMessage } from '../../api/client';
import {
  areaColor,
  getMachineAssetTagFormat,
  listAreas,
} from '../../api/environment';
import type { Area } from '../../api/environment';
import {
  clearMaintenance,
  createMachine,
  listLifecycleEvents,
  listMachines,
  reactivateMachine,
  retireMachine,
  startMaintenance,
  updateMachine,
} from '../../api/machines';
import type {
  Machine,
  MachineEditDraft,
  MachineLifecycleEvent,
} from '../../api/machines';
import { useApiData } from '../../api/use-api-data';
import type { ApiDataState } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { useUiClock } from '../../components/ui-clock';
import { AreaDot } from '../../components/indicators';
import { Code128Svg } from '../../components/Code128Svg';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { TypedConfirmDialog } from '../../components/TypedConfirmDialog';
import { UnsavedChoiceDialog } from '../../components/UnsavedChoiceDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatAssetTag, machineBarcode } from '../asset-tags';
import {
  LIFECYCLE_EVENT_LABEL,
  MACHINE_STATE_LABEL,
  formatStateAge,
} from '../machine-state';

// Management → Machines: the single place for operational Machine
// monitoring and authorized Machine configuration. Access is
// permission-based (Production Manager, Process Engineer, Maintenance
// Manager, or another authorized specialist) — full Administrator
// access is deliberately NOT required, and Administration keeps no
// duplicate Machine screen. Focused scope: lifecycle, maintenance and
// asset identification only — PartFlow is not a CMMS (no spare parts,
// no maintenance schedules, no service contracts, no cost accounting).
//
// Phase 3.5: this view reads and writes the real Machine registry
// through /api/machines. Since Phase 6 the server reports the ACTIVE
// quantity assigned to each Machine (`assignedQuantity`, derived from
// the production projection) and the derived state itself
// (`operationalState`, the same derivation the Scan Station cards show):
// the Assigned now column shows the total, and retirement stays blocked
// while it is above zero. The per-PN breakdown of that quantity arrives
// with the monitoring read models.

type PendingDialog =
  | { kind: 'new' }
  | { kind: 'edit'; machine: Machine }
  | { kind: 'start-maintenance'; machine: Machine }
  | { kind: 'clear-maintenance'; machine: Machine }
  | { kind: 'retired-details'; machine: Machine }
  | { kind: 'reactivate'; machine: Machine };

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
  rows: Machine[],
  dir: SortDir,
  value: (m: Machine) => string | number,
): Machine[] {
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
function confirmIdentifier(machine: Machine): {
  value: string;
  label: string;
} {
  return { value: machine.assetTag, label: 'Asset Tag' };
}

// Long-data preview Machines (?state=long, development builds only):
// many rows plus over-long display names, manufacturer/model/serial
// and notes, to exercise dense-table and truncation behavior. Never
// part of the server data — added to the rendered list only.
const longPreviewMachines: ((areaId: number) => Machine[]) | null = import.meta
  .env.DEV
  ? (areaId: number) => [
      ...Array.from({ length: 15 }, (_, i): Machine => {
        const n = i + 1;
        const tag = `CD-LONG-${String(n).padStart(4, '0')}`;
        return {
          id: -n,
          areaId,
          name: `Long preview Machine ${n} — extended qualification cell`,
          assetTag: tag,
          barcode: machineBarcode(tag),
          stateChangedAt: '2026-07-01T00:00:00.000Z',
          assignedQuantity: 0,
          operationalState: 'idle',
          manufacturer: 'Long-Preview Manufacturing Equipment Co.',
          model: `LP-${String(9000 + n)}-EXTENDED-MODEL-DESIGNATION`,
          serialNumber: `LONG-PREVIEW-SERIAL-${String(100000 + n)}`,
          installedOn: '2020-01-01',
        };
      }),
      {
        id: -16,
        areaId,
        name: 'Supplemental long-preview Machine — extended display name for dense-table layout testing only',
        assetTag: 'CD-LONG-SUPPLEMENTAL',
        barcode: machineBarcode('CD-LONG-SUPPLEMENTAL'),
        stateChangedAt: '2026-07-01T00:00:00.000Z',
        assignedQuantity: 0,
        operationalState: 'idle',
        manufacturer:
          'Supplemental Long-Preview Precision Machinery Manufacturing',
        model: 'SUPPLEMENTAL-LONG-PREVIEW-MODEL-DESIGNATION-EXTENDED-2026',
        serialNumber: 'SUPPLEMENTAL-LONG-PREVIEW-SERIAL-NUMBER-000001',
        installedOn: '2020-01-01',
        notes:
          'Auto-generated long-data preview Machine with an over-long notes field, used only to exercise layout and truncation in the Machines table — not real equipment.',
      },
    ]
  : null;

export function MachinesView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const machinesData = useApiData(listMachines);
  const areasData = useApiData(listAreas);
  const formatData = useApiData(getMachineAssetTagFormat);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  // Clear maintenance runs through the generic ConfirmDialog, so its
  // request state lives here with the dialog selection.
  const [clearBusy, setClearBusy] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
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

  const openDialog = (next: PendingDialog | null) => {
    setClearError(null);
    setDialog(next);
  };

  const reloadAll = () => {
    machinesData.reload();
    areasData.reload();
    formatData.reload();
  };

  if (
    preview === 'loading' ||
    machinesData.state.status === 'loading' ||
    areasData.state.status === 'loading' ||
    formatData.state.status === 'loading'
  ) {
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
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }
  if (machinesData.state.status === 'error') {
    return (
      <section className="mg" aria-label="Machines">
        <ErrorState
          message="Machine data could not be loaded."
          detail={machinesData.state.message}
          onRetry={reloadAll}
        />
      </section>
    );
  }
  if (areasData.state.status === 'error') {
    return (
      <section className="mg" aria-label="Machines">
        <ErrorState
          message="Machine data could not be loaded."
          detail={areasData.state.message}
          onRetry={reloadAll}
        />
      </section>
    );
  }
  if (formatData.state.status === 'error') {
    return (
      <section className="mg" aria-label="Machines">
        <ErrorState
          message="Machine data could not be loaded."
          detail={formatData.state.message}
          onRetry={reloadAll}
        />
      </section>
    );
  }

  const machines = machinesData.state.data;
  const areas = areasData.state.data;
  const format = formatData.state.data;
  const areaById = new Map(areas.map((area) => [area.id, area]));
  // New Machines and reactivations choose among active non-terminal
  // Areas — a terminal Area holds completed quantity, never Machines.
  const areaChoices = areas.filter((area) => area.isActive && !area.isTerminal);
  const assetTagPreview = format
    ? formatAssetTag(format, format.nextSequence)
    : null;
  const canCreate = assetTagPreview !== null && areaChoices.length > 0;

  const query = search.trim().toLowerCase();
  const matches = (machine: Machine): boolean =>
    !query ||
    [
      machine.name,
      areaById.get(machine.areaId)?.name ?? '',
      machine.assetTag,
      machine.model ?? '',
      machine.manufacturer ?? '',
    ]
      .join(' ')
      .toLowerCase()
      .includes(query);

  const baseMachines =
    preview === 'long' && longPreviewMachines
      ? [...machines, ...longPreviewMachines(areas[0]?.id ?? 0)]
      : machines;
  const visible = preview === 'empty' ? [] : baseMachines.filter(matches);
  const unsortedActive = visible.filter((m) => m.retiredOn === undefined);
  const unsortedRetired = visible.filter((m) => m.retiredOn !== undefined);

  /** Active-table column value driving the current sort. */
  const sortValue = (m: Machine, key: SortKey): string | number => {
    switch (key) {
      case 'machine':
        return m.name.toLowerCase();
      case 'state':
        return STATE_SORT_RANK[m.operationalState];
      case 'assigned':
        return m.assignedQuantity;
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
    m: Machine,
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

  /** Complete one successful write: fresh server state, dialog closed.
   * The Asset Tag counter reloads too — creation consumes it. */
  const completeWrite = () => {
    machinesData.reload();
    formatData.reload();
    setDialog(null);
  };

  return (
    <section className="mg" aria-label="Machines">
      <h1>Machines</h1>
      <p className="mg-sub">
        Monitor Machine status, assignments, maintenance, and configuration.
        Running and Idle are based on assigned quantity.
      </p>
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
          disabled={writeBlocked || !canCreate}
          onClick={() => openDialog({ kind: 'new' })}
        >
          + New Machine
        </button>
      </div>
      {!canCreate ? (
        <PageNote>
          {assetTagPreview === null
            ? 'New Machines need the Machine Asset Tag format. Configure it in Administration → Barcode configuration first.'
            : 'New Machines need at least one active non-terminal Area. Configure Areas in Administration first.'}
        </PageNote>
      ) : null}

      {active.length === 0 ? (
        <EmptyState
          message={
            query
              ? `No Machines match “${search.trim()}”.`
              : 'No Machines configured yet.'
          }
        />
      ) : (
        <table className="mg-table mg-active">
          <thead>
            <tr>
              {(
                [
                  { key: 'machine', label: 'Machine', className: undefined },
                  { key: 'state', label: 'State', className: 'mg-statecol' },
                  {
                    key: 'assigned',
                    label: 'Assigned now',
                    className: 'mg-assignedcol',
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
                area={areaById.get(machine.areaId)}
                writeBlocked={writeBlocked}
                onOpenEdit={() => openDialog({ kind: 'edit', machine })}
                onToggleMaintenance={() =>
                  openDialog({
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
                    openDialog({ kind: 'retired-details', machine })
                  }
                >
                  <td>
                    <button
                      className="rowbtn"
                      aria-label={`Machine details — ${machine.name}`}
                    >
                      <MachineIdentityCell
                        machine={machine}
                        area={areaById.get(machine.areaId)}
                      />
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
                  <td className="mg-meta" data-label="Notes">
                    {machine.notes ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <PageNote>
            Retired Machines remain in history and cannot receive new work or
            assignments. Reactivate only the same physical Machine. Replacements
            may reuse the display name but require a new Machine record and
            Asset Tag.
          </PageNote>
        </div>
      ) : null}

      {dialog?.kind === 'new' ? (
        <MachineEditDialog
          machines={machines}
          areaById={areaById}
          areaChoices={areaChoices}
          assetTagPreview={assetTagPreview ?? undefined}
          assignedQty={0}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onCreate={async (input) => {
            try {
              await createMachine(input);
            } catch (error) {
              // A stale Asset Tag preview (409) consumed nothing — the
              // refreshed counter feeds the corrected preview.
              formatData.reload();
              throw error;
            }
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <MachineEditDialog
          machines={machines}
          machine={dialog.machine}
          areaById={areaById}
          areaChoices={areaChoices}
          assignedQty={dialog.machine.assignedQuantity}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (draft) => {
            await updateMachine(dialog.machine.id, draft);
            completeWrite();
          }}
          onRetire={async (edits) => {
            await retireMachine(dialog.machine.id, { edits });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'start-maintenance' ? (
        <StartMaintenanceDialog
          machine={dialog.machine}
          assignedQty={dialog.machine.assignedQuantity}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onConfirm={async (note, expectedReturn) => {
            await startMaintenance(dialog.machine.id, {
              note: note || null,
              expectedReturn: expectedReturn || null,
            });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'clear-maintenance' ? (
        <ConfirmDialog
          title="Clear maintenance"
          confirmLabel="Clear maintenance"
          cancelLabel="Cancel (Esc)"
          confirmDisabled={writeBlocked || clearBusy}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setClearBusy(true);
            setClearError(null);
            clearMaintenance(dialog.machine.id).then(
              () => {
                setClearBusy(false);
                completeWrite();
              },
              (error: unknown) => {
                setClearBusy(false);
                setClearError(errorMessage(error));
              },
            );
          }}
        >
          {dialog.machine.assignedQuantity > 0 ? (
            <>
              <b>{dialog.machine.name}</b> will return to <b>Running</b> —{' '}
              {dialog.machine.assignedQuantity} pcs are still assigned to it.
              Maintenance never moved or released that quantity.
            </>
          ) : (
            <>
              <b>{dialog.machine.name}</b> will return to <b>Idle</b> — no
              quantity is currently assigned to it.
            </>
          )}
          {clearError ? (
            <div className="err" role="alert">
              {clearError}
            </div>
          ) : null}
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === 'retired-details' ? (
        <RetiredMachineDetailsDialog
          machine={dialog.machine}
          area={areaById.get(dialog.machine.areaId)}
          areaById={areaById}
          writeBlocked={writeBlocked}
          onClose={() => setDialog(null)}
          onReactivate={() =>
            openDialog({ kind: 'reactivate', machine: dialog.machine })
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
          areaById={areaById}
          areaChoices={areaChoices}
          writeBlocked={writeBlocked}
          cancelLabel="‹ Back"
          onCancel={() =>
            openDialog({ kind: 'retired-details', machine: dialog.machine })
          }
          onConfirm={async ({ name, areaId, reason }) => {
            await reactivateMachine(dialog.machine.id, {
              name,
              areaId,
              reason,
            });
            completeWrite();
          }}
        />
      ) : null}
    </section>
  );
}

function MachineIdentityCell({
  machine,
  area,
}: {
  machine: Machine;
  area: Area | undefined;
}) {
  return (
    <>
      <div className="mgname">{machine.name}</div>
      <div className="mgarea">
        <AreaDot colorVar={areaColor(area)} size={9} />
        {area?.name ?? '—'}
      </div>
    </>
  );
}

function AssetMeta({ machine }: { machine: Machine }) {
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
  writeBlocked = false,
  onToggle,
}: {
  machine: Machine;
  /** Disables the switch while the backend is unreachable — starting
   * or clearing Maintenance is a production-configuration write
   * (offline write-block). */
  writeBlocked?: boolean;
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
      disabled={writeBlocked}
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
  area,
  writeBlocked = false,
  onOpenEdit,
  onToggleMaintenance,
}: {
  machine: Machine;
  area: Area | undefined;
  /** Disables the row's Maintenance switch while the backend is
   * unreachable (offline write-block); opening Edit Machine to read
   * stays available. */
  writeBlocked?: boolean;
  onOpenEdit: () => void;
  onToggleMaintenance: () => void;
}) {
  const qty = machine.assignedQuantity;
  // The state is the server's derivation — the same value the Scan
  // Station Machine cards show — never re-derived from the quantity.
  const status = machine.operationalState;
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
          <MachineIdentityCell machine={machine} area={area} />
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
      {/* The Assigned now column is the first real column to yield on
          narrow viewports (hidden with the header cell — GUI_DESIGN
          §2.5 column shedding). */}
      <td className="mg-assignedcol">
        {qty === 0 ? (
          <span className="mg-meta">—</span>
        ) : (
          <div className="mg-assign">
            <span className={`q ${status}`}>{qty}</span>{' '}
            <span className="unit">pcs assigned</span>
          </div>
        )}
      </td>
      <td className="mg-metacol">
        <AssetMeta machine={machine} />
      </td>
      <td className="mg-maintcol" onClick={(event) => event.stopPropagation()}>
        <MaintenanceSwitch
          machine={machine}
          writeBlocked={writeBlocked}
          onToggle={onToggleMaintenance}
        />
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
  value: number;
  choices: Area[];
  onChange: (areaId: number) => void;
}) {
  const fieldId = useId();
  const selected = choices.find((choice) => choice.id === value);
  return (
    <>
      <label className="mg-arealabelrow" htmlFor={fieldId}>
        {label}
        <span
          className="mg-arealine"
          style={{ background: areaColor(selected) }}
          aria-hidden="true"
        />
      </label>
      <select
        id={fieldId}
        className="field"
        value={String(value)}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {choices.map((choice) => (
          <option key={choice.id} value={String(choice.id)}>
            {choice.name}
          </option>
        ))}
      </select>
    </>
  );
}

/** One confirmation-summary row (the Scan Station §4.6 idiom). */
interface SummaryRow {
  label: string;
  value: ReactNode;
  /** `primary` rows scan first (bigger, bolder); `secondary` context
   * rows stay present but quiet. Weight and size carry the
   * distinction — never color alone. */
  emphasis?: 'primary' | 'secondary';
  /** Additive semantic value tone on top of the emphasis. */
  tone?: 'ok' | 'warn' | 'err';
}

/**
 * Key/value recap shared by the final confirmation summaries and the
 * Retired Machine Details dialog — the same two-column definition-list
 * presentation as the Scan Station confirmation summaries (§4.6):
 * content-sized label column on a quiet panel, primary values leading,
 * secondary context receding, semantic tones additive only.
 */
function SummaryList({ rows }: { rows: SummaryRow[] }) {
  return (
    <dl className="mg-summary">
      {rows.map((row) => (
        <Fragment key={row.label}>
          <dt className={row.emphasis}>{row.label}</dt>
          <dd
            className={`${row.emphasis ?? ''}${
              row.tone ? ` tone-${row.tone}` : ''
            }`}
          >
            {row.value}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** Barcode value in the shared app-wide reading tone (global
 * `.barcodeval`) — plain verification text, never a badge. */
function BarcodeValue({ value }: { value: string }) {
  return <span className="barcodeval">{value}</span>;
}

/** Load one Machine's append-only lifecycle history from the API. */
function useLifecycleEvents(machineId: number | undefined) {
  const load = useCallback(
    () =>
      machineId === undefined
        ? Promise.resolve([] as MachineLifecycleEvent[])
        : listLifecycleEvents(machineId),
    [machineId],
  );
  return useApiData(load);
}

/**
 * Append-only lifecycle audit (RETIRED / REACTIVATED) as the ONE
 * shared vertical-timeline presentation — tone-ringed markers on a
 * hairline rail (error for RETIRED, success for REACTIVATED; the event
 * name always renders — color is never the only distinction), with
 * date, actor (when recorded), reason, and the previous → current Area
 * on a move. Used by Edit Machine, Reactivate Machine and the Retired
 * Machine Details dialog. `compact` keeps the rail and markers but
 * renders each event on ONE line (Edit Machine — the audit stays
 * present without claiming form height). Without events it renders
 * `emptyText` when given, nothing otherwise.
 */
function LifecycleTimeline({
  state,
  areaById,
  emptyText,
  compact = false,
}: {
  state: ApiDataState<MachineLifecycleEvent[]>;
  areaById: Map<number, Area>;
  emptyText?: string;
  compact?: boolean;
}) {
  if (state.status === 'loading') return null;
  if (state.status === 'error') {
    return (
      <div className={`mg-timeline${compact ? ' compact' : ''}`}>
        <div className="mg-lifetitle">Machine History</div>
        <p className="tl-empty">
          Machine history could not be loaded. {state.message}
        </p>
      </div>
    );
  }
  const events = state.data;
  if (events.length === 0 && !emptyText) return null;
  const areaName = (id: number | undefined) =>
    id === undefined ? undefined : (areaById.get(id)?.name ?? `Area ${id}`);
  return (
    <div className={`mg-timeline${compact ? ' compact' : ''}`}>
      <div className="mg-lifetitle">Machine History</div>
      {events.length === 0 ? (
        <p className="tl-empty">{emptyText}</p>
      ) : (
        <ol>
          {events.map((event) => (
            <li
              className={`mg-tlevent ${
                event.event === 'RETIRED' ? 'ev-retired' : 'ev-reactivated'
              }`}
              key={event.id}
            >
              <span className="dot" aria-hidden="true" />
              {compact ? (
                <div className="tl-line">
                  <span className="ev">
                    {LIFECYCLE_EVENT_LABEL[event.event]}
                  </span>{' '}
                  <span className="at">{event.at.slice(0, 10)}</span>
                  {event.actor ? <> · {event.actor}</> : null}
                  {event.reason ? <> — {event.reason}</> : null}
                  {event.fromAreaId !== undefined &&
                  event.toAreaId !== undefined ? (
                    <>
                      {' '}
                      · {areaName(event.fromAreaId)} →{' '}
                      {areaName(event.toAreaId)}
                    </>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="tl-head">
                    <span className="ev">
                      {LIFECYCLE_EVENT_LABEL[event.event]}
                    </span>
                    <span className="at">{event.at.slice(0, 10)}</span>
                  </div>
                  {event.actor ? (
                    <div className="tl-meta">{event.actor}</div>
                  ) : null}
                  {event.reason ? (
                    <div className="tl-reason">{event.reason}</div>
                  ) : null}
                  {event.fromAreaId !== undefined &&
                  event.toAreaId !== undefined ? (
                    <div className="tl-meta">
                      {areaName(event.fromAreaId)} → {areaName(event.toAreaId)}
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
  area,
  onOpenLabel,
}: {
  assetTag: string;
  machine?: Machine;
  area?: Area;
  onOpenLabel?: () => void;
}) {
  return (
    <div className="mg-idhead">
      <div className="idcol">
        <span className="idlabel">Asset Tag</span>
        <span className="idvalue tag">{assetTag}</span>
      </div>
      <div className="idcol grow">
        <span className="idlabel">Barcode</span>
        <span className="idvalue barcodeval">{machineBarcode(assetTag)}</span>
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
              style={{ background: areaColor(area) }}
              aria-hidden="true"
            />
          </div>
          <div className="mg-areafixedvalue">
            <AreaDot colorVar={areaColor(area)} size={11} />
            {area?.name ?? '—'}
          </div>
          <p className="idareahelp">
            The Area cannot be changed while the Machine is active. If the
            physical Machine moves to another Area, retire it first and update
            the Area when it is reactivated.
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
  machine: Machine;
  onClose: () => void;
}) {
  const value = machineBarcode(machine.assetTag);
  return (
    <ModalDialog label="Machine barcode label" onClose={onClose}>
      <h3>Machine barcode label</h3>
      <div className="sub">
        Scan this label to identify <b>{machine.name}</b>.
      </div>
      <div className="mg-label mg-labelprint">
        <div className="lname">{machine.name}</div>
        <div className="ltag">{machine.assetTag}</div>
        <Code128Svg className="lbarcode" value={value} />
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
 * first. The decision is only RECORDED (v17): it travels with the
 * retirement request and the server applies it in the same transaction
 * as the retirement and its lifecycle event, so cancelling the typed
 * confirmation or the final summary returns to the form with the edits
 * intact.
 */
function MachineEditDialog({
  machines,
  machine,
  areaById,
  areaChoices,
  assetTagPreview,
  assignedQty,
  writeBlocked = false,
  onCancel,
  onCreate,
  onSave,
  onRetire,
}: {
  machines: Machine[];
  machine?: Machine;
  areaById: Map<number, Area>;
  areaChoices: Area[];
  /** The next Asset Tag the server will assign (new Machine only). */
  assetTagPreview?: string;
  assignedQty: number;
  /** Disables Save and Retire… while the backend is unreachable
   * (Management → Machines offline write-block). */
  writeBlocked?: boolean;
  onCancel: () => void;
  /** Create the Machine (new only). Rejects with the server message. */
  onCreate?: (input: {
    areaId: number;
    name: string;
    manufacturer: string | null;
    model: string | null;
    serialNumber: string | null;
    installedOn: string | null;
    notes: string | null;
    expectedAssetTag: string | null;
  }) => Promise<void>;
  /** Save the edit draft (existing only). Rejects with the message. */
  onSave?: (draft: MachineEditDraft) => Promise<void>;
  /** Retire with the recorded edits decision (existing only). */
  onRetire?: (edits: MachineEditDraft | null) => Promise<void>;
}) {
  const initial = {
    name: machine?.name ?? '',
    areaId: machine?.areaId ?? areaChoices[0]?.id ?? 0,
    manufacturer: machine?.manufacturer ?? '',
    model: machine?.model ?? '',
    serialNumber: machine?.serialNumber ?? '',
    installedOn: machine?.installedOn ?? '',
    notes: machine?.notes ?? '',
    maintenanceNote: machine?.maintenance?.note ?? '',
    maintenanceReturn: machine?.maintenance?.expectedReturn ?? '',
  };
  const [name, setName] = useState(initial.name);
  const [areaId, setAreaId] = useState(initial.areaId);
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
  // One write request at a time; a rejected write shows the server's
  // message where the user currently is instead of closing anything.
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const events = useLifecycleEvents(machine?.id);
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
  // next tag the server will assign on create (sent along as the
  // optimistic precondition, so a stale preview can never silently
  // become a different tag).
  const dialogAssetTag = machine?.assetTag ?? assetTagPreview ?? '—';

  const trimmedName = name.trim();
  const targetAreaId = machine?.areaId ?? areaId;
  const targetArea = areaById.get(targetAreaId);
  const targetAreaName = targetArea?.name ?? '—';
  // Display names stay unique among the active Machines of one Area
  // (reuse across time and replacements stays allowed). The check here
  // is live field feedback; the server enforces it authoritatively.
  const nameCollision = machines.some(
    (m) =>
      m.id !== machine?.id &&
      m.retiredOn === undefined &&
      m.areaId === targetAreaId &&
      m.name === trimmedName,
  );
  const [nameAttempted, setNameAttempted] = useState(false);
  const nameFeedback = nameCollision ? (
    <div className="err" role="alert">
      ✕ “{trimmedName}” already exists in {targetAreaName}.
    </div>
  ) : !trimmedName && nameAttempted ? (
    <div className="err" role="alert">
      A display name is required.
    </div>
  ) : trimmedName && trimmedName !== (machine?.name ?? '') ? (
    <div className="mg-fieldok">
      ✓ “{trimmedName}” is available in {targetAreaName}.
    </div>
  ) : null;

  /** The one Save-changes draft (Edit) / retire edits payload. */
  const buildDraft = (): MachineEditDraft => ({
    name: trimmedName,
    manufacturer: manufacturer.trim() || null,
    model: model.trim() || null,
    serialNumber: serialNumber.trim() || null,
    installedOn: installedOn || null,
    notes: notes.trim() || null,
    // Update the maintenance context in place — `since` and the
    // Maintenance state itself never change here.
    ...(machine?.maintenance
      ? {
          maintenanceNote: maintenanceNote.trim() || null,
          maintenanceExpectedReturn: maintenanceReturn || null,
        }
      : {}),
  });
  const draftError = !trimmedName
    ? 'A display name is required.'
    : nameCollision
      ? `An active Machine named “${trimmedName}” already exists in ${targetAreaName}. Display names stay unique among the active Machines of one Area.`
      : null;

  const submitSave = async () => {
    if (!onSave) return;
    setBusy(true);
    setServerError(null);
    try {
      await onSave(buildDraft());
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const submitCreate = async () => {
    if (!onCreate) return;
    setBusy(true);
    setServerError(null);
    try {
      await onCreate({
        areaId: targetAreaId,
        name: trimmedName,
        manufacturer: manufacturer.trim() || null,
        model: model.trim() || null,
        serialNumber: serialNumber.trim() || null,
        installedOn: installedOn || null,
        notes: notes.trim() || null,
        expectedAssetTag: assetTagPreview ?? null,
      });
    } catch (error) {
      setServerError(errorMessage(error));
      setAddStage('summary');
    } finally {
      setBusy(false);
    }
  };

  /** Edit: save immediately. New: validate, then enter the summary.
   * Name problems already render at the field — save only blocks. */
  const save = () => {
    if (!trimmedName) {
      setNameAttempted(true);
      return;
    }
    if (nameCollision) return;
    if (machine) {
      void submitSave();
      return;
    }
    setServerError(null);
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
    setServerError(null);
    setRetireEditsIntent(null);
    setRetireStage(dirty ? 'unsaved' : 'confirm');
  };

  /** Leave the retire flow — the form keeps its (unsaved) edits. */
  const cancelRetire = () => {
    setRetireStage(null);
    setRetireEditsIntent(null);
  };

  /** The retirement really happens HERE: the recorded edits decision
   * travels with the request and commits in ONE transaction with the
   * retirement and its lifecycle event. */
  const finalizeRetire = async () => {
    if (!onRetire) return;
    setBusy(true);
    setServerError(null);
    try {
      await onRetire(retireEditsIntent === 'save' ? buildDraft() : null);
    } catch (error) {
      setServerError(errorMessage(error));
      setRetireStage('summary');
    } finally {
      setBusy(false);
    }
  };

  const identifier = machine ? confirmIdentifier(machine) : null;
  const selectedArea = areaById.get(targetAreaId);
  // The summary shows the record as it will be retired: with the
  // edits when the recorded decision is Save, as last saved otherwise.
  const summaryName =
    retireEditsIntent === 'save' && !draftError
      ? trimmedName
      : (machine?.name ?? trimmedName);

  const serverErrorBlock = serverError ? (
    <div className="err" role="alert">
      {serverError}
    </div>
  ) : null;

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
        area={machine ? areaById.get(machine.areaId) : undefined}
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
            <div className="mg-span2 mg-fieldcol">
              <Field label="Display name">
                <input
                  className="field"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Lathe 5"
                />
              </Field>
              {nameFeedback}
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
            <div className="mg-fieldcol">
              <Field label="Display name">
                <input
                  ref={nameInputRef}
                  className="field"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Lathe 5"
                />
              </Field>
              {nameFeedback}
            </div>
            <div className="mg-areacell">
              <AreaSelectField
                label="Area"
                value={areaId}
                choices={areaChoices}
                onChange={setAreaId}
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
              Update the maintenance reason or expected return date here. To end
              maintenance, use the Maintenance switch in the Machines list.
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
        {machine ? (
          <LifecycleTimeline state={events.state} areaById={areaById} compact />
        ) : null}
        {!machine ? (
          <div className="mg-note">
            Replacing a physical Machine? Retire the old Machine record and
            create a new one — the new Machine gets its own new Asset Tag and
            barcode and may reuse the familiar display name. History always
            keeps the Machine that really did the work.
          </div>
        ) : null}
      </div>
      {retireStage === null && addStage === null ? serverErrorBlock : null}
      <div className="row">
        <button className="bigbtn ghost" onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={save}
        >
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
              disabled={assignedQty > 0 || writeBlocked || busy}
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
            draftError
              ? `The edits cannot be saved yet: ${draftError}`
              : undefined
          }
          saveDisabled={writeBlocked || busy}
          onCancel={() => setLeaveConfirm(false)}
          onSave={() => {
            if (draftError) return;
            setLeaveConfirm(false);
            void submitSave();
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
          Your entered information will not be saved.
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
            draftError
              ? `The edits cannot be saved yet: ${draftError}`
              : undefined
          }
          onCancel={cancelRetire}
          onSave={() => {
            if (draftError) return;
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
          confirmDisabled={writeBlocked || busy}
          onCancel={cancelRetire}
          onConfirm={() => setRetireStage('summary')}
        >
          Retiring <b>{machine.name}</b>:
          <ul className="mg-consequences">
            <li>It disappears from Machine assignment choices.</li>
            <li>
              Its barcode (
              <span className="barcodeval">
                {machineBarcode(machine.assetTag)}
              </span>
              ) no longer accepts assignment scans.
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
      {(retireStage === 'summary' || retireStage === 'final') && machine ? (
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
              {
                label: 'Machine',
                value: summaryName,
                emphasis: 'primary',
              },
              {
                label: 'Area',
                value: (
                  <>
                    <AreaDot colorVar={areaColor(selectedArea)} size={10} />
                    {areaById.get(machine.areaId)?.name ?? '—'}
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
                  <BarcodeValue value={machineBarcode(machine.assetTag)} />
                ),
                emphasis: 'secondary',
              },
              ...(retireEditsIntent
                ? [
                    {
                      label: 'Unsaved edits',
                      value:
                        retireEditsIntent === 'save'
                          ? 'Saved with the retirement'
                          : 'Discarded — retires as last saved',
                      emphasis: 'secondary',
                      tone: 'warn',
                    } as const,
                  ]
                : []),
            ]}
          />
          <div className="mg-note">
            The Machine leaves all assignment choices and its barcode stops
            accepting assignment scans. All history is preserved — the record
            moves to Retired Machines and is never deleted.
          </div>
          {serverErrorBlock}
          <div className="row">
            <button className="bigbtn ghost" onClick={cancelRetire}>
              Cancel (Esc)
            </button>
            <button
              className="bigbtn danger"
              disabled={busy}
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
              confirmDisabled={writeBlocked || busy}
              onCancel={() => setRetireStage('summary')}
              onConfirm={() => void finalizeRetire()}
            >
              <b>{machine.name}</b> will be retired. This retirement is
              permanently recorded in Machine history. If the same Machine is
              reactivated later, the retirement remains recorded.
            </ConfirmDialog>
          ) : null}
        </ModalDialog>
      ) : null}
      {!machine && addStage !== null && !draftError ? (
        <ModalDialog
          label="Confirm new Machine"
          onClose={() => setAddStage(null)}
        >
          <h3>Confirm new Machine</h3>
          <div className="sub">
            Final check — nothing has been added yet. <b>{trimmedName}</b> is
            added only when you confirm below.
          </div>
          <SummaryList
            rows={[
              {
                label: 'Machine',
                value: trimmedName,
                emphasis: 'primary',
              },
              {
                label: 'Area',
                value: (
                  <>
                    <AreaDot colorVar={areaColor(selectedArea)} size={10} />
                    {selectedArea?.name ?? '—'}
                  </>
                ),
              },
              {
                label: 'Asset Tag',
                value: <span className="mono">{dialogAssetTag}</span>,
              },
              {
                label: 'Barcode',
                value: <BarcodeValue value={machineBarcode(dialogAssetTag)} />,
                emphasis: 'secondary',
              },
              ...(manufacturer.trim()
                ? [
                    {
                      label: 'Manufacturer',
                      value: manufacturer.trim(),
                      emphasis: 'secondary',
                    } as const,
                  ]
                : []),
              ...(model.trim()
                ? [
                    {
                      label: 'Model',
                      value: model.trim(),
                      emphasis: 'secondary',
                    } as const,
                  ]
                : []),
              ...(serialNumber.trim()
                ? [
                    {
                      label: 'Serial number',
                      value: (
                        <span className="mono">{serialNumber.trim()}</span>
                      ),
                      emphasis: 'secondary',
                    } as const,
                  ]
                : []),
              ...(installedOn
                ? [
                    {
                      label: 'Installed',
                      value: installedOn,
                      emphasis: 'secondary',
                    } as const,
                  ]
                : []),
              ...(notes.trim()
                ? [
                    {
                      label: 'Notes',
                      value: notes.trim(),
                      emphasis: 'secondary',
                    } as const,
                  ]
                : []),
            ]}
          />
          <div className="mg-note">
            The Asset Tag and barcode are assigned when the Machine is added and
            never change afterwards. A Machine record is permanent — it can be
            retired, never deleted.
          </div>
          {serverErrorBlock}
          <div className="row">
            <button className="bigbtn ghost" onClick={() => setAddStage(null)}>
              Back
            </button>
            <button
              className="bigbtn primary"
              disabled={busy}
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
              confirmDisabled={writeBlocked || busy}
              onCancel={() => setAddStage('summary')}
              onConfirm={() => void submitCreate()}
            >
              Add <b>{trimmedName}</b> with Asset Tag <b>{dialogAssetTag}</b>? A
              Machine record is permanent and <b>cannot be deleted or undone</b>{' '}
              once added — it can only be retired later.
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
  area,
  areaById,
  writeBlocked = false,
  onClose,
  onReactivate,
}: {
  machine: Machine;
  area: Area | undefined;
  areaById: Map<number, Area>;
  /** Disables Reactivate while the backend is unreachable (Management
   * → Machines offline write-block); viewing history stays available. */
  writeBlocked?: boolean;
  onClose: () => void;
  onReactivate: () => void;
}) {
  const events = useLifecycleEvents(machine.id);
  return (
    <ModalDialog label="Retired Machine Details" onClose={onClose} size="wide">
      <h3>Retired Machine Details</h3>
      <div className="mg-rdhead">
        <div className="rdid">
          <span className="nm">{machine.name}</span>
          <span className="rdarea">
            <AreaDot colorVar={areaColor(area)} size={9} />
            {area?.name ?? '—'}
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
          <span className="idvalue barcodeval">
            {machineBarcode(machine.assetTag)}
          </span>
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
        state={events.state}
        areaById={areaById}
        emptyText="No Machine history recorded."
      />
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Close (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked}
          onClick={onReactivate}
        >
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
  writeBlocked = false,
  onCancel,
  onConfirm,
}: {
  machine: Machine;
  assignedQty: number;
  /** Disables Start maintenance while the backend is unreachable
   * (Management → Machines offline write-block). */
  writeBlocked?: boolean;
  onCancel: () => void;
  /** Start the override. Rejects with the server's message. */
  onConfirm: (note: string, expectedReturn: string) => Promise<void>;
}) {
  const [note, setNote] = useState('');
  const [expectedReturn, setExpectedReturn] = useState('');
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setServerError(null);
    try {
      await onConfirm(note.trim(), expectedReturn);
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalDialog label="Start maintenance" onClose={onCancel}>
      <h3>Start maintenance</h3>
      <div className="sub">
        <b>{machine.name}</b> will switch to <b>Maintenance</b>.{' '}
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
        {serverError ? (
          <div className="err" role="alert">
            {serverError}
          </div>
        ) : null}
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
          Start maintenance
        </button>
      </div>
    </ModalDialog>
  );
}

/**
 * Return-to-service of the SAME physical machine (v15): the record,
 * identity, barcode, asset metadata and history stay untouched; the
 * retirement date clears and one REACTIVATED lifecycle event is
 * appended — atomically, by the server. The machine normally returns
 * as Idle (running stays derived from assigned quantity —
 * reactivation never invents an assignment). Blocked while its Asset
 * Tag (which is also its barcode) or serial number has been reissued
 * to another active Machine, and while the display name would collide
 * with an active Machine in the target Area (names must stay unique
 * among active Machines of one Area — assignment displays rely on
 * them); a rename inside this dialog resolves the collision. If the
 * physical machine moved while retired, a new active Area may be
 * chosen here — forward-looking only, historical Movements keep their
 * recorded Areas.
 */
function ReactivateMachineDialog({
  machine,
  machines,
  areaById,
  areaChoices,
  cancelLabel = 'Cancel (Esc)',
  writeBlocked = false,
  onCancel,
  onConfirm,
}: {
  machine: Machine;
  machines: Machine[];
  areaById: Map<number, Area>;
  areaChoices: Area[];
  /** Label of the leave-the-workflow action — `‹ Back` when the dialog
   * is entered from the Retired Machine Details dialog. */
  cancelLabel?: string;
  /** Disables the final Reactivate confirmation while the backend is
   * unreachable (Management → Machines offline write-block). */
  writeBlocked?: boolean;
  onCancel: () => void;
  /** Reactivate the Machine. Rejects with the server's message. */
  onConfirm: (result: {
    name: string;
    areaId: number;
    reason: string;
  }) => Promise<void>;
}) {
  const [name, setName] = useState(machine.name);
  const [areaId, setAreaId] = useState(
    areaChoices.some((choice) => choice.id === machine.areaId)
      ? machine.areaId
      : (areaChoices[0]?.id ?? machine.areaId),
  );
  const [reason, setReason] = useState('');
  const [samePhysical, setSamePhysical] = useState(false);
  // Validation errors render AT the failing field, never as one
  // catch-all block at the bottom of the form. Each entry clears
  // itself as soon as the user fixes the field.
  const [fieldErrors, setFieldErrors] = useState<{
    name?: string;
    reason?: string;
    physical?: string;
  }>({});
  // Staged confirmation (v17): the form validates on Continue, a
  // summary recap follows, and a final explicit question confirms
  // before the Machine really reactivates.
  const [stage, setStage] = useState<'form' | 'summary' | 'final'>('form');
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const events = useLifecycleEvents(machine.id);
  // The Display name is the field most likely to need attention (the
  // reused floor-position name may collide) — it takes initial focus.
  const nameInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameInputRef.current?.focus();
  }, []);

  const activeOnes = machines.filter(
    (m) => m.retiredOn === undefined && m.id !== machine.id,
  );
  // Hard blockers: identity conflicts that make a safe reactivation
  // impossible without fixing other records first. The Asset Tag check
  // also covers the barcode — the barcode IS the Asset Tag. The server
  // enforces the same blockers authoritatively.
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

  const selectedArea = areaById.get(areaId);
  const selectedAreaName = selectedArea?.name ?? '—';
  const nameCollision = activeOnes.some(
    (m) => m.areaId === areaId && m.name === name.trim(),
  );
  const moved = areaId !== machine.areaId;

  const continueToSummary = () => {
    // Collect every failing field at once — each message renders at
    // its own field (the live collision message already occupies the
    // name slot, so a collision blocks without a second message).
    const errors: { name?: string; reason?: string; physical?: string } = {};
    if (!name.trim()) errors.name = 'A display name is required.';
    if (!reason.trim()) {
      errors.reason =
        'A reason is required and will be saved in Machine history.';
    }
    if (!samePhysical) {
      errors.physical =
        'Confirm that this is the same physical machine. A different physical machine needs a new Machine record.';
    }
    setFieldErrors(errors);
    if (nameCollision || Object.keys(errors).length > 0) return;
    setServerError(null);
    setStage('summary');
  };

  const submit = async () => {
    setBusy(true);
    setServerError(null);
    try {
      await onConfirm({ name: name.trim(), areaId, reason: reason.trim() });
    } catch (error) {
      setServerError(errorMessage(error));
      setStage('summary');
    } finally {
      setBusy(false);
    }
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
            { label: 'Machine', value: name.trim(), emphasis: 'primary' },
            {
              label: 'Area',
              value: (
                <>
                  <AreaDot colorVar={areaColor(selectedArea)} size={10} />
                  {moved ? (
                    <>
                      {areaById.get(machine.areaId)?.name ?? '—'} →{' '}
                      {selectedAreaName}
                    </>
                  ) : (
                    selectedAreaName
                  )}
                </>
              ),
              // An Area move is a deviation worth noticing.
              tone: moved ? 'warn' : undefined,
            },
            {
              label: 'Asset Tag',
              value: <span className="mono">{machine.assetTag}</span>,
            },
            {
              label: 'Barcode',
              value: <BarcodeValue value={machineBarcode(machine.assetTag)} />,
              emphasis: 'secondary',
            },
            {
              label: 'Physical identity',
              value: 'Same physical machine confirmed',
              tone: 'ok',
            },
            { label: 'Reason', value: reason.trim() },
            {
              label: 'Returns as',
              value: 'Idle',
              emphasis: 'secondary',
              // The Idle state keeps its semantic warn text tone here
              // too — the same color it carries on every monitoring
              // surface (§4.10, §12.1).
              tone: 'warn',
            },
          ]}
        />
        <div className="mg-note">
          The same Machine returns to Active with its existing Asset Tag and
          barcode. Previous production history is preserved.
          {moved ? ' The Area change applies from reactivation onward.' : ''}
        </div>
        {serverError ? (
          <div className="err" role="alert">
            {serverError}
          </div>
        ) : null}
        <div className="row">
          <button className="bigbtn ghost" onClick={() => setStage('form')}>
            Back
          </button>
          <button
            className="bigbtn primary"
            disabled={busy}
            onClick={() => setStage('final')}
          >
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
            confirmDisabled={writeBlocked || busy}
            onCancel={() => setStage('summary')}
            onConfirm={() => void submit()}
          >
            <b>{machine.name}</b> will return to service. This reactivation is
            permanently recorded in Machine history. The previous retirement
            remains part of that history.
          </ConfirmDialog>
        ) : null}
      </ModalDialog>
    );
  }

  return (
    <ModalDialog label="Reactivate Machine" onClose={onCancel} size="wide">
      <h3>Reactivate Machine</h3>
      {/* The read-only identity header (§12.3) leads the dialog like
          New/Edit Machine: Asset Tag, derived barcode and the asset
          metadata (labelled `Machine`) share one identity row — all
          untouched by reactivation. */}
      <div className="mg-idhead">
        <div className="idcol">
          <span className="idlabel">Asset Tag</span>
          <span className="idvalue tag">{machine.assetTag}</span>
        </div>
        <div className="idcol grow">
          <span className="idlabel">Barcode</span>
          <span className="idvalue barcodeval">
            {machineBarcode(machine.assetTag)}
          </span>
        </div>
        <div className="idcol">
          <span className="idlabel">Machine</span>
          <span className="idassets">
            {machine.manufacturer || machine.model || machine.serialNumber ? (
              <>
                {machine.manufacturer ? (
                  <span className="mfr">{machine.manufacturer}</span>
                ) : null}
                {machine.manufacturer && machine.model ? ' ' : null}
                {machine.model ? (
                  <span className="mdl">{machine.model}</span>
                ) : null}
                {machine.serialNumber ? (
                  <>
                    {machine.manufacturer || machine.model ? ' · ' : null}
                    <span className="mono">S/N {machine.serialNumber}</span>
                  </>
                ) : null}
              </>
            ) : (
              '—'
            )}
          </span>
        </div>
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
            Resolve the conflict first. If this is a replacement Machine, create
            a new Machine instead.
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
                ref={nameInputRef}
                className="field"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Lathe 1"
              />
            </Field>
            {nameCollision ? (
              <div className="err" role="alert">
                ✕ “{name.trim()}” already exists in {selectedAreaName}.
              </div>
            ) : !name.trim() && fieldErrors.name ? (
              <div className="err" role="alert">
                {fieldErrors.name}
              </div>
            ) : name.trim() ? (
              <div className="mg-fieldok">
                ✓ “{name.trim()}” is available in {selectedAreaName}.
              </div>
            ) : null}
          </div>
          <div className="mg-areacell">
            <AreaSelectField
              label="Return Area"
              value={areaId}
              choices={areaChoices}
              onChange={setAreaId}
            />
            <p className="mg-areahelp">
              Change only if the physical Machine moved while retired. Previous
              production history keeps the original Area.
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
        {/* Fixed-height slot: the Reason error sits 4px under its
            input (same rhythm as the name/Area feedback) and the
            reserved height keeps the distance to the Important panel
            constant whether or not the error shows. */}
        <div className="mg-reasonslot">
          {fieldErrors.reason && !reason.trim() ? (
            <div className="err" role="alert">
              {fieldErrors.reason}
            </div>
          ) : null}
        </div>
        {/* The same-physical-machine acknowledgement sits on a calm
            warning panel — "be sure of this before continuing", never
            an error: only the Important marker carries the warning
            tone, the sentence keeps the normal text tone. Continuing
            without the check is what becomes a validation error,
            rendered right here at the checkbox. */}
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
          {fieldErrors.physical && !samePhysical ? (
            <div className="err" role="alert">
              {fieldErrors.physical}
            </div>
          ) : null}
        </div>
        <LifecycleTimeline state={events.state} areaById={areaById} />
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
