import './PnImage.css';

import { useState } from 'react';

// The ONE shared PN image presentation: the uploaded Part Number image
// when one exists, otherwise the single shared default PN image
// placeholder — the same default on every surface (Tracking Part
// Details, Management → Part Numbers; GUI_DESIGN §7.2/§14.1). No
// second default image exists anywhere. An image that fails to load
// (removed or deleted between the read and the image request, or any
// other load failure) falls back to the same placeholder — the
// browser's broken-image icon never shows.

/** The shared default PN image placeholder glyph. */
const PLACEHOLDER = '🔩';

export function PnImage({
  pn,
  image,
  size = 'md',
}: {
  /** Canonical PN — names the image for assistive technology. */
  pn: string;
  /** Uploaded PN image URL — a server image URL (`partNumberImageUrl`)
   * or a staged object URL; absent or failing to load = placeholder. */
  image?: string;
  /** `md` = 64px detail size; `sm` = compact table-row size. */
  size?: 'md' | 'sm';
}) {
  // The URL that last failed to load; a different URL is tried again.
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const sizeClass = size === 'sm' ? ' sm' : '';
  if (image && image !== failedImage) {
    return (
      <img
        className={`pn-img${sizeClass}`}
        src={image}
        alt={`Part image — ${pn}`}
        onError={() => setFailedImage(image)}
      />
    );
  }
  // The placeholder is decorative — the PN itself is named elsewhere.
  return (
    <span className={`pn-img${sizeClass}`} aria-hidden="true">
      {PLACEHOLDER}
    </span>
  );
}
