import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';

import { PERMISSIONS } from '../../api/roles';
import type { Permission } from '../../api/roles';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { ConnectivityContext } from '../../app/connectivity-context';
import { prepareImageUpload } from '../../components/image-upload';
import { PartNumbersView } from './PartNumbersView';

// Management → Part Numbers (GUI_DESIGN §14): a REAL view since Phase
// 13, exercised here against an in-memory fake of the `/api/part-numbers`
// surface with the same routes, bodies and status codes as the backend
// contract. The canonical PN string is the identity — saved details are
// optional metadata only, the barcode is always the derived
// `PF:PN:<part-number>`, the default image is the ONE shared PN
// placeholder, and deletion removes nothing but the saved details.

// Image preparation (sniff, decode, downscale) has its own suite; here
// it passes the chosen file through.
vi.mock('../../components/image-upload', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../components/image-upload')>();
  return { ...actual, prepareImageUpload: vi.fn(async (file: File) => file) };
});

interface FakeRecord {
  part_number: string;
  barcode_value: string;
  name: string | null;
  current_revision: string | null;
  erp_id: string | null;
  image_updated_at: string | null;
}

type RouteKey =
  | 'GET page'
  | 'GET number'
  | 'POST'
  | 'PATCH'
  | 'DELETE'
  | 'PUT image'
  | 'DELETE image';

type Failure = { status: number; detail: string } | 'network';

interface Write {
  method: string;
  url: string;
  body?: Record<string, unknown>;
  contentType?: string;
}

const T0 = '2026-10-01T08:00:00.000000+00:00';
const IMAGE_AT = '2026-10-02T09:30:00.000000+00:00';

let records: FakeRecord[];
let calls: string[];
let writes: Write[];
let failures: Partial<Record<RouteKey, Failure>>;
let holds: Partial<Record<RouteKey, Promise<void>>>;
let imageVersion: number;

function record(pn: string, extra: Partial<FakeRecord> = {}): FakeRecord {
  return {
    part_number: pn,
    barcode_value: `PF:PN:${pn}`,
    name: null,
    current_revision: null,
    erp_id: null,
    image_updated_at: null,
    ...extra,
  };
}

