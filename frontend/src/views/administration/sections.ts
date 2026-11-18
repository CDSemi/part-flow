// Administration sidebar registry (GUI_DESIGN §9): the grouped section
// list with each section's implementation phase and the permission its
// changes need. Production-safe — static navigation configuration, no
// sample data.

import type { Permission } from '../../api/roles';

export const ADMIN_GROUPS = [
  'Organization',
  'Production setup',
  'Production rules',
  'Access',
  'Data & system',
] as const;

export interface AdminSection {
  id: string;
  group: (typeof ADMIN_GROUPS)[number];
  label: string;
  subtitle: string;
  /**
   * When this configuration becomes real (IMPLEMENTATION_ROADMAP):
   * `minimum` — part of the Minimum Environment Setup prerequisite
   * (Phase 3.5), configured before the real production workflows run;
   * `full` — part of the later full Administration phase (Phase 13);
   * `deferred` — no working setting is defined yet
   * (IMPLEMENTATION_ROADMAP `Deferred`); presented as not available —
   * at most as disabled previews (Phase 17) — with no promised phase.
   */
  phase: 'minimum' | 'full' | 'deferred';
  /**
   * The permission that changing this section's configuration needs
   * (the server checks it). Without it the section is view-only. Absent
   * for a section with nothing to change (a statement, or no content).
   */
  writePermission?: Permission;
}

export const ADMIN_SECTIONS: AdminSection[] = [
  {
    id: 'departments',
    writePermission: 'MANAGE_DEPARTMENTS',
    phase: 'minimum',
    group: 'Organization',
    label: 'Departments',
    subtitle: 'Organizational production units',
  },
  {
    id: 'areas',
    writePermission: 'MANAGE_AREAS',
    phase: 'minimum',
    group: 'Organization',
    label: 'Areas',
    subtitle: 'Physical production locations',
  },
  {
    id: 'operations',
    writePermission: 'MANAGE_OPERATIONS',
    phase: 'minimum',
    group: 'Organization',
    label: 'Operations',
    subtitle: 'Work performed within an Area',
  },
  // Machines, Planned Routes (Route Templates) and PartNumber master
  // metadata are production master data owned by authorized production
  // roles — they live in Management → Machines / Planned Routes / Part
  // Numbers, never as a duplicate Administration screen. Administration
  // stays focused on system administration (users, roles, Scan
  // Stations, policies).
  {
    id: 'workers',
    writePermission: 'MANAGE_WORKERS',
    phase: 'full',
    group: 'Organization',
    label: 'Workers',
    subtitle:
      'The people who scan their badge at the Scan Stations; separate from Users',
  },
  {
    id: 'scan-stations',
    writePermission: 'MANAGE_SCAN_STATIONS',
    phase: 'minimum',
    group: 'Production setup',
    label: 'Scan Stations',
    subtitle: 'Stations bound to one Area — Station ID and active status',
  },
  {
    id: 'barcode-configuration',
    writePermission: 'MANAGE_BARCODE_CONFIGURATION',
    phase: 'minimum',
    group: 'Production setup',
    label: 'Barcode configuration',
    subtitle: 'PF: prefix scheme, Machine Asset Tag format, label printing',
  },
  {
    id: 'scan-behavior',
    phase: 'deferred',
    group: 'Production setup',
    label: 'Scan behavior',
    subtitle: 'Station scan-resolution policies',
  },
  {
    id: 'department-display',
    writePermission: 'MANAGE_DEPARTMENTS',
    phase: 'full',
    group: 'Production setup',
    label: 'Department display settings',
    subtitle: "How each Department's Production Board pages rotate",
  },
  {
    id: 'worker-sessions',
    writePermission: 'MANAGE_WORKER_SESSION_POLICIES',
    phase: 'full',
    group: 'Production rules',
    label: 'Worker sessions',
    subtitle:
      'When a scanned Worker Session ends, and which actions need a badge scan',
  },
  {
    id: 'machine-assignment',
    phase: 'full',
    group: 'Production rules',
    label: 'Machine assignment',
    subtitle: 'How an Area works with its Machines',
  },
  {
    id: 'correction-permissions',
    writePermission: 'MANAGE_CORRECTION_PERMISSIONS',
    phase: 'full',
    group: 'Production rules',
    label: 'Correction permissions',
    subtitle: 'Who may undo and correct, and whether an Undo needs a reason',
  },
  {
    id: 'users',
    writePermission: 'MANAGE_USERS_AND_ROLES',
    phase: 'full',
    group: 'Access',
    label: 'Users',
    subtitle:
      'Application accounts — name, login name, role, avatar, active status; separate from Workers',
  },
  {
    id: 'roles',
    writePermission: 'MANAGE_USERS_AND_ROLES',
    phase: 'full',
    group: 'Access',
    label: 'Roles & permissions',
    subtitle: 'Named roles and the permissions each one grants',
  },
  {
    id: 'security',
    writePermission: 'CONFIGURE_SYSTEM_SETTINGS',
    phase: 'full',
    group: 'Access',
    label: 'Security',
    subtitle: 'How users sign in and how their accounts stay protected',
  },
  {
    id: 'backups',
    writePermission: 'CONFIGURE_SYSTEM_SETTINGS',
    phase: 'full',
    group: 'Data & system',
    label: 'Backups',
    subtitle:
      'How often the server takes a scheduled backup, and how many hourly, daily and weekly backups it keeps',
  },
  {
    id: 'data-retention',
    writePermission: 'CONFIGURE_SYSTEM_SETTINGS',
    phase: 'full',
    group: 'Data & system',
    label: 'History archival & purge',
    subtitle:
      'Admin-only Movement-history retention maintenance: configurable retention period, size threshold or manual request — lossless archive export, verification, then purge exactly the archived rows, with scope preview, reason and full audit; normal workflows never delete history',
  },
  {
    id: 'settings',
    writePermission: 'CONFIGURE_SYSTEM_SETTINGS',
    phase: 'full',
    group: 'Data & system',
    label: 'Settings',
    subtitle: 'General application settings',
  },
];
