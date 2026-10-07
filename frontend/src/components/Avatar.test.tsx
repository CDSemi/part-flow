import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { Avatar } from './Avatar';

// The shared avatar presentation (Worker and User avatars): the given
// image, otherwise decorative initials.

test('without an image the avatar shows up to two initials, decorative', () => {
  const { container } = render(<Avatar name="Jane Doe" />);
  const mark = container.querySelector('.worker-avatar')!;
  expect(mark.tagName).toBe('SPAN');
  expect(mark.textContent).toBe('JD');
  expect(mark.className).toBe('worker-avatar sm');
  expect(mark.getAttribute('aria-hidden')).toBe('true');
  expect(container.querySelector('img')).toBeNull();
});

test('a null source still shows the initials', () => {
  const { container } = render(<Avatar name="mai" size="md" src={null} />);
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('.worker-avatar.md')?.textContent).toBe('M');
});

test('a source renders a decorative image', () => {
  const { container } = render(
    <Avatar name="Jane Doe" size="md" src="/api/users/3/avatar?v=1" />,
  );
  const image = container.querySelector('img')!;
  expect(image.getAttribute('src')).toBe('/api/users/3/avatar?v=1');
  expect(image.getAttribute('alt')).toBe('');
  expect(image.className).toBe('worker-avatar md');
  expect(container.textContent).toBe('');
});
