// Production mock-boundary verification (runs as part of `npm run build`).
//
// The Phase 2 mock views and datasets live behind the development-only
// registry in src/app/dev-views.ts. This check fails the build when any
// known mock sentinel value appears in the generated production assets,
// so development mock data can never silently leak into a production
// bundle. src/production-boundary.test.ts keeps this sentinel list in
// sync with the mock datasets.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

// Unique records from the development mock datasets in src/mocks/.
// Each value must keep existing in the mock sources (verified by
// src/production-boundary.test.ts) so this check cannot rot.
export const MOCK_SENTINELS = [
    '0455-20-0118-03', // mocks/work-orders.ts (and others)
    '2027-60-8114-00', // mocks/scan-station.ts, tracking, …
    '0118-40-0022-07', // long-data sets
    '007010', // mocks/work-orders.ts (mock Work Order Number)
    'TMP-20260721-0940-REWORK', // mocks/work-orders.ts
    'LATHE-ST-01', // mocks/scan-station.ts
    'QF-0161', // mocks/work-orders.ts (released-line status)
    'PF:PN:1014', // mock PN barcode catalog
];

const distDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');

function walk(dir) {
    const files = [];
    for (const entry of readdirSync(dir)) {
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) files.push(...walk(path));
        else files.push(path);
    }
    return files;
}

let files;
try {
    files = walk(distDir);
} catch {
    console.error(
        `check-production-boundary: no build output at ${distDir} — run vite build first.`,
    );
    process.exit(1);
}

const TEXT_EXTENSIONS = /\.(js|mjs|css|html|json|txt|svg|map)$/;
const leaks = [];
for (const file of files) {
    if (!TEXT_EXTENSIONS.test(file)) continue;
    const content = readFileSync(file, 'utf8');
    for (const sentinel of MOCK_SENTINELS) {
        if (content.includes(sentinel)) {
            leaks.push({ file: relative(distDir, file), sentinel });
        }
    }
}

if (leaks.length > 0) {
    console.error(
        'check-production-boundary: FAILED — development mock data leaked into the production build:',
    );
    for (const leak of leaks) {
        console.error(`  ${leak.file}: contains "${leak.sentinel}"`);
    }
    process.exit(1);
}

console.log(
    `check-production-boundary: OK — ${files.length} production asset(s) contain none of the ${MOCK_SENTINELS.length} mock sentinels.`,
);
