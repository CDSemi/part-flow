import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, test, vi } from 'vitest';

import type { Area, Operation } from '../../api/environment';
import type { Machine } from '../../api/machines';
import { PERMISSIONS } from '../../api/roles';
import { ConnectivityContext } from '../../app/connectivity-context';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { PlannedRoutesView } from './PlannedRoutesView';
import { RouteStepList } from './route-step-editor';
import type { Catalog, EditableStep } from './route-steps';
import { editableStep, stepsKey, validateSteps } from './route-steps';

// FE-11: the route step rows shared by Planned Routes and Tracking →
// Edit assigned Route (GUI_DESIGN §13.2). Planned Routes renders them
// with the defaults (planned-routes.test.tsx is the unchanged guard of
// that behavior); an assigned route's tail continues the route's
// numbering, may be emptied, and starts a new step in the last locked
// step's Area.

function area(id: number, name: string, isActive = true): Area {
  return {
    id,
    departmentId: 1,
    name,
    barcodeValue: `PF:AREA:${id}`,
    description: null,
    color: null,
    isTerminal: false,
    isActive,
    workerIdentificationMode: 'DISABLED',
    fixedWorkerId: null,
    workerSessionTimeoutMinutes: null,
  };
}

function operation(id: number, areaId: number, code: string): Operation {
  return {
    id,
    areaId,
    code,
    name: null,
    description: null,
    defaultExpectedDuration: null,
    isExternal: false,
    isActive: true,
  };
}

const CATALOG: Catalog = {
  areas: [area(1, 'Material'), area(2, 'Lathe'), area(3, 'Mill', false)],
  operations: [operation(11, 1, 'RCV'), operation(21, 2, 'TURN')],
  machines: [] as Machine[],
};

function stepIn(
  areaId: number,
  operationId: number,
  key: number,
): EditableStep {
  return editableStep(
    {
      areaId,
      operationId,
      expectedDuration: null,
      preferredMachineId: null,
      instructions: null,
    },
    key,
  );
}

