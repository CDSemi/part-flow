// Framework-independent Machine state derivation shared by every
// surface that presents Machines (Scan Station and Area Board
// monitoring cards, Management → Machines). Production-safe: no mock
// data, no framework imports — presentation derivation only.
//
// Lifecycle vs. operational state stay separate concepts:
// - lifecycle: active or retired (`retiredOn`) — retired Machines stay
//   visible in history and reporting but accept no new work;
// - operational state: maintenance is an explicit override, otherwise
//   `running` (assigned active quantity) or `idle` is DERIVED — users
//   never choose running/idle by hand.

import type {
  MachineStatus,
  MockAreaCard,
  MockAreaMachine,
  MockMachine,
} from './view-models';

/**
 * Effective operational state of an active Machine:
 * 1. an explicit maintenance override wins;
 * 2. otherwise assigned active quantity means `running`;
 * 3. otherwise the Machine is `idle`.
 */
export function effectiveMachineStatus(
  machine: Pick<MockMachine, 'maintenance'>,
  assignedQty: number,
): MachineStatus {
  if (machine.maintenance) return 'maintenance';
  return assignedQty > 0 ? 'running' : 'idle';
}

/**
 * Active quantity currently assigned to a Machine, derived from the
 * shared Area presence cards (queue/vendor portions and finished
 * quantity never count — only active Machine assignments).
 */
export function machineAssignedQty(
  cards: readonly MockAreaCard[],
  machine: Pick<MockMachine, 'area' | 'name'>,
): number {
  let total = 0;
  for (const card of cards) {
    if (card.area !== machine.area) continue;
    for (const [context, qty] of card.machines) {
      if (context === machine.name) total += qty;
    }
  }
  return total;
}

/** PN portions currently assigned to a Machine (name + quantity). */
export function machineAssignments(
  cards: readonly MockAreaCard[],
  machine: Pick<MockMachine, 'area' | 'name'>,
): { pn: string; qty: number }[] {
  const portions: { pn: string; qty: number }[] = [];
  for (const card of cards) {
    if (card.area !== machine.area) continue;
    for (const [context, qty] of card.machines) {
      if (context === machine.name) portions.push({ pn: card.pn, qty });
    }
  }
  return portions;
}

/**
 * Elapsed time in the current state, derived from the shared
 * `stateChangedAt` timestamp (never a stored formatted duration), in
 * the compact duration language used across the monitoring surfaces:
 * `18m`, `1h 24m`, `2d 03h`. Sub-minute ages render as `<1m`; a
 * timestamp in the future (clock skew) clamps to `<1m` instead of
 * producing a negative age.
 */
export function formatStateAge(
  stateChangedAt: string,
  now: number = Date.now(),
): string {
  const elapsedMs = now - new Date(stateChangedAt).getTime();
  const minutes = Math.floor(elapsedMs / 60_000);
  if (!Number.isFinite(minutes) || minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ${String(hours % 24).padStart(2, '0')}h`;
}

/** Operator-facing label of one operational state. */
export const MACHINE_STATE_LABEL: Record<MachineStatus, string> = {
  running: 'Running',
  idle: 'Idle',
  maintenance: 'Maintenance',
};

/**
 * Project one Machine record into the shared monitoring-card shape,
 * with the operational state derived from the maintenance override and
 * the assigned quantity.
 */
export function toAreaMachine(
  machine: MockMachine,
  assignedQty: number,
): MockAreaMachine {
  return {
    name: machine.name,
    status: effectiveMachineStatus(machine, assignedQty),
    stateChangedAt: machine.stateChangedAt,
    maintenanceNote: machine.maintenance?.note,
    expectedReturn: machine.maintenance?.expectedReturn,
  };
}
