import { expect, test } from 'vitest';

import { createDraftLine, lineRemoveRule } from './demand-lines';

// The demand-line removal rule is the presentation mirror of the
// backend refusals (PROJECT_PROFILE §13, Phase 12 Hot guard): the
// server stays the authority and answers 409 removing nothing.

test('an unsaved draft line is removed directly; a saved line asks first', () => {
  expect(lineRemoveRule(createDraftLine({ due: '' }))).toBe('draft');
  expect(
    lineRemoveRule(createDraftLine({ due: '', saved: true, demandId: 1 })),
  ).toBe('confirm');
});

test('a released line never offers removal', () => {
  expect(
    lineRemoveRule(
      createDraftLine({ due: '', saved: true, demandId: 1, released: true }),
    ),
  ).toBe('blocked');
});

test('a Hot line offers no removal until it leaves the Hot list', () => {
  expect(
    lineRemoveRule(
      createDraftLine({ due: '', saved: true, demandId: 1, hotRank: 3 }),
    ),
  ).toBe('hot');
});

test('released takes precedence over Hot — the permanent reason is shown', () => {
  expect(
    lineRemoveRule(
      createDraftLine({
        due: '',
        saved: true,
        demandId: 1,
        released: true,
        hotRank: 1,
      }),
    ),
  ).toBe('blocked');
});
