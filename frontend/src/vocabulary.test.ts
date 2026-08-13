import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

// Canonical vocabulary guards:
// 1. PROJECT_PROFILE v7 — the business container previously called
//    Purchase Order is a Work Order; the obsolete vocabulary may not
//    return in identifiers, routes, labels, or CSS class names.
// 2. PROJECT_PROFILE v9 — REWORK is no longer a Request Type (Repair is
//    a movement intent, not demand), temporary Work Order Numbers
//    (TMP-…) are never generated (a blank number is NULL and displays
//    as —), Action barcodes were removed, and there is no persistent
//    Machine Session and no Recent Scans list. (Deliberate negative
//    mentions of the Machine Session stay allowed — the UI explains
//    that none exists.)
// 3. PROJECT_PROFILE v16 — the canonical PN string (uppercase,
//    whitespace-free) is the stable domain identity: no surrogate
//    part_number_id linkage, no pnKey comparison helper, and no
//    preserved-first-entered-casing rule may return.

const srcDir = dirname(fileURLToPath(import.meta.url));
const roots = [srcDir, join(srcDir, '..', 'scripts')];

const STALE_PATTERNS: RegExp[] = [
  /PurchaseOrder/,
  /Purchase Order/i,
  /PoDemand/,
  /\bPO Demand\b/,
  /PoAllocation/,
  /\bPO Allocation\b/,
  /purchase_order/,
  /po_number/,
  /po_demand/,
  /po_allocation/,
  /purchase-orders/,
  /\bMockPo\b/,
  /\bNewPoDialog\b/,
  /\bPoDetailPanel\b/,
  /\bPO-\d/, // demo-style PO numbers (Work Order Numbers are numeric-looking)
  /\bNew PO\b/,
  /\bPO Number\b/,
  /\bPO Intake\b/,
  // Scan-workflow redesign (PROJECT_PROFILE v9):
  /\bREWORK\b/,
  /PF:ACTION/,
  /\bTMP-\d/,
  /TMP-YYYYMMDD/,
  /generateTemporaryWorkOrderNumber/,
  /Recent scans/i,
  // PN-string identity (PROJECT_PROFILE v16): no surrogate PN identity
  // and no casing-preservation rule may return.
  /part_number_id/,
  /partNumberId/,
  /\bpnKey\b/,
  /first-entered casing/i,
  /casing is preserved/i,
  /casing of first creation/i,
];

function walk(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else if (/\.(ts|tsx|css|mjs|html)$/.test(entry.name)) files.push(path);
  }
  return files;
}

test('no current frontend source retains obsolete Purchase Order vocabulary', () => {
  const offenders: string[] = [];
  for (const root of roots) {
    for (const file of walk(root)) {
      if (file === fileURLToPath(import.meta.url)) continue; // this guard
      const content = readFileSync(file, 'utf8');
      for (const pattern of STALE_PATTERNS) {
        if (pattern.test(content)) {
          offenders.push(`${relative(srcDir, file)}: ${pattern}`);
        }
      }
    }
  }
  expect(offenders, offenders.join('\n')).toEqual([]);
});