function seedRecords(): FakeRecord[] {
  return [
    record('118-052', { name: 'SPACER, 0.25 THK', current_revision: 'A' }),
    record('142-260', {
      name: 'PLATE, BASE',
      current_revision: 'A',
      erp_id: 'ERP-PN-142',
    }),
    record('2027-60-8114-00', {
      name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
      current_revision: 'C',
      erp_id: 'ERP-PN-40412',
      image_updated_at: IMAGE_AT,
    }),
    record('214-406'),
  ];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function wire(r: FakeRecord) {
  return { ...r, created_at: T0, updated_at: T0 };
}

const canonical = (value: string) => value.trim().toUpperCase();

function routeKey(method: string, path: string): RouteKey | null {
  if (path === '/api/part-numbers/page' && method === 'GET') return 'GET page';
  if (path === '/api/part-numbers' && method === 'GET') return 'GET number';
  if (path === '/api/part-numbers' && method === 'POST') return 'POST';
  if (path === '/api/part-numbers' && method === 'PATCH') return 'PATCH';
  if (path === '/api/part-numbers' && method === 'DELETE') return 'DELETE';
  if (path === '/api/part-numbers/image' && method === 'PUT') {
    return 'PUT image';
  }
  if (path === '/api/part-numbers/image' && method === 'DELETE') {
    return 'DELETE image';
  }
  return null;
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  calls.push(`${method} ${url}`);
  if (method !== 'GET') {
    writes.push({
      method,
      url,
      body:
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined,
      contentType: (init?.headers as Record<string, string> | undefined)?.[
        'Content-Type'
      ],
    });
  }
  const key = routeKey(method, path);
  if (key === null) {
    return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
  }
  const hold = holds[key];
  if (hold) {
    delete holds[key];
    await hold;
  }
  const failure = failures[key];
  if (failure) {
    delete failures[key];
    if (failure === 'network') throw new TypeError('Failed to fetch');
    return json({ detail: failure.detail }, failure.status);
  }

  const number = params.get('number');
  const found =
    number !== null
      ? records.find((r) => r.part_number === canonical(number))
      : undefined;
  const missing = () =>
    json(
      {
        detail: `Part Number ${canonical(number ?? '')} has no saved details.`,
      },
      404,
    );

  switch (key) {
    case 'GET page': {
      const term = (params.get('search') ?? '').trim().toLowerCase();
      const limit = Number(params.get('limit'));
      const matching = records
        .filter(
          (r) =>
            !term ||
            [r.part_number, r.name, r.current_revision, r.erp_id].some((v) =>
              v?.toLowerCase().includes(term),
            ),
        )
        .sort((a, b) => a.part_number.localeCompare(b.part_number));
      const rows = matching.slice(0, limit);
      return json({
        rows: rows.map(wire),
        total: matching.length,
        offset: 0,
        limit,
        has_more: rows.length < matching.length,
      });
    }
    case 'GET number':
      return json(found ? [wire(found)] : []);
    case 'POST': {
      const body = JSON.parse(String(init?.body)) as Record<string, string>;
      const pn = canonical(body.part_number);
      if (records.some((r) => r.part_number === pn)) {
        return json(
          { detail: `Part Number “${pn}” already has saved details.` },
          409,
        );
      }
      const created = record(pn, {
        name: body.name ?? null,
        current_revision: body.current_revision ?? null,
        erp_id: body.erp_id ?? null,
      });
      records.push(created);
      return json(wire(created), 201);
    }
    case 'PATCH': {
      if (!found) return missing();
      const body = JSON.parse(String(init?.body)) as Partial<FakeRecord>;
      Object.assign(found, body);
      return json(wire(found));
    }
    case 'DELETE':
      if (!found) return missing();
      records = records.filter((r) => r !== found);
      return new Response(null, { status: 204 });
    case 'PUT image':
      if (!found) return missing();
      imageVersion += 1;
      found.image_updated_at = `2026-10-05T10:00:00.00000${imageVersion}+00:00`;
      return json(wire(found));
    case 'DELETE image':
      if (!found) return missing();
      found.image_updated_at = null;
      return json(wire(found));
  }
}

/** A promise the test resolves to release a held request. */
function deferred(): { promise: Promise<void>; release: () => void } {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/part-numbers');
  session = signedInSession();
  records = seedRecords();
  calls = [];
  writes = [];
  failures = {};
  holds = {};
  imageVersion = 0;
  // jsdom has no object URLs; the staged image preview needs one.
  URL.createObjectURL = vi.fn(() => 'blob:staged-pn-image');
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/**
 * The signed-in user of a test (Phase 14 slice 3): a Management view
 * offers its changes only to a user holding their permissions, so a
 * test holds every permission unless it signs in another user.
 */
function signedInSession(
  permissions: readonly Permission[] = PERMISSIONS,
): SessionValue {
  const user = {
    id: 90,
    loginName: 'mia',
    displayName: 'Mia Manager',
    roleId: 2,
    roleName: 'Manager',
    avatarUpdatedAt: null,
    permissions: [...permissions],
    mustChangePassword: false,
    sessionExpiresAt: null,
  };
  return {
    status: 'signed-in',
    user,
    setupOpen: false,
    checking: false,
    endedBy: null,
    can: (permission) => hasPermission(user, permission),
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
}

let session: SessionValue = signedInSession();

function SignedIn({ children }: { children: ReactNode }) {
  return (
    <SessionContext.Provider value={session}>
      {children}
    </SessionContext.Provider>
  );
}

function view(status: 'connected' | 'unavailable') {
  return (
    <SignedIn>
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PartNumbersView />
      </ConnectivityContext.Provider>
    </SignedIn>
  );
}

/** Render Part Numbers with a fixed connectivity status and wait for
 * the first page. */
async function renderPartNumbers(
  status: 'connected' | 'unavailable' = 'connected',
) {
  const rendered = render(view(status));
  await screen.findByRole('heading', { name: 'Part Numbers' });
  return rendered;
}

/** The list row whose PN cell names the record. */
function row(pn: string): HTMLElement {
  return screen.getByRole('button', { name: `Edit ${pn}` }).closest('tr')!;
}

/** Open Edit Part Number through the whole-row click and wait for the
 * loaded details. */
async function openEdit(pn: string): Promise<HTMLElement> {
  fireEvent.click(row(pn));
  const dialog = screen.getByRole('dialog', { name: 'Edit Part Number' });
  await within(dialog).findByLabelText(/Name \/ Description/);
  return dialog;
}

function openNew(): HTMLElement {
  fireEvent.click(screen.getByRole('button', { name: '+ New Part Number' }));
  return screen.getByRole('dialog', { name: 'New Part Number' });
}

const pageReads = () =>
  calls.filter((c) => c.startsWith('GET /api/part-numbers/page')).length;
const writeSummary = () => writes.map((w) => `${w.method} ${w.url}`);

function pngFile(): File {
  return new File(
    [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2])],
    'part.png',
    { type: 'image/png' },
  );
}

async function chooseImage(dialog: HTMLElement, label = 'Upload image') {
  const file = pngFile();
  fireEvent.change(within(dialog).getByLabelText(label), {
    target: { files: [file] },
  });
  await waitFor(() =>
    expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
      'src',
      'blob:staged-pn-image',
    ),
  );
  expect(prepareImageUpload).toHaveBeenCalledWith(file);
  return file;
}

