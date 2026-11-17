// Print barcodes (GUI_DESIGN §12.6, §14.5): the framework-independent
// selection model of the ONE shared multi-label dialog
// (`PrintBarcodesDialog`). A selection is a set of card keys; one key
// is ONE label however many groups show it (a PN on several running
// Work Orders is selected once and prints once).
//
// Production-safe: no mock data, no framework state.

import type { ReactNode } from 'react';

/** One group of cards (an Area, a running Work Order) or the flat list. */
export interface BarcodeSection {
  key: string;
  /** Visible group heading; absent for the one flat list. */
  title?: ReactNode;
  /** Plain-text group name for the group checkbox's accessible name. */
  titleText?: string;
  /** The cards of the group in display order. */
  itemKeys: readonly string[];
}

/** A checkbox state; `mixed` = some but not all of its cards. */
export type CheckState = 'on' | 'off' | 'mixed';

/** The check state of a set of cards under the selection. */
export function checkState(
  selected: ReadonlySet<string>,
  keys: readonly string[],
): CheckState {
  const count = keys.filter((key) => selected.has(key)).length;
  if (count === 0) return 'off';
  return count === keys.length ? 'on' : 'mixed';
}

/** The selection with one card toggled. */
export function toggleKey(
  selected: ReadonlySet<string>,
  key: string,
): Set<string> {
  const next = new Set(selected);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/**
 * The selection after a group (or Select all) checkbox: a fully
 * selected set is cleared, anything else (off or mixed) selects every
 * card of the set. Cards outside the set keep their state.
 */
export function toggleKeys(
  selected: ReadonlySet<string>,
  keys: readonly string[],
): Set<string> {
  const next = new Set(selected);
  if (checkState(selected, keys) === 'on') {
    for (const key of keys) next.delete(key);
  } else {
    for (const key of keys) next.add(key);
  }
  return next;
}

/** Every card key shown, once each, in display order. */
export function shownKeys(sections: readonly BarcodeSection[]): string[] {
  return [...new Set(sections.flatMap((section) => section.itemKeys))];
}

/**
 * The labels to print, once each: the selected cards in the order they
 * are shown, then selected cards not shown right now (a narrower search)
 * in the order they were selected.
 */
export function printOrder(
  selected: ReadonlySet<string>,
  shown: readonly string[],
): string[] {
  const first = shown.filter((key) => selected.has(key));
  const listed = new Set(first);
  return [...first, ...[...selected].filter((key) => !listed.has(key))];
}

/** `1 label selected` / `3 labels selected`. */
export function selectedCountText(count: number): string {
  return `${count} ${count === 1 ? 'label' : 'labels'} selected`;
}