function Harness({
  initial,
  ...props
}: {
  initial: EditableStep[];
  firstNumber?: number;
  minSteps?: number;
  fallbackAreaId?: number;
  disabled?: boolean;
}) {
  const [steps, setSteps] = useState(initial);
  return (
    <>
      <RouteStepList
        catalog={CATALOG}
        steps={steps}
        onChange={setSteps}
        {...props}
      />
      <output data-testid="key">{stepsKey(steps)}</output>
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('FE-11: the defaults number from 1 and keep at least one step', () => {
  render(<Harness initial={[stepIn(1, 11, 0)]} />);
  expect(screen.getByLabelText('Step 1 Area')).toHaveValue('1');
  expect(screen.getByRole('button', { name: 'Remove step 1' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Move step 1 up' })).toBeDisabled();
});

test('FE-11: firstNumber continues the route numbering in every label', () => {
  render(
    <Harness
      initial={[stepIn(1, 11, 0), stepIn(2, 21, 1)]}
      firstNumber={4}
      minSteps={0}
    />,
  );
  expect(screen.getByLabelText('Step 4 Area')).toHaveValue('1');
  expect(screen.getByLabelText('Step 5 Operation')).toHaveValue('21');
  expect(screen.getByLabelText('Step 5 expected duration')).toBeInTheDocument();
  expect(screen.getByLabelText('Step 4 preferred Machine')).toBeInTheDocument();
  expect(
    Array.from(
      document.querySelectorAll('.rt-steprow .idx'),
      (el) => el.textContent,
    ),
  ).toEqual(['4', '5']);
  fireEvent.click(screen.getByRole('button', { name: 'Move step 5 up' }));
  expect(screen.getByLabelText('Step 4 Area')).toHaveValue('2');
});

test('FE-11: minSteps 0 removes down to an empty list; a new step starts in the fallback Area', () => {
  render(
    <Harness initial={[stepIn(1, 11, 0)]} minSteps={0} fallbackAreaId={2} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 1' }));
  expect(document.querySelectorAll('.rt-steprow')).toHaveLength(0);
  expect(screen.getByTestId('key').textContent).toBe('[]');
  fireEvent.click(screen.getByRole('button', { name: '+ Add step' }));
  expect(screen.getByLabelText('Step 1 Area')).toHaveValue('2');
  expect(screen.getByLabelText('Step 1 Operation')).toHaveValue('21');
  // With a row present the new step follows the last row's Area.
  fireEvent.change(screen.getByLabelText('Step 1 Area'), {
    target: { value: '1' },
  });
  fireEvent.click(screen.getByRole('button', { name: '+ Add step' }));
  expect(screen.getByLabelText('Step 2 Area')).toHaveValue('1');
});

test('FE-11: an inactive fallback Area is never used — the first active Area is', () => {
  render(<Harness initial={[]} minSteps={0} fallbackAreaId={3} />);
  fireEvent.click(screen.getByRole('button', { name: '+ Add step' }));
  expect(screen.getByLabelText('Step 1 Area')).toHaveValue('1');
});

test('FE-11: disabled makes every control read-only', () => {
  render(<Harness initial={[stepIn(1, 11, 0), stepIn(2, 21, 1)]} disabled />);
  expect(screen.getByLabelText('Step 1 Area')).toBeDisabled();
  expect(screen.getByLabelText('Step 2 preferred Machine')).toBeDisabled();
  expect(screen.getByLabelText('Step 1 expected duration')).toHaveAttribute(
    'readonly',
  );
  expect(
    screen.getByRole('button', { name: 'Move step 1 down' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Remove step 2' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '+ Add step' })).toBeDisabled();
});

test('validateSteps names the absolute step number', () => {
  const steps = [stepIn(1, 11, 0), stepIn(3, 21, 1)];
  expect(validateSteps(steps, CATALOG, 4)).toBe(
    'Step 5: choose an available Area.',
  );
  expect(validateSteps([], CATALOG, 4)).toBeNull();
  expect(
    validateSteps([{ ...stepIn(1, 11, 0), durationText: 'soon' }], CATALOG, 3),
  ).toBe('Step 3: enter the estimated time like 45m, 4h or 2d 03h.');
});

test('stepsKey ignores render keys and surrounding whitespace', () => {
  const a = [stepIn(1, 11, 0)];
  const b = [{ ...stepIn(1, 11, 7), instructions: '  ' }];
  expect(stepsKey(a)).toBe(stepsKey(b));
  expect(stepsKey([{ ...stepIn(1, 11, 0), durationText: '4h' }])).not.toBe(
    stepsKey(a),
  );
});

// ---------------------------------------------------------------------------
// The used-template note points at the assigned-route workflow
// ---------------------------------------------------------------------------

test('FE-13: the used Planned Route note names Tracking → Edit assigned Route…', async () => {
  const user = {
    id: 90,
    loginName: 'mia',
    displayName: 'Mia Manager',
    roleId: 2,
    roleName: 'Manager',
    avatarUpdatedAt: null,
    permissions: [...PERMISSIONS],
    mustChangePassword: false,
    sessionExpiresAt: null,
  };
  const session: SessionValue = {
    status: 'signed-in',
    user,
    setupOpen: false,
    checking: false,
    endedBy: null,
    can: (key) => hasPermission(user, key),
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
  const template = {
    id: 7,
    name: 'Bracket std v3',
    description: null,
    archived_at: null,
    archived_on: null,
    updated_on: '2026-07-24',
    ever_used: true,
    usage_count: 2,
    steps: [
      {
        id: 700,
        sequence: 1,
        area_id: 1,
        operation_id: 11,
        expected_duration: null,
        preferred_machine_id: null,
        instructions: null,
      },
    ],
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body =
        url === '/api/route-templates/management'
          ? [template]
          : url === '/api/areas' ||
              url === '/api/operations' ||
              url === '/api/machines'
            ? []
            : { detail: `unexpected ${url}` };
      return new Response(JSON.stringify(body), {
        status: Array.isArray(body) ? 200 : 404,
      });
    }),
  );
  render(
    <SessionContext.Provider value={session}>
      <ConnectivityContext.Provider
        value={{ status: 'connected', retry: () => {} }}
      >
        <PlannedRoutesView />
      </ConnectivityContext.Provider>
    </SessionContext.Provider>,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit Bracket std v3' }),
  );
  const note = document.querySelector('.rt-editnote');
  expect(note?.textContent?.replace(/\s+/g, ' ')).toBe(
    'Changes apply to future assignments only. The 2 Quantity Flows already released with this route keep the assigned route unchanged — an in-production route is changed in its own audited workflow, with a reason (Tracking → Edit assigned Route…).',
  );
});