/* ============ List ============ */

test('the list shows saved details with canonical PN, derived barcode and the image', async () => {
  await renderPartNumbers();

  expect(calls).toContain('GET /api/part-numbers/page?search=&limit=100');
  const headers = Array.from(
    document.querySelectorAll('.pnm-table thead th'),
  ).map((th) => th.textContent);
  expect(headers).toEqual([
    'Image',
    'Part Number',
    'Name / Description',
    'Revision',
    'ERP ID',
    'Barcode',
  ]);

  const bracket = row('2027-60-8114-00');
  expect(bracket.textContent).toContain(
    'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
  );
  expect(bracket.textContent).toContain('ERP-PN-40412');
  expect(bracket.querySelector('.barcodeval')?.textContent).toBe(
    'PF:PN:2027-60-8114-00',
  );
  // A saved image renders from the server URL, versioned for caching.
  expect(
    within(bracket).getByAltText('Part image — 2027-60-8114-00'),
  ).toHaveAttribute(
    'src',
    `/api/part-numbers/image?number=2027-60-8114-00&v=${encodeURIComponent(IMAGE_AT)}`,
  );

  // Absent optional details render `—` and the ONE shared placeholder.
  const spacer = row('214-406');
  expect(within(spacer).getAllByText('—')).toHaveLength(3);
  expect(spacer.querySelector('img')).toBeNull();
  expect(spacer.querySelector('.pn-img')).not.toBeNull();

  // A real view: no development notice.
  expect(document.querySelector('.dev-notice')).toBeNull();
  expect(screen.getByRole('status').textContent).toBe(
    'Showing 4 of 4 Part Numbers',
  );
});

test('search reaches the server debounced over PN, name, revision and ERP id', async () => {
  await renderPartNumbers();

  fireEvent.change(screen.getByLabelText('Search Part Numbers'), {
    target: { value: 'bracket' },
  });
  // Debounced: nothing is requested on the keystroke itself.
  expect(calls.some((c) => c.includes('search=bracket'))).toBe(false);
  await waitFor(() =>
    expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(1),
  );
  expect(calls).toContain(
    'GET /api/part-numbers/page?search=bracket&limit=100',
  );
  expect(row('2027-60-8114-00')).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Search Part Numbers'), {
    target: { value: 'erp-pn-142' },
  });
  await waitFor(() => expect(row('142-260')).toBeInTheDocument());
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(1);

  fireEvent.change(screen.getByLabelText('Search Part Numbers'), {
    target: { value: 'no-such-part' },
  });
  expect(
    await screen.findByText(
      'No saved Part Number details match “no-such-part”.',
    ),
  ).toBeInTheDocument();
});

test('an empty list states that no details exist yet', async () => {
  records = [];
  await renderPartNumbers();
  expect(
    screen.getByText('No Part Number details have been added yet.'),
  ).toBeInTheDocument();
});

