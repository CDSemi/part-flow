import './PnImage.css';

// The ONE shared PN image presentation: the uploaded PN master image
// when one exists, otherwise the single shared default PN image
// placeholder — the same default on every surface (Tracking Part
// Details, Management → Part Numbers; GUI_DESIGN §7.2/§14.1). No
// second default image exists anywhere.

/** The shared default PN image placeholder glyph. */
const PLACEHOLDER = '🔩';

export function PnImage({
  pn,
  image,
  size = 'md',
}: {
  /** Canonical PN — names the image for assistive technology. */
  pn: string;
  /** Uploaded PN master image (data/object URL); absent = placeholder. */
  image?: string;
  /** `md` = 64px detail size; `sm` = compact table-row size. */
  size?: 'md' | 'sm';
}) {
  const sizeClass = size === 'sm' ? ' sm' : '';
  if (image) {
    return (
      <img
        className={`pn-img${sizeClass}`}
        src={image}
        alt={`Part image — ${pn}`}
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
