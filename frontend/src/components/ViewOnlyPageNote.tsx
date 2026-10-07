import type { Permission } from '../api/roles';
import { PERMISSION_LABELS } from '../views/administration/permissions';
import { PageNote } from './PageNote';

/**
 * The page-level view-only note of a Management view whose changes the
 * signed-in user may not make (Phase 14 slice 3): it names the
 * permission — or the permissions, any one of which — the view's
 * changes need, in the sentence form of Administration's view-only
 * note. Presentation only: the server checks every write itself.
 */
export function ViewOnlyPageNote({
  permissions,
}: {
  permissions: readonly Permission[];
}) {
  const labels = permissions.map((key) => PERMISSION_LABELS[key]);
  return (
    <PageNote>
      {labels.length === 1
        ? `View only — changing this needs the ${labels[0]} permission.`
        : `View only — changing this needs one of these permissions: ${labels.join(', ')}.`}
    </PageNote>
  );
}
