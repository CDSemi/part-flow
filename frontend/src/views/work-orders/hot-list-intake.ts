// Add to Hot list at Work Order creation (Phase 17; PROJECT_PROFILE §21
// Priority Management, GUI_DESIGN §11.3). The lines of a just-saved Work
// Order join the bottom of the Hot list in line order through the
// Priority view's own command — one ADD per line, each confirmed against
// the order the previous one committed — so the rank is written in one
// place only and every change is audited like any other.

import { errorMessage } from '../../api/client';
import {
  applyHotListChange,
  getHotList,
  hotListChanged,
} from '../../api/hot-list';
import { newDeviceEventId } from '../../api/production-release';
import { writeOutcomeUnknown } from '../../api/scan-station';

export interface HotListIntakeOutcome {
  /** The lines that joined the Hot list, in line order. */
  added: number;
  /** The reason the remaining lines did not join; null when all did. */
  error: string | null;
}

const UNANSWERED =
  'The server did not answer while a line was being added, so it may or may not have joined.';

/**
 * Add the demand lines `demandIds` (in line order) at the bottom of the
 * Hot list. A list changed elsewhere meanwhile is re-read once, from the
 * refusal that reports it; any other failure stops, and the lines not
 * added stay off the Hot list — nothing is retried blindly.
 */
export async function addLinesToHotList(
  demandIds: readonly number[],
): Promise<HotListIntakeOutcome> {
  let order: number[];
  try {
    order = (await getHotList()).entries.map(
      (entry) => entry.workOrderDemandId,
    );
  } catch (error) {
    // Only a read failed: no line was added.
    return { added: 0, error: errorMessage(error) };
  }
  let added = 0;
  for (const demandId of demandIds) {
    try {
      order = await addAtTheBottom(order, demandId);
    } catch (error) {
      return {
        added,
        error: writeOutcomeUnknown(error) ? UNANSWERED : errorMessage(error),
      };
    }
    added += 1;
  }
  return { added, error: null };
}

/** One ADD; returns the order it committed. */
async function addAtTheBottom(
  order: readonly number[],
  demandId: number,
): Promise<number[]> {
  const add = (expected: readonly number[]) =>
    applyHotListChange({
      deviceEventId: newDeviceEventId(),
      action: 'ADD',
      expectedOrder: [...expected],
      newOrder: [...expected, demandId],
    });
  let result;
  try {
    result = await add(order);
  } catch (error) {
    const current = hotListChanged(error);
    if (current === null) throw error;
    result = await add(current.map((entry) => entry.workOrderDemandId));
  }
  return result.entries === null
    ? [...order, demandId]
    : result.entries.map((entry) => entry.workOrderDemandId);
}

/** The sentence the saved-Work-Order notice ends with. */
export function hotListIntakeNotice(
  outcome: HotListIntakeOutcome,
  lineCount: number,
): string {
  if (outcome.error === null) {
    return lineCount === 1
      ? '🔥 Its line joined the bottom of the Hot list.'
      : `🔥 Its ${lineCount} lines joined the bottom of the Hot list.`;
  }
  return `⚠ ${outcome.added} of ${lineCount} line${lineCount === 1 ? '' : 's'} joined the Hot list: ${outcome.error} Add the rest in Priority.`;
}