test('the bounded list pages with Show more up to 200 rows', async () => {
  records = Array.from({ length: 205 }, (_, i) =>
    record(`P-${String(i).padStart(3, '0')}`),
  );
  await renderPartNumbers();

  const status = () => screen.getByRole('status').textContent;
  expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(100);
  expect(status()).toContain('Showing 100 of 205 Part Numbers');

  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  await waitFor(() =>
    expect(document.querySelectorAll('.pnm-table tbody tr')).toHaveLength(200),
  );
  expect(calls).toContain('GET /api/part-numbers/page?search=&limit=200');
  expect(status()).toContain('Showing 200 of 205 Part Numbers');
  expect(status()).toContain(
    'Only the first 200 are listed — narrow the search to find the rest.',
  );
  expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
});

test('a failed list read shows the error with Retry', async () => {
  failures['GET page'] = { status: 503, detail: 'Database unavailable.' };
  render(view('connected'));

  expect(
    await screen.findByText('Part Number data could not be loaded.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Database unavailable.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('button', { name: 'Edit 214-406' }),
  ).toBeInTheDocument();
});

/* ============ Edit dialog ============ */

test('the row and its Edit button open Edit with the read-only identity header while loading', async () => {
  await renderPartNumbers();
  const gate = deferred();
  holds['GET number'] = gate.promise;

  fireEvent.click(screen.getByRole('button', { name: 'Edit 2027-60-8114-00' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Part Number' });
  expect(calls).toContain('GET /api/part-numbers?number=2027-60-8114-00');

  // The identity header renders at once from the PN; the form waits.
  const identity = dialog.querySelector('.pnm-idhead') as HTMLElement;
  expect(identity.textContent).toContain('2027-60-8114-00');
  expect(identity.textContent).toContain('PF:PN:2027-60-8114-00');
  expect(
    within(dialog).getByRole('status', { name: 'Loading Part Number details' }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();

  gate.release();
  expect(
    await within(dialog).findByLabelText(/Name \/ Description/),
  ).toHaveValue('BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125');
  expect(within(dialog).getByLabelText(/Revision/)).toHaveValue('C');
  expect(within(dialog).getByLabelText(/ERP ID/)).toHaveValue('ERP-PN-40412');
  // The PN is never an input on an existing record.
  expect(within(dialog).queryByLabelText('Part Number')).toBeNull();
  expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
    'src',
    `/api/part-numbers/image?number=2027-60-8114-00&v=${encodeURIComponent(IMAGE_AT)}`,
  );

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);
});

test('a load failure inside the dialog offers Retry while the barcode label stays available', async () => {
  await renderPartNumbers();
  failures['GET number'] = { status: 503, detail: 'Database unavailable.' };

  fireEvent.click(row('118-052'));
  const dialog = screen.getByRole('dialog', { name: 'Edit Part Number' });
  expect(
    await within(dialog).findByText('Part Number details could not be loaded.'),
  ).toBeInTheDocument();
  expect(within(dialog).getByText('Database unavailable.')).toBeInTheDocument();

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = screen.getByRole('dialog', {
    name: 'Part Number barcode label',
  });
  // The ONE shared PN label: Code 128 bars, the PN beneath, the value.
  const svg = label.querySelector('svg.lbarcode') as SVGElement;
  expect(svg.getAttribute('aria-label')).toBe('Barcode PF:PN:118-052');
  expect(svg.querySelectorAll('rect').length).toBeGreaterThan(20);
  expect(label.querySelector('.lpn')?.textContent).toBe('118-052');
  expect(label.querySelector('.lvalue')?.textContent).toBe('PF:PN:118-052');
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));

  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
  expect(
    await within(dialog).findByLabelText(/Name \/ Description/),
  ).toHaveValue('SPACER, 0.25 THK');
});

test('an Edit save PATCHes only the changed fields and reloads the list after close', async () => {
  await renderPartNumbers();
  const readsBefore = pageReads();

  const dialog = await openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: ' B ' },
  });
  // A cleared field is sent as null.
  fireEvent.change(within(dialog).getByLabelText(/ERP ID/), {
    target: { value: '  ' },
  });
  expect(within(dialog).getByText('● Unsaved changes')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: '/api/part-numbers?number=142-260',
      body: { current_revision: 'B', erp_id: null },
      contentType: 'application/json',
    },
  ]);
  await waitFor(() => expect(row('142-260').textContent).toContain('B'));
  expect(pageReads()).toBe(readsBefore + 1);
});

