import './WorkerAvatar.css';

// The ONE shared avatar presentation (Worker and User avatars): the
// given image when there is one, otherwise the person's initials in a
// tinted circle (the approved Scan Station Worker pill mark). The name
// always renders beside the avatar, so the mark itself is decorative.
// The owner resolves the image URL, so this module never links Workers
// and Users.

/** Initials: the first letter of the first two words, uppercased. */
function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => Array.from(word)[0])
    .join('')
    .toUpperCase();
}

export function Avatar({
  name,
  size = 'sm',
  src,
}: {
  name: string;
  /** `sm` = 32px table size; `md` = 64px editor size; `pill` = the
   * Scan Station Worker pill mark spanning the pill's two text lines. */
  size?: 'sm' | 'md' | 'pill';
  /** The image to show (a stored avatar URL or a staged preview);
   * none → initials. */
  src?: string | null;
}) {
  const className = `worker-avatar ${size}`;
  if (src) {
    return <img className={className} src={src} alt="" />;
  }
  return (
    <span className={className} aria-hidden="true">
      {initials(name)}
    </span>
  );
}
