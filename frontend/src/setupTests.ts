import '@testing-library/jest-dom/vitest';

import { configure } from '@testing-library/dom';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// The default 1 s findBy*/waitFor timeout is too tight for the first
// render of a lazily imported view in slow environments — inside the
// Docker dev container the bind-mounted sources make the initial
// transform of a view chunk take multiple seconds, and the first test
// of a suite then fails while the loading skeleton is still up. No
// test relies on these utilities timing out, so a generous ceiling
// only affects genuinely broken tests (they fail slower).
configure({ asyncUtilTimeout: 5000 });

// Vitest runs without injected globals (vite.config.ts does not set
// `test.globals`), so testing-library's automatic DOM cleanup never
// registers itself. Register it explicitly: without this, rendered
// containers leak from one test into the next inside a file and
// document-scoped `screen` queries start matching stale duplicates.
afterEach(() => {
  cleanup();
});