test('an image-only save sends an empty PATCH, then the image with its type', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('214-406');
  await chooseImage(dialog);
  expect(writes).toEqual([]);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'PATCH /api/part-numbers?number=214-406',
    'PUT /api/part-numbers/image?number=214-406',
  ]);
  expect(writes[0].body).toEqual({});
  expect(writes[1].contentType).toBe('image/png');
  await waitFor(() =>
    expect(
      within(row('214-406')).getByAltText('Part image — 214-406'),
    ).toHaveAttribute(
      'src',
      `/api/part-numbers/image?number=214-406&v=${encodeURIComponent('2026-10-05T10:00:00.000001+00:00')}`,
    ),
  );
});

test('removing the image sends DELETE image and restores the placeholder', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('2027-60-8114-00');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove image' }));
  expect(dialog.querySelector('img.pn-img')).toBeNull();
  expect(within(dialog).getByText('● Unsaved changes')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'PATCH /api/part-numbers?number=2027-60-8114-00',
    'DELETE /api/part-numbers/image?number=2027-60-8114-00',
  ]);
  await waitFor(() =>
    expect(row('2027-60-8114-00').querySelector('img')).toBeNull(),
  );
});

test('an image failure states whether the details were saved', async () => {
  await renderPartNumbers();

  // Details changed, image refused.
  let dialog = await openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  await chooseImage(dialog);
  failures['PUT image'] = { status: 415, detail: 'Unsupported image type.' };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(
    await within(dialog).findByText(
      'The Part Number details were saved, but the image could not be updated: Unsupported image type.',
    ),
  ).toBeInTheDocument();
  // Still open on the saved record, the staged image kept.
  expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
    'src',
    'blob:staged-pn-image',
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  // Nothing but the image changed: the unchanged `{}` PATCH saved
  // nothing, so the copy does not claim it did.
  dialog = await openEdit('118-052');
  await chooseImage(dialog);
  failures['PUT image'] = { status: 413, detail: 'The image is too large.' };
  writes = [];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(
    await within(dialog).findByText(
      'The image could not be updated: The image is too large.',
    ),
  ).toBeInTheDocument();
  expect(writes[0].body).toEqual({});
});

test('a save answered 404 turns the dialog into New with the PN fixed, input kept', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  await chooseImage(dialog);
  // Deleted elsewhere meanwhile.
  records = records.filter((r) => r.part_number !== '142-260');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  expect(
    await within(dialog).findByText(
      'Part Number 142-260 has no saved details.',
    ),
  ).toBeInTheDocument();
  expect(dialog).toHaveAccessibleName('New Part Number');
  expect(within(dialog).getByRole('heading', { level: 3 })).toHaveTextContent(
    'New Part Number',
  );
  expect(dialog.querySelector('.pnm-idhead')?.textContent).toContain('142-260');
  expect(dialog.querySelector('.pnm-dangerzone')).toBeNull();
  expect(within(dialog).getByLabelText(/Revision/)).toHaveValue('B');
  expect(within(dialog).getByLabelText(/Name \/ Description/)).toHaveValue(
    'PLATE, BASE',
  );
  expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
    'src',
    'blob:staged-pn-image',
  );
  expect(within(dialog).getByText('● Unsaved changes')).toBeInTheDocument();

  // Add Part Number re-creates the record, then uploads the image.
  writes = [];
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'POST /api/part-numbers',
    'PUT /api/part-numbers/image?number=142-260',
  ]);
  expect(writes[0].body).toEqual({
    part_number: '142-260',
    name: 'PLATE, BASE',
    current_revision: 'B',
    erp_id: 'ERP-PN-142',
  });
});

test('no answer shows the unknown-outcome copy; Cancel and Escape are ignored while saving', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  const gate = deferred();
  holds.PATCH = gate.promise;
  failures.PATCH = 'network';
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  // In flight: neither Cancel nor Escape closes the dialog.
  expect(
    within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.getByRole('dialog', { name: 'Edit Part Number' })).toBe(dialog);
  expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull();

  gate.release();
  expect(
    await within(dialog).findByText(
      'The server did not answer — this change may or may not have been saved. Close this window to refresh, then check the Part Number before trying again.',
    ),
  ).toBeInTheDocument();
});

