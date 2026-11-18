import './label-print.css';

import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * The ONE print sheet of barcode labels (GUI_DESIGN §12.3, §12.6, §14):
 * a direct child of <body>, hidden on screen, present only while the
 * dialog that renders it is open. While it exists, printing shows only
 * its labels — left to right, row after row, page after page, never
 * splitting a label — so one label (the Machine and Part Number barcode
 * label dialogs) and many labels (Print barcodes) print the same way.
 * The print styles leave every other part of the page out of the
 * layout, including what a browser extension adds next to <body>, so a
 * print never gains a blank page.
 */
export function LabelPrintSheet({
  labels,
}: {
  /** The printed labels in print order, each with a stable key. */
  labels: readonly { key: string; label: ReactNode }[];
}) {
  return createPortal(
    <div className="pbx-sheet" aria-hidden="true">
      {labels.map(({ key, label }) => (
        <div key={key} className="pbx-plabel">
          {label}
        </div>
      ))}
    </div>,
    document.body,
  );
}
