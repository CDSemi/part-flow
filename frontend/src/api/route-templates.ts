// Read-only RouteTemplate listing (Phase 4 — GUI_DESIGN §11.4).
//
// The release flow offers an existing **active** RouteTemplate when
// the user confirms `PLANNED`; this module reads exactly that minimal
// selection data — the active templates with their ordered steps (the
// first step is the PLANNED release's fixed starting Area/Operation).
// Planned Routes management (GUI_DESIGN §13) is a later phase: nothing
// here can create, change, or archive a template.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

export interface RouteTemplateStep {
  id: number;
  sequence: number;
  areaId: number;
  operationId: number | null;
  instructions: string | null;
}

export interface RouteTemplate {
  id: number;
  name: string;
  description: string | null;
  /** Steps in sequence order; the first step starts a PLANNED flow. */
  steps: RouteTemplateStep[];
}

interface RouteStepWire {
  id: number;
  sequence: number;
  area_id: number;
  operation_id: number | null;
  instructions: string | null;
}

interface RouteTemplateWire {
  id: number;
  name: string;
  description: string | null;
  steps: RouteStepWire[];
}

function toTemplate(wire: RouteTemplateWire): RouteTemplate {
  return {
    id: wire.id,
    name: wire.name,
    description: wire.description,
    steps: wire.steps.map((step) => ({
      id: step.id,
      sequence: step.sequence,
      areaId: step.area_id,
      operationId: step.operation_id,
      instructions: step.instructions,
    })),
  };
}

/** The active RouteTemplates with ordered steps (read-only). */
export async function listRouteTemplates(): Promise<RouteTemplate[]> {
  const wires = await apiRequest<RouteTemplateWire[]>('/api/route-templates');
  return wires.map(toTemplate);
}
