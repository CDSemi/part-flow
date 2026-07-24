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
    machineMode: 'None (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'cut',
    name: 'Cut',
    operations: 'Cutting',
    machineMode: 'Auto-assign single Machine',
    machines: 'Saw 1',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'lathe',
    name: 'Lathe',
    operations: 'Turning',
    machineMode: 'Queue → select by scan',
    machines: 'Lathe 1–4',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'mill',
    name: 'Mill',
    operations: 'Milling',
    machineMode: 'Queue → select by scan',
    machines: 'Mill 1–2',
    workerMode: 'Scanned session',
    active: true,
  },
  {
    areaKey: 'manual',
    name: 'Manual',
    operations: 'Manual work',
    machineMode: 'None (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'deburr',
    name: 'Deburr',
    operations: 'Deburring',
    machineMode: 'None (no Machines)',
    machines: '—',
    workerMode: 'Fixed Worker',
    active: true,
  },
  {
    areaKey: 'external',
    name: 'External',
    operations: 'Plating · Painting · Testing',
    machineMode: 'None (no Machines)',
    machines: '—',
    workerMode: 'Disabled',
    active: true,
  },
  {
    areaKey: 'stockroom',
    name: 'Stockroom',
    operations: 'Receiving',
    machineMode: 'None (no Machines)',
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
}

export const MOCK_ADMIN_SECTIONS: MockAdminSection[] = [
  {
    id: 'departments',
    group: 'Organization',
    label: 'Departments',
    subtitle: 'Organizational production units',
  },
  {
    id: 'areas',
    group: 'Organization',
    label: 'Areas',
    subtitle: 'Physical production locations — Machine Shop',
  },
  {
    id: 'operations',
    group: 'Organization',
    label: 'Operations',
    subtitle: 'Work performed within an Area (§8.5)',
  },
  {
    id: 'machines',
    group: 'Organization',
    label: 'Machines',
    subtitle: 'Machines available per Area',
  },
  {
    id: 'workers',
    group: 'Organization',
    label: 'Workers',
    subtitle: 'Workers and badge barcodes',
  },
  {
    id: 'route-templates',
    group: 'Production setup',
    label: 'Route Templates',
    subtitle: 'Reusable production routes',
  },
  {
    id: 'barcode-configuration',
    group: 'Production setup',
    label: 'Barcode configuration',
    subtitle: 'PF: prefix scheme and label printing',
  },
  {
    id: 'scan-behavior',
    group: 'Production setup',
    label: 'Scan behavior',
    subtitle: 'Station scan-resolution policies',
  },
  {
    id: 'users',
    group: 'Access',
    label: 'Users',
    subtitle: 'Application user accounts',
  },
  {
    id: 'roles',
    group: 'Access',
    label: 'Roles & permissions',
    subtitle: 'Role-based access (Phase 14)',
  },
  {
    id: 'worker-sessions',
    group: 'Policies',
    label: 'Worker sessions',
    subtitle: 'Worker session lifetime policies (§19)',
  },
  {
    id: 'machine-assignment',
    group: 'Policies',
    label: 'Machine assignment',
    subtitle: 'Area machine-assignment modes (§12)',
  },
  {
    id: 'correction-permissions',
    group: 'Policies',
    label: 'Correction permissions',
    subtitle: 'Who may correct, with reasons (§16)',
  },
  {
    id: 'settings',
    group: 'Policies',
    label: 'Settings',
    subtitle: 'General application settings',
  },
];
