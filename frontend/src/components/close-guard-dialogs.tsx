import type { ReactNode } from 'react';

import { ConfirmDialog } from './ConfirmDialog';
import { UnsavedChoiceDialog } from './UnsavedChoiceDialog';

/**
 * The close decision of an EXISTING record's editor that holds unsaved
 * changes (`useCloseGuard`): the shared `Unsaved changes` choice — Save
 * changes / Discard changes / Cancel (Esc). Save first closes this
 * decision, then runs the editor's own save, which validates, keeps the
 * editor open with its message when the save is refused, and closes it
 * on success. Discard closes the editor without saving; Cancel returns
 * to the form with every entry intact.
 */
export function UnsavedChangesDialog({
  children,
  saveDisabled = false,
  onKeepEditing,
  onSave,
  onDiscard,
}: {
  /** One line naming what has unsaved changes. */
  children: ReactNode;
  /** External gate on Save (offline write-block). */
  saveDisabled?: boolean;
  onKeepEditing: () => void;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <UnsavedChoiceDialog
      title="Unsaved changes"
      saveLabel="Save changes"
      discardLabel="Discard changes"
      saveDisabled={saveDisabled}
      onCancel={onKeepEditing}
      onSave={() => {
        onKeepEditing();
        onSave();
      }}
      onDiscard={onDiscard}
    >
      {children}
    </UnsavedChoiceDialog>
  );
}

/**
 * The close decision of a NEW record or a one-time entry that holds
 * input (`useCloseGuard`): nothing exists yet, so there is nothing to
 * save — Keep editing returns to the form, Discard input closes it.
 */
export function DiscardInputDialog({
  title,
  onKeepEditing,
  onDiscard,
}: {
  /** For example `Discard new Area?`. */
  title: string;
  onKeepEditing: () => void;
  onDiscard: () => void;
}) {
  return (
    <ConfirmDialog
      title={title}
      confirmLabel="Discard input"
      cancelLabel="Keep editing"
      onCancel={onKeepEditing}
      onConfirm={onDiscard}
    >
      Your entered information will not be saved.
    </ConfirmDialog>
  );
}
