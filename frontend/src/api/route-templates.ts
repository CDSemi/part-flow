// RouteTemplate (Planned Route) API — Phase 4 release selection plus
// Phase 13 Planned Routes management (GUI_DESIGN §11.4, §13).
//
// `listRouteTemplates` keeps the Phase 4 contract: the **active**
// templates with their ordered steps, offered by the release flow when
// the user confirms `PLANNED` (the first step is the PLANNED release's
// fixed starting Area/Operation). Management → Planned Routes reads the
// separate management listing (active and archived templates with
// their usage) and creates, replaces (full step set), archives
// (ever-used only) and deletes (never-used only) templates. Editing a
// template changes future assignments only — released Quantity Flows
// keep their own Assigned Route snapshot, which nothing here touches.
// Validation and transactions live in the backend; this module only
// maps the wire format.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

export interface RouteTemplateStep {
  id: number;
  sequence: number;
  areaId: number;
  /** Required on every save; null only on a legacy step. */
  operationId: number | null;
  /** Advisory estimated time — ISO 8601 (`PT4H`) as delivered. */
  expectedDuration: string | null;
  /** Advisory preferred Machine by its stable id (never the name). */
  preferredMachineId: number | null;
  instructions: string | null;
}

export interface RouteTemplate {
  id: number;
  name: string;
  description: string | null;
  /** Steps in sequence order; the first step starts a PLANNED flow. */
  steps: RouteTemplateStep[];
}

/** One template as Management → Planned Routes lists it. */
export interface RouteTemplateRecord extends RouteTemplate {
  archivedAt: string | null;
  /** Site date of the archiving (`YYYY-MM-DD`); null while active. */
  archivedOn: string | null;
  /** Site date of the last change (`YYYY-MM-DD`). */
  updatedOn: string;
  /** Whether any Assigned Route was ever copied from the template —
   * an ever-used template archives, a never-used one deletes. */
  everUsed: boolean;
  /** Released Quantity Flows assigned this template. */
  usageCount: number;
}

/** One requested step; request order is the route order (the server
 * assigns the sequences). */
export interface RouteStepInput {
  areaId: number;
  operationId: number | null;
  expectedDuration: string | null;
  preferredMachineId: number | null;
  instructions: string | null;
}

export interface RouteTemplateInput {
  name: string;
  description: string | null;
  steps: RouteStepInput[];
}

export interface RouteTemplateUsageFlow {
  quantityFlowId: number;
  partNumber: string;
  /** Site date of the release (`YYYY-MM-DD`). */
  releasedOn: string;
}

export interface RouteTemplateUsage {
  /** Every released Quantity Flow assigned the template. */
  total: number;
  /** The most recent ones, newest first (bounded by the server). */
  flows: RouteTemplateUsageFlow[];
}

interface RouteStepWire {
  id: number;
  sequence: number;
  area_id: number;
  operation_id: number | null;
  expected_duration?: string | null;
  preferred_machine_id?: number | null;
  instructions: string | null;
}

interface RouteTemplateWire {
  id: number;
  name: string;
  description: string | null;
  steps: RouteStepWire[];
}

interface RouteTemplateManagementWire extends RouteTemplateWire {
  archived_at: string | null;
  archived_on: string | null;
  updated_on: string;
  ever_used: boolean;
  usage_count: number;
}

interface RouteTemplateUsageWire {
  template_id: number;
  total: number;
  flows: {
    quantity_flow_id: number;
    part_number: string;
    released_on: string;
  }[];
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
      expectedDuration: step.expected_duration ?? null,
      preferredMachineId: step.preferred_machine_id ?? null,
      instructions: step.instructions,
    })),
  };
}

function toRecord(wire: RouteTemplateManagementWire): RouteTemplateRecord {
  return {
    ...toTemplate(wire),
    archivedAt: wire.archived_at,
    archivedOn: wire.archived_on,
    updatedOn: wire.updated_on,
    everUsed: wire.ever_used,
    usageCount: wire.usage_count,
  };
}

function toWriteBody(input: RouteTemplateInput): Record<string, unknown> {
  return {
    name: input.name,
    description: input.description,
    steps: input.steps.map((step) => ({
      area_id: step.areaId,
      operation_id: step.operationId,
      expected_duration: step.expectedDuration,
      preferred_machine_id: step.preferredMachineId,
      instructions: step.instructions,
    })),
  };
}

/** The active RouteTemplates with ordered steps (release selection). */
export async function listRouteTemplates(): Promise<RouteTemplate[]> {
  const wires = await apiRequest<RouteTemplateWire[]>('/api/route-templates');
  return wires.map(toTemplate);
}

/** Every template, active first, with its usage (management listing). */
export async function listRouteTemplateRecords(): Promise<
  RouteTemplateRecord[]
> {
  const wires = await apiRequest<RouteTemplateManagementWire[]>(
    '/api/route-templates/management',
  );
  return wires.map(toRecord);
}

/** Create a template (not retry-safe — a retried create can add a
 * second never-used template). */
export async function createRouteTemplate(
  input: RouteTemplateInput,
): Promise<RouteTemplateRecord> {
  const wire = await apiRequest<RouteTemplateManagementWire>(
    '/api/route-templates',
    { method: 'POST', body: toWriteBody(input) },
  );
  return toRecord(wire);
}

/** Replace a template's name, description and complete step set (an
 * identical replacement is a server-side no-op). */
export async function replaceRouteTemplate(
  id: number,
  input: RouteTemplateInput,
): Promise<RouteTemplateRecord> {
  const wire = await apiRequest<RouteTemplateManagementWire>(
    `/api/route-templates/${id}`,
    { method: 'PUT', body: toWriteBody(input) },
  );
  return toRecord(wire);
}

/** Archive an ever-used template (idempotent: archiving an archived
 * template answers it unchanged). */
export async function archiveRouteTemplate(
  id: number,
): Promise<RouteTemplateRecord> {
  const wire = await apiRequest<RouteTemplateManagementWire>(
    `/api/route-templates/${id}/archive`,
    { method: 'POST' },
  );
  return toRecord(wire);
}

/** Delete a never-used template (204; a 404 means it is already gone). */
export async function deleteRouteTemplate(id: number): Promise<void> {
  await apiRequest<void>(`/api/route-templates/${id}`, { method: 'DELETE' });
}

/** The released Quantity Flows assigned the template, newest first. */
export async function getRouteTemplateUsage(
  id: number,
): Promise<RouteTemplateUsage> {
  const wire = await apiRequest<RouteTemplateUsageWire>(
    `/api/route-templates/${id}/usage`,
  );
  return {
    total: wire.total,
    flows: wire.flows.map((flow) => ({
      quantityFlowId: flow.quantity_flow_id,
      partNumber: flow.part_number,
      releasedOn: flow.released_on,
    })),
  };
}
