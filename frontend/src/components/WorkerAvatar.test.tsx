import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { WorkerAvatar } from './WorkerAvatar';

// The shared Worker avatar: the stored avatar through its versioned
// URL, otherwise decorative initials — the name always renders beside
// it, so neither variant adds an accessible name of its own.

function renderAvatar(name: string, avatarUpdatedAt: string | null = null) {
  return render(
    <WorkerAvatar worker={{ id: 7, name, avatarUpdatedAt }} size="sm" />,
  ).container;
}

test('a Worker without an avatar shows up to two initials, uppercased', () => {
  for (const [name, expected] of [
    ['Alex Tran', 'AT'],
    ['Mai', 'M'],
    ['  alex   van   tran  ', 'AV'],
  ] as const) {
    const container = renderAvatar(name);
    const mark = container.querySelector('.worker-avatar')!;
    expect(mark.tagName).toBe('SPAN');
    expect(mark.textContent).toBe(expected);
    expect(mark.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('img')).toBeNull();
  }
});

test('a Worker with an avatar renders the versioned image URL, decorative', () => {
  const container = renderAvatar(
    'Alex Tran',
    '2026-09-01T08:00:00.123456+00:00',
  );
  const image = container.querySelector('img')!;
  expect(image.getAttribute('src')).toBe(
    '/api/workers/7/avatar?v=2026-09-01T08%3A00%3A00.123456%2B00%3A00',
  );
  expect(image.getAttribute('alt')).toBe('');
  expect(image.className).toBe('worker-avatar sm');
  expect(container.textContent).toBe('');
});

test('an explicit source (a staged preview) replaces the stored avatar', () => {
  const { container } = render(
    <WorkerAvatar
      worker={{ id: 7, name: 'Mai', avatarUpdatedAt: null }}
      size="md"
      src="blob:preview"
    />,
  );
  const image = container.querySelector('img')!;
  expect(image.getAttribute('src')).toBe('blob:preview');
  expect(image.className).toBe('worker-avatar md');
});
