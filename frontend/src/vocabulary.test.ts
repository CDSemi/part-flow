import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

// Canonical vocabulary migration guard (PROJECT_PROFILE v7): the
// business container previously called Purchase Order is a Work Order.
// No current frontend source, style, script, or test may retain the
// obsolete Purchase Order vocabulary — in identifiers, routes, labels,
// or CSS class names.

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
