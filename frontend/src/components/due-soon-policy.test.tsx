import { cleanup, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, expect, test, vi } from 'vitest';

import type { DueSoonPolicy } from '../views/dates';
import {
  useDueSoonPolicy,
  useDueSoonPolicyIfLoaded,
} from './due-soon-policy-context';
import { DueSoonPolicyProvider } from './due-soon-policy-provider';

// The Due Soon policy provider has NO default (GUI_DESIGN §3.12): a due
// consumer outside a provider, or a strict consumer under an explicitly
// unavailable policy, fails loudly instead of guessing a window.

const POLICY: DueSoonPolicy = { minDays: 1, leadTimePercent: 20, maxDays: 5 };

function providing(policy: DueSoonPolicy | null) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <DueSoonPolicyProvider policy={policy}>{children}</DueSoonPolicyProvider>
    );
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Render a hook expected to throw, without React's error noise. */
function expectThrows(render: () => void, message: string) {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  expect(render).toThrow(message);
}

test('useDueSoonPolicy returns the provided loaded policy', () => {
  const { result } = renderHook(() => useDueSoonPolicy(), {
    wrapper: providing(POLICY),
  });
  expect(result.current).toBe(POLICY);
});

test('useDueSoonPolicy throws without a provider and under an unavailable policy', () => {
  const message =
    'useDueSoonPolicy needs a DueSoonPolicyProvider with the loaded Due Soon policy';
  expectThrows(() => renderHook(() => useDueSoonPolicy()), message);
  expectThrows(
    () => renderHook(() => useDueSoonPolicy(), { wrapper: providing(null) }),
    message,
  );
});

test('useDueSoonPolicyIfLoaded throws without a provider and states an unavailable policy as null', () => {
  expectThrows(
    () => renderHook(() => useDueSoonPolicyIfLoaded()),
    'useDueSoonPolicyIfLoaded needs a DueSoonPolicyProvider',
  );
  expect(
    renderHook(() => useDueSoonPolicyIfLoaded(), { wrapper: providing(null) })
      .result.current,
  ).toBeNull();
  expect(
    renderHook(() => useDueSoonPolicyIfLoaded(), {
      wrapper: providing(POLICY),
    }).result.current,
  ).toBe(POLICY);
});
