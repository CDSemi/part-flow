import { useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api/client';
import {
  createWorker,
  listWorkers,
  removeWorkerAvatar,
  updateWorker,
  uploadWorkerAvatar,
} from '../../api/workers';
import type { Worker } from '../../api/workers';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { useSession } from '../../app/session-context';
import { ModalDialog } from '../../components/ModalDialog';
import { WorkerAvatar } from '../../components/WorkerAvatar';
import {
  ImageUploadError,
  prepareImageUpload,
} from '../../components/image-upload';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  ActiveField,
  AdminField,
  RowOpener,
  SectionHeader,
  ServerErrorNote,
  StatusPill,
  ViewOnlyNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Workers: the Scan Station audit identity of the
// people operating the stations — separate from application Users. The
// standard table + editor pattern: Workers are created and edited here
// and deactivated, never deleted. The server owns every rule (badge
// canonical form, uniqueness across all Workers, image limits); the
// editor mirrors the badge rules only to answer early.
//
// A save is up to two audited writes — the profile, then an avatar
// change on its own binary endpoint — so the editor handles a
// partially completed save explicitly. An edit sends only the fields
// the operator changed, so an editor opened before another
// administrator's change never reverts it. The list reloads only when
// the editor closes, so a failing refresh never unmounts an open editor.
//
// Without the Manage Workers permission the section is view-only, and
// the server withholds the badge barcodes, so no Badge column renders.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'workers')?.subtitle ?? '';

const MAX_BADGE_LENGTH = 128;

const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the Worker before trying again.';

/** The badge as the server stores and matches it: trimmed, uppercase. */
function canonicalBadge(raw: string): string {
  return raw.trim().toUpperCase();
}

/** Client mirror of the server badge rule, on the canonical form. */
function badgeError(canonical: string): string | null {
  if (!canonical) return 'A badge barcode is required.';
  if (Array.from(canonical).length > MAX_BADGE_LENGTH) {
    return 'A badge barcode must be at most 128 characters.';
  }
  if (canonical.startsWith('PF:')) {
    return 'A badge barcode cannot start with PF: — scan the barcode printed on the employee badge.';
  }
  return null;
}

type PendingDialog = { kind: 'new' } | { kind: 'edit'; worker: Worker };

export function WorkersSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const canWrite = useSession().can('MANAGE_WORKERS');
  const workersData = useApiData(listWorkers);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);
  const ready = workersData.state.status === 'ready';

  const closeDialog = (wroteAny: boolean) => {
    setDialog(null);
    if (wroteAny) workersData.reload();
  };

  let body;
  if (workersData.state.status === 'loading') {
    body = <LoadingState label="Loading Workers" />;
  } else if (workersData.state.status === 'error') {
    body = (
      <ErrorState
        message="Worker data could not be loaded."
        detail={workersData.state.message}
        onRetry={workersData.reload}
      />
    );
  } else {
    const workers = workersData.state.data;
    // The server sends the badges only to users who may manage Workers.
    const showBadges = workers.every((worker) => worker.badgeBarcode !== null);
    body = (
      <>
        {workers.length === 0 ? (
          <EmptyState message="No Workers configured yet." />
        ) : (
          <table className="ad-table">
            <thead>
              <tr>
                <th>Worker</th>
                {showBadges ? <th>Badge barcode</th> : null}
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {workers.map((worker) => (
                <tr
                  key={worker.id}
                  className={canWrite ? 'selrow' : undefined}
                  onClick={
                    canWrite
                      ? () => setDialog({ kind: 'edit', worker })
                      : undefined
                  }
                >
                  <td>
                    <RowOpener
                      editable={canWrite}
                      label={`Edit ${worker.name}`}
                    >
                      <span className="ad-worker">
                        <WorkerAvatar worker={worker} size="sm" />
                        <b>{worker.name}</b>
                      </span>
                    </RowOpener>
                  </td>
                  {showBadges ? (
                    <td className="mono" data-label="Badge barcode">
                      {worker.badgeBarcode}
                    </td>
                  ) : null}
                  <td>
                    <StatusPill active={worker.isActive} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="ad-notice">
          A Worker is the Scan Station identity of a person operating the
          stations — separate from application Users. The badge barcode is the
          barcode already printed on the employee badge: it is saved in capital
          letters and matched regardless of letter case, and it must be unique
          among all Workers, including inactive ones. Workers are deactivated,
          never deleted.
        </div>
      </>
    );
  }

  return (
    <>
      <SectionHeader
        title="Workers"
        subtitle={SUBTITLE}
        action={
          canWrite ? (
            <button
              className="btn primary"
              disabled={!ready || writeBlocked}
              onClick={() => setDialog({ kind: 'new' })}
            >
              + New Worker
            </button>
          ) : undefined
        }
      />
      {canWrite ? null : <ViewOnlyNote permission="MANAGE_WORKERS" />}
      {body}
      {dialog ? (
        <WorkerDialog
          worker={dialog.kind === 'edit' ? dialog.worker : undefined}
          writeBlocked={writeBlocked}
          onClose={closeDialog}
        />
      ) : null}
    </>
  );
}

/** The avatar change staged in the editor, applied on Save. */
type StagedAvatar =
  | { kind: 'upload'; image: Blob; previewUrl: string }
  | { kind: 'remove' }
  | null;

function WorkerDialog({
  worker,
  writeBlocked,
  onClose,
}: {
  worker?: Worker;
  writeBlocked: boolean;
  /** Close request; `wroteAny` = at least one write was sent. */
  onClose: (wroteAny: boolean) => void;
}) {
  // The saved record the editor works against: undefined until a new
  // Worker exists, then the server's latest answer.
  const [saved, setSaved] = useState<Worker | undefined>(worker);
  const [name, setName] = useState(worker?.name ?? '');
  const [badge, setBadge] = useState(worker?.badgeBarcode ?? '');
  const [isActive, setIsActive] = useState(worker?.isActive ?? true);
  const [staged, setStaged] = useState<StagedAvatar>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const wroteAny = useRef(false);
  const closed = useRef(false);

  // Release the staged preview's object URL when it is replaced or the
  // editor unmounts.
  useEffect(() => {
    if (staged?.kind !== 'upload') return undefined;
    const url = staged.previewUrl;
    return () => URL.revokeObjectURL(url);
  }, [staged]);

  const close = () => {
    if (closed.current) return;
    closed.current = true;
    onClose(wroteAny.current);
  };

  // Cancel, Escape and the backdrop are ignored while a save is in
  // flight: its writes cannot be recalled, so the editor stays open
  // until they settle — success closes it (reloading the list after
  // the commits), a failure keeps it open with the error.
  const requestClose = () => {
    if (busy) return;
    close();
  };

  const trimmedName = name.trim();
  const canonical = canonicalBadge(badge);
  const nameInvalid = !trimmedName;
  const badgeInvalid = badgeError(canonical);
  const title = saved ? 'Edit Worker' : 'New Worker';
  const hasStoredAvatar = saved?.avatarUpdatedAt != null;
  const avatarWorker = {
    id: saved?.id ?? 0,
    name: trimmedName,
    avatarUpdatedAt:
      staged?.kind === 'remove' ? null : (saved?.avatarUpdatedAt ?? null),
  };
  const showRemove =
    staged?.kind === 'upload' || (hasStoredAvatar && staged === null);
  const controlsBlocked = writeBlocked || busy || preparing;

  const chooseImage = async (file: File | undefined) => {
    if (!file) return;
    setImageError(null);
    setPreparing(true);
    try {
      const image = await prepareImageUpload(file);
      setStaged({
        kind: 'upload',
        image,
        previewUrl: URL.createObjectURL(image),
      });
    } catch (error) {
      setImageError(
        error instanceof ImageUploadError
          ? error.message
          : 'Choose a PNG, JPEG or WebP image.',
      );
    } finally {
      setPreparing(false);
    }
  };

  const removeAvatar = () => {
    setImageError(null);
    setStaged(hasStoredAvatar ? { kind: 'remove' } : null);
  };

  const submit = async () => {
    if (nameInvalid || badgeInvalid) {
      setAttempted(true);
      return;
    }
    // Only the fields whose value differs from the saved record.
    const delta = saved
      ? {
          ...(trimmedName !== saved.name ? { name: trimmedName } : {}),
          ...(canonical !== saved.badgeBarcode
            ? { badgeBarcode: canonical }
            : {}),
          ...(isActive !== saved.isActive ? { isActive } : {}),
        }
      : null;
    const sendProfile = delta === null || Object.keys(delta).length > 0;
    if (!sendProfile && staged === null) {
      close();
      return;
    }
    setBusy(true);
    setServerError(null);
    let step: 'profile' | 'avatar' = 'profile';
    let profileChanged = false;
    try {
      // Step 1 — the profile: create, or only the changed fields.
      wroteAny.current = true;
      let record: Worker;
      if (!saved) {
        record = await createWorker({
          name: trimmedName,
          badgeBarcode: canonical,
        });
        profileChanged = true;
      } else if (delta && sendProfile) {
        record = await updateWorker(saved.id, delta);
        profileChanged = true;
      } else {
        record = saved;
      }
      setSaved(record);

      // Step 2 — the staged avatar change, its own audited write.
      step = 'avatar';
      if (staged?.kind === 'upload') {
        record = await uploadWorkerAvatar(record.id, staged.image);
      } else if (staged?.kind === 'remove') {
        record = await removeWorkerAvatar(record.id);
      }
      setSaved(record);
      setStaged(null);
      close();
    } catch (error) {
      if (!(error instanceof ApiError)) {
        // No answer: the write may or may not have committed.
        setServerError(UNKNOWN_OUTCOME_MESSAGE);
      } else if (step === 'profile') {
        setServerError(error.message);
      } else if (profileChanged) {
        setServerError(
          `The Worker was saved, but the avatar could not be updated: ${error.message}`,
        );
      } else {
        setServerError(`The avatar could not be updated: ${error.message}`);
      }
      setBusy(false);
    }
  };

  return (
    <ModalDialog label={title} onClose={requestClose}>
      <h3>{title}</h3>
      <div className="ad-form">
        <AdminField label="Name">
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Alex Tran"
          />
        </AdminField>
        {nameInvalid && attempted ? (
          <div className="err" role="alert">
            A name is required.
          </div>
        ) : null}
        <AdminField label="Badge barcode">
          <input
            className="field mono"
            value={badge}
            onChange={(event) => setBadge(event.target.value)}
            placeholder="Scan or type the employee badge barcode"
            autoComplete="off"
            spellCheck={false}
          />
        </AdminField>
        <div className="ad-fieldnotes">
          <div aria-live="polite">
            {canonical && canonical !== badge ? (
              <p className="ad-fieldnote">
                Saved as: <span className="mono">{canonical}</span>
              </p>
            ) : null}
          </div>
          <p className="ad-fieldhelp">
            Saved in capital letters — letter case does not matter when
            scanning.
          </p>
        </div>
        {badgeInvalid && attempted ? (
          <div className="err" role="alert">
            {badgeInvalid}
          </div>
        ) : null}
        <div className="ad-avatarblock">
          <span className="ad-avatarlabel">
            Avatar <span className="field-optional">(optional)</span>
          </span>
          <div className="ad-avatarrow">
            <WorkerAvatar
              worker={avatarWorker}
              size="md"
              src={staged?.kind === 'upload' ? staged.previewUrl : undefined}
            />
            <div className="ad-avataractions">
              <button
                type="button"
                className="btn ghost"
                disabled={controlsBlocked}
                onClick={() => fileInput.current?.click()}
              >
                Choose image…
              </button>
              <input
                ref={fileInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="Avatar image file"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  void chooseImage(file);
                }}
              />
              {showRemove ? (
                <button
                  type="button"
                  className="btn ghost"
                  disabled={controlsBlocked}
                  onClick={removeAvatar}
                >
                  Remove avatar
                </button>
              ) : null}
            </div>
          </div>
          <p className="ad-fieldhelp">
            PNG, JPEG or WebP. Large images are resized before upload.
          </p>
          {imageError ? (
            <div className="err" role="alert">
              {imageError}
            </div>
          ) : null}
        </div>
        {saved ? (
          <ActiveField
            label="Active"
            checked={isActive}
            onChange={setIsActive}
          />
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" disabled={busy} onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={controlsBlocked}
          onClick={() => void submit()}
        >
          {saved ? 'Save changes' : 'Add Worker'}
        </button>
      </div>
    </ModalDialog>
  );
}
