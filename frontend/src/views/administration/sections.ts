// Administration sidebar registry (GUI_DESIGN §9): the grouped section
// list with each section's implementation phase. Production-safe —
// static navigation configuration, no sample data.

export const ADMIN_GROUPS = [
  'Organization',
  'Production setup',
  'Access',
  'Policies',
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
   * `full` — part of the later full Administration phase (Phase 13).
   */
  phase: 'minimum' | 'full';
}

export const ADMIN_SECTIONS: AdminSection[] = [
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
    subtitle: 'Physical production locations',
  },
  {
    id: 'operations',
    phase: 'minimum',
    group: 'Organization',
    label: 'Operations',
    subtitle: 'Work performed within an Area (§8.5)',
  },
  // Machines, Planned Routes (Route Templates) and PartNumber master
  // metadata are production master data owned by authorized production
  // roles — they live in Management → Machines / Planned Routes / Part
  // Numbers, never as a duplicate Administration screen. Administration
  // stays focused on system administration (users, roles, Scan
  // Stations, policies).
  {
    id: 'workers',
    phase: 'full',
    group: 'Organization',
    label: 'Workers',
    subtitle:
      'Worker profiles — name, badge barcode (existing employee badge), avatar, active status; separate from Users (§8.13)',
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
    subtitle: 'PF: prefix scheme, Machine Asset Tag format, label printing',
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
    subtitle:
      'Scanned-session sliding inactivity timeout and badge confirmation of sensitive actions — DONE, QUEUE and UNDO (§19)',
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
    id: 'department-display',
    phase: 'full',
    group: 'Policies',
    label: 'Department display settings',
    subtitle:
      'Per-Department display configuration — Production Board rotation timing: seconds per displayed row and minimum page dwell (§21)',
  },
  {
    id: 'data-retention',
    phase: 'full',
    group: 'Policies',
    label: 'History archival & purge',
    subtitle:
      'Admin-only Movement-history retention maintenance: configurable retention period, size threshold or manual request — lossless archive export, verification, then purge exactly the archived rows, with scope preview, reason and full audit; normal workflows never delete history',
  },
  {
    id: 'settings',
    phase: 'full',
    group: 'Policies',
    label: 'Settings',
    subtitle: 'General application settings',
  },
];
