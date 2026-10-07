import { expect, test } from 'vitest';

import { ADMIN_SECTIONS } from './sections';

// The Administration sidebar registry against the approved target UI
// (GUI_DESIGN §9): the Policies group lists its sections in exactly the
// documented order.

test('the Policies sections follow the GUI_DESIGN §9 order', () => {
  expect(
    ADMIN_SECTIONS.filter((section) => section.group === 'Policies').map(
      (section) => section.label,
    ),
  ).toEqual([
    'Worker sessions',
    'Machine assignment',
    'Correction permissions',
    'History archival & purge',
    'Department display settings',
    'Settings',
  ]);
});