/* ============ New Part Number ============ */

test('a new Part Number shows whitespace, required and canonical feedback, and POSTs the trimmed entry', async () => {
  await renderPartNumbers();
  const readsBefore = pageReads();

  const dialog = openNew();
  const pnField = within(dialog).getByLabelText('Part Number');
  expect(pnField).toHaveFocus();
  // No identity header before a PN exists.
  expect(dialog.querySelector('.pnm-idhead')).toBeNull();

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  expect(dialog.textContent).toContain('A Part Number is required.');
  expect(writes).toEqual([]);

  fireEvent.change(pnField, { target: { value: 'ABC 123' } });
  expect(dialog.textContent).toContain(
    'Part Number cannot contain spaces or other whitespace.',
  );

  fireEvent.change(pnField, { target: { value: ' ab-1 ' } });
  expect(dialog.textContent).toContain('✓ Will be saved as AB-1');
  expect(dialog.textContent).toContain('Barcode PF:PN:AB-1');
  fireEvent.change(within(dialog).getByLabelText(/Name \/ Description/), {
    target: { value: '  SAMPLE, TEST PART ' },
  });
  // The debounced duplicate lookup runs before the save.
  await waitFor(() =>
    expect(calls).toContain('GET /api/part-numbers?number=AB-1'),
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/part-numbers',
      body: {
        part_number: 'ab-1',
        name: 'SAMPLE, TEST PART',
        current_revision: null,
        erp_id: null,
      },
      contentType: 'application/json',
    },
  ]);
  await waitFor(() => expect(row('AB-1')).toBeInTheDocument());
  expect(pageReads()).toBe(readsBefore + 1);
});

test('a duplicate found by the debounced lookup disables Add Part Number; a 409 shows the same copy', async () => {
  await renderPartNumbers();

  const dialog = openNew();
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: '142-260' },
  });
  expect(
    await within(dialog).findByText(
      'Part Number “142-260” already has saved details.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  ).toBeDisabled();

  // Created elsewhere after the lookup: the server answers E1.
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: 'race-1' },
  });
  await waitFor(() =>
    expect(calls).toContain('GET /api/part-numbers?number=RACE-1'),
  );
  records.push(record('RACE-1'));
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  expect(
    await within(dialog).findByText(
      'Part Number “RACE-1” already has saved details.',
    ),
  ).toBeInTheDocument();
  // E1 reloads the record: the dialog becomes Edit of RACE-1.
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Save changes' }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole('dialog', { name: 'Edit Part Number' })).toBe(dialog);
});

test('a typed-PN create answered 409 after an unknown outcome reloads into Edit, keeping only the entered values and the staged image', async () => {
  await renderPartNumbers();

  const dialog = openNew();
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: ' ab-9 ' },
  });
  fireEvent.change(within(dialog).getByLabelText(/Name \/ Description/), {
    target: { value: 'PLATE' },
  });
  await chooseImage(dialog);
  await waitFor(() =>
    expect(calls).toContain('GET /api/part-numbers?number=AB-9'),
  );

  // The first create gets no answer…
  failures.POST = 'network';
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  expect(
    await within(dialog).findByText(/The server did not answer/),
  ).toBeInTheDocument();
  // …and someone else saves details for the same PN meanwhile.
  records.push(
    record('AB-9', { name: 'FIRST', current_revision: 'C', erp_id: 'ERP-9' }),
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );

  expect(
    await within(dialog).findByText(
      'Part Number “AB-9” already has saved details.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Save changes' }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole('dialog', { name: 'Edit Part Number' })).toBe(dialog);
  expect(within(dialog).queryByLabelText('Part Number')).toBeNull();
  // The entered name stays as an edit; the fields left blank show the
  // saved values instead of clearing them.
  expect(within(dialog).getByLabelText(/Name \/ Description/)).toHaveValue(
    'PLATE',
  );
  expect(within(dialog).getByLabelText(/Revision/)).toHaveValue('C');
  expect(within(dialog).getByLabelText(/ERP ID/)).toHaveValue('ERP-9');
  expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
    'src',
    'blob:staged-pn-image',
  );
  expect(within(dialog).getByText('● Unsaved changes')).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'POST /api/part-numbers',
    'POST /api/part-numbers',
    'PATCH /api/part-numbers?number=AB-9',
    'PUT /api/part-numbers/image?number=AB-9',
  ]);
  expect(writes[2].body).toEqual({ name: 'PLATE' });
  const saved = records.find((r) => r.part_number === 'AB-9');
  expect(saved).toMatchObject({
    name: 'PLATE',
    current_revision: 'C',
    erp_id: 'ERP-9',
  });
  expect(saved?.image_updated_at).not.toBeNull();
});

