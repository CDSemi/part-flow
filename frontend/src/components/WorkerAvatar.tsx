import './WorkerAvatar.css';

import { workerAvatarUrl } from '../api/workers';
import type { Worker } from '../api/workers';

// The ONE shared Worker avatar presentation: the uploaded avatar image
// when the Worker has one, otherwise the Worker's initials in a tinted
// circle (the approved Scan Station Worker pill mark). The name always
// renders beside the avatar, so the mark itself is decorative.

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

export function WorkerAvatar({
  worker,
  size = 'sm',
  src,
}: {
  worker: Pick<Worker, 'id' | 'name' | 'avatarUpdatedAt'>;
  /** `sm` = 32px table size; `md` = 64px editor size. */
  size?: 'sm' | 'md';
  /**
   * Explicit image source (an editor's staged local preview) shown
   * instead of the stored avatar.
   */
  src?: string;
}) {
  const className = `worker-avatar ${size}`;
  const imageSrc = src ?? workerAvatarUrl(worker);
  if (imageSrc) {
    return <img className={className} src={imageSrc} alt="" />;
  }
  return (
    <span className={className} aria-hidden="true">
      {initials(worker.name)}
    </span>
  );
}
