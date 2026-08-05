export interface MockAdminAreaRow {
  areaKey: string;
  name: string;
  operations: string;
  machineMode: string;
  machines: string;
  workerMode: string;
  terminal?: boolean;
  active: boolean;
}

export const MOCK_ADMIN_AREAS: MockAdminAreaRow[] = [
  {
    areaKey: 'material',
    name: 'Material',
    operations: 'Receiving',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'cut',
    name: 'Cut',
    operations: 'Cutting',
    // One Machine behaves exactly like several: queue → one-shot
    // assignment. Behavior never differs by Machine count.
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Saw 1',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'lathe',
    name: 'Lathe',
    operations: 'Turning',
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Lathe 1–4',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'mill',
    name: 'Mill',
    operations: 'Milling',
    machineMode: 'Queue → assign (one-shot)',
    machines: 'Mill 1–2',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'manual',
    name: 'Manual',
    operations: 'Manual work',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'deburr',
    name: 'Deburr',
    operations: 'Deburring',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'external',
    name: 'External',
    operations: 'Plating · Painting · Testing',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'stockroom',
    name: 'Stockroom',
    operations: 'Receiving',
    machineMode: 'Direct processing (no Machines)',
    machines: '—',
    workerMode: 'Scanned session',
    terminal: true,
    active: true,
  },
];

export interface MockAdminSection {
  id: string;
  group: 'Organization' | 'Production setup' | 'Access' | 'Policies';
  label: string;
  subtitle: string;
  /**
   * When this configuration becomes real (IMPLEMENTATION_ROADMAP):
   * `minimum` — part of the Minimum Environment Setup prerequisite
   * configured before the real production workflows run; `full` —
   * part of the later full Administration phase.
   */
  phase: 'minimum' | 'full';
}

export const MOCK_ADMIN_SECTIONS: MockAdminSection[] = [
  {
    id: 'departments',
    phase: 'minimum',
    group: 'Organization',
    label: 'Departments',
    subtitle: 'Organizational production units',
  },
  {
    id: 'areas',
    phase: 'minimum',
    group: 'Organization',
    label: 'Areas',
    subtitle: 'Physical production locations — Machine Shop',
  },
  {
    id: 'operations',
    phase: 'minimum',
    group: 'Organization',
    label: 'Operations',
    subtitle: 'Work performed within an Area (§8.5)',
  },
  // Machines and Planned Routes (Route Templates) are production
  // master data owned by authorized production roles — they live in
  // Management → Machines / Planned Routes, never as a duplicate
  // Administration screen. Administration stays focused on system
  // administration (users, roles, Scan Stations, policies).
  {
    id: 'workers',
    phase: 'full',
    group: 'Organization',
    label: 'Workers',
    subtitle: 'Workers and badge barcodes',
  },
  {
    id: 'part-numbers',
    phase: 'full',
    group: 'Organization',
    label: 'PartNumbers',
    subtitle:
      'PN master records — archive (soft-delete) junk/test PNs; history keeps the original PN text with an (archived) marker; physical purge is a separate explicit maintenance operation',
  },
  {
    id: 'scan-stations',
    phase: 'minimum',
    group: 'Production setup',
    label: 'Scan Stations',
    subtitle: 'Stations bound to one Area — Station ID and active status',
  },
  {
    id: 'barcode-configuration',
    phase: 'minimum',
    group: 'Production setup',
    label: 'Barcode configuration',
    subtitle: 'PF: prefix scheme and label printing',
  },
  {
    id: 'scan-behavior',
    phase: 'full',
    group: 'Production setup',
    label: 'Scan behavior',
    subtitle: 'Station scan-resolution policies',
  },
  {
    id: 'users',
    phase: 'full',
    group: 'Access',
    label: 'Users',
    subtitle: 'Application user accounts',
  },
  {
    id: 'roles',
    phase: 'full',
    group: 'Access',
    label: 'Roles & permissions',
    subtitle: 'Role-based access (Phase 14)',
  },
  {
    id: 'worker-sessions',
    phase: 'full',
    group: 'Policies',
    label: 'Worker sessions',
    subtitle: 'Worker session lifetime policies (§19)',
  },
  {
    id: 'machine-assignment',
    phase: 'full',
    group: 'Policies',
    label: 'Machine assignment',
    subtitle:
      'Two Area modes only: no Machines → direct processing; Machines → queue and one-shot assign (never inferred from Machine count)',
  },
  {
    id: 'correction-permissions',
    phase: 'full',
    group: 'Policies',
    label: 'Correction permissions',
    subtitle: 'Who may correct, with reasons (§16)',
  },
  {
    id: 'data-retention',
    phase: 'full',
    group: 'Policies',
    label: 'History archival & purge',
    subtitle:
      'Admin-only maintenance: retention policy, size threshold, manual archive/purge with scope preview, reason and full audit — normal workflows never delete history',
  },
  {
    id: 'settings',
    phase: 'full',
    group: 'Policies',
    label: 'Settings',
    subtitle: 'General application settings',
  },
];