test('a new Part Number with an image creates the record, then uploads to the canonical PN', async () => {
  await renderPartNumbers();

  const dialog = openNew();
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: 'img-7' },
  });
  await chooseImage(dialog);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Part Number' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'POST /api/part-numbers',
    'PUT /api/part-numbers/image?number=IMG-7',
  ]);
});

/* ============ Delete ============ */

test('deleting removes only the saved details after an explicit confirmation', async () => {
  await renderPartNumbers();

  let dialog = await openEdit('214-406');
  const zone = dialog.querySelector('.pnm-dangerzone') as HTMLElement;
  expect(zone.textContent).toContain('Delete Part Number Details');
  expect(zone.textContent).toContain(
    'This removes the saved image, description, revision, and ERP ID for 214-406. Production tracking and Work Order history are not affected.',
  );
  fireEvent.click(
    within(zone).getByRole('button', { name: 'Delete details…' }),
  );
  let confirm = screen.getByRole('dialog', {
    name: 'Delete Part Number details?',
  });
  expect(confirm.className).toContain('tone-danger');
  expect(confirm.textContent).toContain(
    'This permanently removes the saved image, description, revision, and ERP ID for 214-406. The Part Number and its production history remain available.',
  );
  // Cancel changes nothing.
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(writes).toEqual([]);

  // With unsaved edits the confirmation says they are discarded too.
  dialog = await openEdit('214-406');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'Z' },
  });
  fireEvent.click(
    within(dialog.querySelector('.pnm-dangerzone') as HTMLElement).getByRole(
      'button',
      { name: 'Delete details…' },
    ),
  );
  confirm = screen.getByRole('dialog', { name: 'Delete Part Number details?' });
  expect(confirm.textContent).toContain(
    'for 214-406 (unsaved edits are discarded with it). The Part Number',
  );
  const readsBefore = pageReads();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete details' }),
  );

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual(['DELETE /api/part-numbers?number=214-406']);
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'Edit 214-406' })).toBeNull(),
  );
  expect(pageReads()).toBe(readsBefore + 1);
  expect(row('2027-60-8114-00')).toBeInTheDocument();
});

test('a delete answered 404 is treated as already gone', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('118-052');
  records = records.filter((r) => r.part_number !== '118-052');
  fireEvent.click(
    within(dialog.querySelector('.pnm-dangerzone') as HTMLElement).getByRole(
      'button',
      { name: 'Delete details…' },
    ),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Delete Part Number details?' }),
    ).getByRole('button', { name: 'Delete details' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'Edit 118-052' })).toBeNull(),
  );
});

/* ============ Unsaved input ============ */

test('closing a dirty Edit asks first; Save changes saves from the choice', async () => {
  await renderPartNumbers();

  const dialog = await openEdit('142-260');
  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(choice.textContent).toContain('You have unsaved changes to 142-260.');
  fireEvent.click(within(choice).getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[0].body).toEqual({ current_revision: 'B' });
});

