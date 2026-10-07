import { workerAvatarUrl } from '../api/workers';
import type { Worker } from '../api/workers';
import { Avatar } from './Avatar';

// The shared Worker avatar: the Worker's uploaded avatar image when
// there is one, otherwise the Worker's initials (the shared `Avatar`
// presentation).

export function WorkerAvatar({
  worker,
  size = 'sm',
  src,
}: {
  worker: Pick<Worker, 'id' | 'name' | 'avatarUpdatedAt'>;
  /** `sm` = 32px table size; `md` = 64px editor size; `pill` = the
   * Scan Station Worker pill mark spanning the pill's two text lines. */
  size?: 'sm' | 'md' | 'pill';
  /**
   * Explicit image source (an editor's staged local preview) shown
   * instead of the stored avatar.
   */
  src?: string;
}) {
  return (
    <Avatar
      name={worker.name}
      size={size}
      src={src ?? workerAvatarUrl(worker)}
    />
  );
}
