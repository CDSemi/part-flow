import { expect, test } from 'vitest';

import { ADMIN_GROUPS, ADMIN_SECTIONS } from './sections';

// The Administration sidebar registry against the approved target UI
// (GUI_DESIGN §9): every group lists its sections in exactly the
// documented order.

test('the sidebar groups and their sections follow the GUI_DESIGN §9 table', () => {
  expect(
    Object.fromEntries(
      ADMIN_GROUPS.map((group) => [
        group,
        ADMIN_SECTIONS.filter((section) => section.group === group).map(
          (section) => section.label,
        ),
      ]),
    ),
  ).toEqual({
    Organization: ['Departments', 'Areas', 'Operations', 'Workers'],
    'Production setup': [
      'Scan Stations',
      'Barcode configuration',
      'Scan behavior',
      'Department display settings',
    ],
    'Production rules': [
      'Worker sessions',
      'Machine assignment',
      'Correction permissions',
    ],
    Access: ['Users', 'Roles & permissions', 'Security'],
    'Data & system': ['Backups', 'History archival & purge', 'Settings'],
  });
  // Every section belongs to a listed group.
  expect(
    ADMIN_SECTIONS.every((section) => ADMIN_GROUPS.includes(section.group)),
  ).toBe(true);
});