test('discarding a new Part Number asks before dropping entered input', async () => {
  await renderPartNumbers();

  const dialog = openNew();
  fireEvent.change(within(dialog).getByLabelText('Part Number'), {
    target: { value: 'new-part-01' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const confirm = screen.getByRole('dialog', {
    name: 'Discard new Part Number?',
  });
  expect(confirm.textContent).toContain(
    'Your entered information will not be saved.',
  );
  expect(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Discard input' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);
});

/* ============ Offline write-block ============ */

test('offline disables the writes; reading, staging an image and the label stay available', async () => {
  await renderPartNumbers('unavailable');

  expect(
    screen.getByRole('button', { name: '+ New Part Number' }),
  ).toBeDisabled();
  const dialog = await openEdit('214-406');
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  expect(
    within(dialog.querySelector('.pnm-dangerzone') as HTMLElement).getByRole(
      'button',
      { name: 'Delete details…' },
    ),
  ).toBeDisabled();
  await chooseImage(dialog);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Part Number barcode label' }),
  ).toBeInTheDocument();
  expect(writes).toEqual([]);
});

test('offline mid-flow disables the delete confirmation and the unsaved Save, keeping Discard', async () => {
  const { rerender } = await renderPartNumbers('connected');

  const dialog = await openEdit('142-260');
  fireEvent.click(
    within(dialog.querySelector('.pnm-dangerzone') as HTMLElement).getByRole(
      'button',
      { name: 'Delete details…' },
    ),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Delete Part Number details?',
  });
  expect(
    within(confirm).getByRole('button', { name: 'Delete details' }),
  ).toBeEnabled();
  rerender(view('unavailable'));
  expect(
    within(confirm).getByRole('button', { name: 'Delete details' }),
  ).toBeDisabled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );

  fireEvent.change(within(dialog).getByLabelText(/Revision/), {
    target: { value: 'B' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  fireEvent.click(
    within(choice).getByRole('button', { name: 'Discard changes' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);

  rerender(view('connected'));
  expect(
    screen.getByRole('button', { name: '+ New Part Number' }),
  ).toBeEnabled();
});

/* ============ ?state=long ============ */

test('?state=long adds long-PN/name/metadata records to the server rows', async () => {
  window.history.replaceState({}, '', '/management/part-numbers?state=long');
  await renderPartNumbers();

  expect(row('2027-60-8114-00')).toBeTruthy();
  const supplemental = row(
    '0118-40-0022-07-0455-88-REV-C-SUPPLEMENTAL-LONG-PREVIEW',
  );
  expect(supplemental.textContent).toContain(
    'SUPPLEMENTAL LONG-PREVIEW PART NUMBER',
  );
  expect(supplemental.textContent).toContain('REV-SUPPLEMENTAL-LONG');
  expect(document.body.textContent).toContain('0114-60-0101-00');
});

/* ============ Phase 14 slice 3 — without Manage Part Numbers ============ */

test('FM-4: without Manage Part Numbers rows open the read-only Part Number details — the barcode label stays, nothing changes', async () => {
  session = signedInSession(['VIEW_PRODUCTION_DATA']);
  await renderPartNumbers();

  expect(
    screen.getByText(
      'View only — changing this needs the Manage Part Numbers, including hard deletion permission.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '+ New Part Number' }),
  ).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Edit 2027-60-8114-00' }),
  ).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Part Number 2027-60-8114-00 details' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Part Number details' });
  expect(
    await within(dialog).findByText(
      'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByText('C')).toBeInTheDocument();
  expect(within(dialog).getByText('ERP-PN-40412')).toBeInTheDocument();
  expect(dialog.querySelector('img.pn-img')).toHaveAttribute(
    'src',
    `/api/part-numbers/image?number=2027-60-8114-00&v=${encodeURIComponent(IMAGE_AT)}`,
  );
  expect(dialog.querySelector('input')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: /Save|Add Part Number/ }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Remove image' }),
  ).toBeNull();
  expect(within(dialog).queryByText('Delete Part Number Details')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeNull();

  // The barcode label stays reachable.
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  expect(screen.getAllByRole('dialog').length).toBeGreaterThan(1);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });

  // Close asks nothing (nothing is editable) and writes nothing.
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Part Number details' }),
    ).getByRole('button', { name: 'Close (Esc)' }),
  );
  expect(
    screen.queryByRole('dialog', { name: 'Part Number details' }),
  ).toBeNull();
  expect(writes).toEqual([]);
});

test('FM-4: a Part Number without saved details opens read-only as details, never as New', async () => {
  session = signedInSession(['VIEW_PRODUCTION_DATA']);
  await renderPartNumbers();

  fireEvent.click(
    screen.getByRole('button', { name: 'Part Number 214-406 details' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Part Number details' });
  await waitFor(() => expect(within(dialog).getAllByText('—')).toHaveLength(3));
  expect(screen.queryByRole('dialog', { name: 'New Part Number' })).toBeNull();
  expect(writes).toEqual([]);
});
