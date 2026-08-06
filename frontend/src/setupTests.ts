import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Vitest runs without injected globals (vite.config.ts does not set
// `test.globals`), so testing-library's automatic DOM cleanup never
// registers itself. Register it explicitly: without this, rendered
// containers leak from one test into the next inside a file and
// document-scoped `screen` queries start matching stale duplicates.
afterEach(() => {
  cleanup();
});
