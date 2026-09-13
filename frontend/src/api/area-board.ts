// Area Board read model API (Phase 11 — GUI_DESIGN §6;
// PROJECT_PROFILE §21).
//
// One read: `GET /api/area-board` returns every active Area of the
// Department with the SHARED Area monitoring model (./area-inventory —
// the same contract the Scan Station reads, the PN's open demand
// context and the scrapped quantity per PN included), its active
// Operations, and, for the terminal Stockroom, the stocked lines with
// the PN's active allocation and its OPEN demand context.
//
// The All Areas overview and the per-Area detail are two presentations
// of this one answer: switching tabs never re-reads, and the two modes
// can never show different quantities. The server sends no derived time
// value — dwell times and due countdowns derive at render from the
// fixed timestamps and dates plus the shared UI clock (§3.12).
//
// Production-safe: no mock data, no framework imports.

import type {
  AreaInventory,
  AreaInventoryWire,
  DemandContext,
  DemandContextWire,
  OperationRef,
} from './area-inventory';
import {
  toAreaInventory,
  toDemandContext,
  toOperationRef,
} from './area-inventory';
import { apiRequest } from './client';

/** One PN held in a terminal Area (the Stockroom column / group). */
export interface StockedLine {
  partNumber: string;
  /** Gross stocked quantity of the PN in THIS Area. */
  quantity: number;
  /** The PN's ACTIVE allocation — a PN-level connection to demand,
   * never divided per Area, so the row states both values. */
  allocatedQuantity: number;
  /** The PN's OPEN demands in the canonical order — the same
   * monitoring context an inventory row carries, so a stocked PN still
   * worked FOR an open demand names it (empty once every Work Order of
   * the PN is complete). */
  demands: DemandContext[];
}

export interface AreaBoardArea {
  /** The shared Area monitoring model, identical to the station's. */
  inventory: AreaInventory;
  operations: OperationRef[];
  /** Terminal Areas only; empty everywhere else. */
  stocked: StockedLine[];
}

export interface AreaBoard {
  department: { id: number; name: string };
  areas: AreaBoardArea[];
}

interface AreaBoardAreaWire {
  inventory: AreaInventoryWire;
  operations: {
    id: number;
    code: string;
    name: string | null;
    is_external: boolean;
  }[];
  stocked: {
    part_number: string;
    quantity: number;
    allocated_quantity: number;
    demands: DemandContextWire[];
  }[];
}

interface AreaBoardWire {
  department: { id: number; name: string };
  areas: AreaBoardAreaWire[];
}

function toArea(wire: AreaBoardAreaWire): AreaBoardArea {
  return {
    inventory: toAreaInventory(wire.inventory),
    operations: wire.operations.map(toOperationRef),
    stocked: wire.stocked.map((line) => ({
      partNumber: line.part_number,
      quantity: line.quantity,
      allocatedQuantity: line.allocated_quantity,
      demands: line.demands.map(toDemandContext),
    })),
  };
}

/**
 * Load the board. `departmentId` selects the Department; omitted, the
 * server resolves the single active Department and refuses an ambiguous
 * configuration (several active Departments) with an explicit message
 * naming them.
 */
export async function loadAreaBoard(
  departmentId: number | null,
): Promise<AreaBoard> {
  const query =
    departmentId !== null
      ? `?department_id=${encodeURIComponent(departmentId)}`
      : '';
  const wire = await apiRequest<AreaBoardWire>(`/api/area-board${query}`);
  return {
    department: wire.department,
    areas: wire.areas.map(toArea),
  };
}
