import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';

import { PnImage } from './PnImage';

// The ONE shared PN image: the uploaded image, or the one default
// placeholder — also when the image fails to load (removed or deleted
// between the read and the image request), so the browser's
// broken-image icon never shows.

afterEach(cleanup);

test('an image URL renders the image; a load failure falls back to the placeholder', () => {
  const { container, rerender } = render(
    <PnImage pn="214-406" image="/api/part-numbers/image?number=214-406&v=1" />,
  );
  const img = container.querySelector('img');
  expect(img).not.toBeNull();
  expect(img).toHaveAttribute('alt', 'Part image — 214-406');

  fireEvent.error(img!);
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('span.pn-img')).toHaveTextContent('🔩');

  // A new image version is tried again.
  rerender(
    <PnImage pn="214-406" image="/api/part-numbers/image?number=214-406&v=2" />,
  );
  expect(container.querySelector('img')).toHaveAttribute(
    'src',
    '/api/part-numbers/image?number=214-406&v=2',
  );
});

test('no image renders the placeholder', () => {
  const { container } = render(<PnImage pn="214-406" size="sm" />);
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('span.pn-img.sm')).toHaveAttribute(
    'aria-hidden',
    'true',
  );
});
