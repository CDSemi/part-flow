// Unsaved work on the page (GUI_DESIGN §3 rule 13): a form on the page
// itself whose input is not saved yet reports it here, and an outdated
// page never reloads itself while any report is active. Forms inside a
// dialog need no report, because no page reloads itself while a dialog
// is open.

import { useEffect } from 'react';

const reports = new Set<symbol>();

/** Whether any part of the page holds unsaved work right now. */
export function hasUnsavedWork(): boolean {
  return reports.size > 0;
}

/** Report unsaved work for as long as `unsaved` is true and the calling
 * component is mounted. */
export function useUnsavedWork(unsaved: boolean): void {
  useEffect(() => {
    if (!unsaved) return undefined;
    const report = Symbol('unsaved work');
    reports.add(report);
    return () => {
      reports.delete(report);
    };
  }, [unsaved]);
}
