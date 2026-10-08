import './EditPartNumberDialog.css';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { ApiError, errorMessage, isReleaseMismatch } from '../api/client';
import {
  createPartNumber,
  deletePartNumber,
  partNumberImageUrl,
  removePartNumberImage,
  resolvePartNumber,
  updatePartNumber,
  uploadPartNumberImage,
} from '../api/part-numbers';
import type { PartNumberDetails, PartNumberMaster } from '../api/part-numbers';
import { normalizePartNumber, pnBarcode } from '../views/scan-station/barcode';
import { ConfirmDialog } from './ConfirmDialog';
import { ImageUploadError, prepareImageUpload } from './image-upload';
import { ModalDialog } from './ModalDialog';
import { PnBarcodeLabelDialog } from './PnBarcodeLabelDialog';
import { PnImage } from './PnImage';
import { UnsavedChoiceDialog } from './UnsavedChoiceDialog';
import { ErrorState, LoadingState } from './view-states';

// The ONE `Edit Part Number` dialog (GUI_DESIGN §14.2; §11.2): opened
// from Management → Part Numbers (a row, or `+ New Part Number`) and
// from the PN control of a Work Orders demand line. The canonical PN
// string is the production identity — saved details (image, Name /
// Description, informational revision, ERP ID) are optional metadata
// only and never gate production use. The PN is entered once at
// creation and never edited; the barcode always derives from it.
//
// A save is up to two audited writes — the details (create, or a PATCH
// of only the changed fields), then a staged image change on its own
// binary endpoint — so a partially completed save is handled
// explicitly and entered input is never silently discarded (GUI §3.10).
//
// A user who may not manage Part Numbers opens the same dialog
// read-only (Phase 14 slice 3): `Part Number details`, the saved values
// as text, the image without its actions, the barcode label, and no
// write at all.

const UNKNOWN_OUTCOME_MESSAGE =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh, then check the Part Number before trying again.';

/** Debounce of the duplicate lookup while a new PN is typed. */
const DUPLICATE_LOOKUP_DEBOUNCE_MS = 250;

/** The image change staged in the editor, applied on Save. */
type StagedImage =
  | { kind: 'upload'; image: Blob; previewUrl: string }
  | { kind: 'remove' }
  | null;

/** The saved details as the dialog loaded them (fixed PN only). */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

/** A text field as the server stores it: trimmed, blank = null. */
function storedText(value: string): string | null {
  return value.trim() || null;
}

/** Whether a field still holds the saved value (null = blank). */
function sameText(value: string, saved: string | null): boolean {
  return storedText(value) === saved;
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <label>
      <span>{label}</span>
      {children}
    </label>
  );
}

/**
 * Read-only Part Number identity header (the Machines §12.3 idiom): the
 * canonical PN and the barcode derived from it — one value in the
 * PF:PN: namespace, never an independently editable field — plus the
 * entry to the printable barcode label. It needs no server, so the
 * label stays available while the details load, fail, or are offline.
 */
function IdentityHeader({
  pn,
  onOpenLabel,
}: {
  pn: string;
  onOpenLabel: () => void;
}) {
  return (
    <div className="pnm-idhead">
      <div className="idcol">
        <span className="idlabel">Part Number</span>
        <span className="idvalue tag">{pn}</span>
      </div>
      <div className="idcol grow">
        <span className="idlabel">Barcode</span>
        <span className="idvalue barcodeval">{pnBarcode(pn)}</span>
      </div>
      <button type="button" className="pnm-labelbtn" onClick={onOpenLabel}>
        Barcode label…
      </button>
    </div>
  );
}

/**
 * Add or edit the saved details of one Part Number.
 *
 * - `pn` given (a list row, a demand line): the identity header renders
 *   at once while the saved details load; saved details open as `Edit
 *   Part Number`, none as `New Part Number` with the PN fixed.
 * - `pn` undefined (`+ New Part Number`): the PN is typed, canonicalized
 *   like every PN entry path (trimmed, internal whitespace rejected —
 *   never silently removed — uppercased) and sent as entered; the
 *   server stays the authority.
 *
 * Edit hosts the delete section (the Machines Danger-Zone presentation,
 * user-facing title `Delete Part Number Details`): `Delete details…`
 * hard-deletes only the saved details behind a plain destructive
 * confirmation.
 */
export function EditPartNumberDialog({
  pn,
  readOnly: readOnlyAtOpen = false,
  writeBlocked,
  onClose,
}: {
  /** Fixed PN (row, demand line) — or undefined for `+ New Part Number`. */
  pn?: string;
  /** Show the details without any change (a user who may not manage
   * Part Numbers); needs a fixed `pn`. Fixed when the dialog opens. */
  readOnly?: boolean;
  /** Disables every write while the backend is unreachable; reading,
   * staging an image, the label and closing stay available. */
  writeBlocked: boolean;
  /** `exists`: whether saved details exist as the dialog LAST OBSERVED
   *  them — from the load (record / null) or from a later write or
   *  refusal (create/update/image → true, delete or E2 → false,
   *  E1 → true); null only when nothing was ever observed (load still
   *  pending or failed, and no write answered). */
  onClose: (result: { wroteAny: boolean; exists: boolean | null }) => void;
}) {
  // The PN the dialog is fixed to: the given one, or — after a new
  // record was created from a typed entry — the server's canonical PN,
  // or — after a typed entry's create answered E1 — its canonical PN.
  // An open dialog never changes mode under a later change of the
  // sign-in.
  const [readOnly] = useState(readOnlyAtOpen);
  const [fixedPn, setFixedPn] = useState<string | undefined>(pn);
  const [loadState, setLoadState] = useState<LoadState>(
    pn !== undefined ? { status: 'loading' } : { status: 'ready' },
  );
  // The saved record the editor works against; null = no saved details
  // (the dialog is `New Part Number`).
  const [record, setRecord] = useState<PartNumberMaster | null>(null);
  const [pnInput, setPnInput] = useState('');
  const [name, setName] = useState('');
  const [revision, setRevision] = useState('');
  const [erpId, setErpId] = useState('');
  const [staged, setStaged] = useState<StagedImage>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [pnAttempted, setPnAttempted] = useState(false);
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [labelOpen, setLabelOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [leaveConfirm, setLeaveConfirm] = useState(false);
  const wroteAny = useRef(false);
  const exists = useRef<boolean | null>(null);
  const closed = useRef(false);
  const loadGeneration = useRef(0);
  // Whether a Retry of a failed load keeps the entered values (set by
  // the E1 recovery reload).
  const keepInputOnRetry = useRef(false);
  const pnInputRef = useRef<HTMLInputElement>(null);

  /**
   * (Re)load the saved details of the fixed PN. `keepInput` keeps the
   * values actually entered (non-blank) as unsaved edits against the
   * loaded record (the E1 recovery) and fills every blank field from the
   * record, so a field the user never filled in is not dirty and the
   * next save never clears a value saved elsewhere; otherwise the
   * fields start from the record.
   */
  const load = useCallback((target: string, keepInput: boolean) => {
    const generation = ++loadGeneration.current;
    setLoadState({ status: 'loading' });
    resolvePartNumber(target).then(
      (found) => {
        if (loadGeneration.current !== generation) return;
        exists.current = found !== null;
        setRecord(found);
        const field = (entered: string, saved: string | null | undefined) =>
          keepInput && storedText(entered) !== null ? entered : (saved ?? '');
        setName((entered) => field(entered, found?.name));
        setRevision((entered) => field(entered, found?.currentRevision));
        setErpId((entered) => field(entered, found?.erpId));
        setLoadState({ status: 'ready' });
      },
      (error: unknown) => {
        if (loadGeneration.current !== generation) return;
        setLoadState({ status: 'error', message: errorMessage(error) });
      },
    );
  }, []);

  useEffect(() => {
    if (pn !== undefined) load(pn, false);
    // A NEW record starts with the PN focused — the one required field.
    else pnInputRef.current?.focus();
    return () => {
      // Discard a load that answers after the dialog closed.
      loadGeneration.current += 1;
    };
    // Initial load only — the PN never changes while the dialog is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Release the staged preview's object URL when it is replaced or the
  // editor unmounts.
  useEffect(() => {
    if (staged?.kind !== 'upload') return undefined;
    const url = staged.previewUrl;
    return () => URL.revokeObjectURL(url);
  }, [staged]);

  // Typed PN feedback (`+ New Part Number` only): canonicalization
  // mirrors every other PN entry path.
  const trimmed = pnInput.trim();
  const canonical = fixedPn === undefined ? normalizePartNumber(pnInput) : null;

  // Debounced duplicate lookup of the typed canonical PN. A failed
  // lookup shows nothing: the server stays the authority and refuses a
  // duplicate create with the same copy (E1).
  useEffect(() => {
    if (canonical === null) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      resolvePartNumber(canonical).then(
        (found) => {
          if (!cancelled) setDuplicateOf(found ? canonical : null);
        },
        () => {
          if (!cancelled) setDuplicateOf(null);
        },
      );
    }, DUPLICATE_LOOKUP_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [canonical]);

  const duplicate = canonical !== null && duplicateOf === canonical;
  const ready = loadState.status === 'ready';
  // A fixed PN reads as Edit until its load says otherwise.
  const isEdit = record !== null || (fixedPn !== undefined && !ready);
  const title = readOnly
    ? 'Part Number details'
    : isEdit
      ? 'Edit Part Number'
      : 'New Part Number';

  const dirty =
    !readOnly &&
    (staged !== null ||
      (record
        ? !sameText(name, record.name) ||
          !sameText(revision, record.currentRevision) ||
          !sameText(erpId, record.erpId)
        : pnInput !== '' || name !== '' || revision !== '' || erpId !== ''));

  const close = () => {
    if (closed.current) return;
    closed.current = true;
    onClose({ wroteAny: wroteAny.current, exists: exists.current });
  };

  // Cancel, Escape and the backdrop are ignored while a save or delete
  // is in flight: its writes cannot be recalled, so the dialog stays
  // open until they settle.
  const requestClose = () => {
    if (busy) return;
    if (dirty) {
      setLeaveConfirm(true);
      return;
    }
    close();
  };

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

  const hasStoredImage = record?.imageUpdatedAt != null;
  const removeImage = () => {
    setImageError(null);
    setStaged(hasStoredImage ? { kind: 'remove' } : null);
  };

  const shownImage =
    staged?.kind === 'upload'
      ? staged.previewUrl
      : staged?.kind === 'remove' || !record
        ? undefined
        : (partNumberImageUrl(record) ?? undefined);
  const showRemove =
    staged?.kind === 'upload' || (hasStoredImage && staged === null);

  const submit = async () => {
    if (!ready || busy) return;
    if (fixedPn === undefined && canonical === null) {
      // Empty: the required-field copy; internal whitespace: the field
      // feedback already explains it.
      setPnAttempted(true);
      return;
    }
    if (fixedPn === undefined && duplicate) return;
    setBusy(true);
    setServerError(null);
    let step: 'details' | 'image' = 'details';
    let detailsChanged = false;
    try {
      // Step 1 — the details: create, or only the changed fields (an
      // image-only save sends `{}`, a no-op that proves the record
      // still exists).
      wroteAny.current = true;
      let saved: PartNumberMaster;
      if (record) {
        const patch: Partial<PartNumberDetails> = {};
        if (!sameText(name, record.name)) patch.name = storedText(name);
        if (!sameText(revision, record.currentRevision)) {
          patch.currentRevision = storedText(revision);
        }
        if (!sameText(erpId, record.erpId)) patch.erpId = storedText(erpId);
        saved = await updatePartNumber(record.partNumber, patch);
        detailsChanged =
          saved.name !== record.name ||
          saved.currentRevision !== record.currentRevision ||
          saved.erpId !== record.erpId;
      } else {
        // The raw trimmed entry (or the fixed PN): the server
        // canonicalizes it.
        saved = await createPartNumber(fixedPn ?? trimmed, {
          name: storedText(name),
          currentRevision: storedText(revision),
          erpId: storedText(erpId),
        });
        detailsChanged = true;
      }
      exists.current = true;
      setRecord(saved);
      setFixedPn(saved.partNumber);

      // Step 2 — the staged image change, its own audited write.
      step = 'image';
      if (staged?.kind === 'upload') {
        saved = await uploadPartNumberImage(saved.partNumber, staged.image);
      } else if (staged?.kind === 'remove') {
        saved = await removePartNumberImage(saved.partNumber);
      }
      setRecord(saved);
      setStaged(null);
      close();
    } catch (error) {
      if (!(error instanceof ApiError)) {
        // No answer: the write may or may not have committed.
        setServerError(UNKNOWN_OUTCOME_MESSAGE);
      } else if (error.status === 404) {
        // The saved details were deleted meanwhile: the dialog becomes
        // `New Part Number` with the PN fixed, input and staged image
        // kept — `Add Part Number` re-creates the record.
        exists.current = false;
        setRecord(null);
        setServerError(error.message);
      } else if (step === 'details' && isReleaseMismatch(error)) {
        // PartFlow was updated while this page was open: nothing was
        // changed, the dialog keeps its mode and input.
        setServerError(error.message);
      } else if (step === 'details' && error.status === 409) {
        // Someone created the details meanwhile, or a retried create
        // already committed (E1). Reload into Edit — fixing a typed PN
        // to its canonical form — keeping the entered values and the
        // staged image as unsaved edits against the loaded record.
        exists.current = true;
        setServerError(error.message);
        const target = fixedPn ?? canonical;
        if (target !== null) {
          setFixedPn(target);
          keepInputOnRetry.current = true;
          load(target, true);
        }
      } else if (step === 'details') {
        setServerError(error.message);
      } else if (detailsChanged) {
        setServerError(
          `The Part Number details were saved, but the image could not be updated: ${error.message}`,
        );
      } else {
        setServerError(`The image could not be updated: ${error.message}`);
      }
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!record || busy) return;
    setBusy(true);
    setServerError(null);
    wroteAny.current = true;
    try {
      await deletePartNumber(record.partNumber);
      exists.current = false;
      close();
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        // Already gone (a retried delete, or deleted elsewhere).
        exists.current = false;
        close();
        return;
      }
      setDeleteConfirm(false);
      setServerError(
        error instanceof ApiError ? error.message : UNKNOWN_OUTCOME_MESSAGE,
      );
      setBusy(false);
    }
  };

  const pnFeedback =
    fixedPn !== undefined ? null : trimmed && !canonical ? (
      <div className="err" role="alert">
        Part Number cannot contain spaces or other whitespace.
      </div>
    ) : duplicate ? (
      <div className="err" role="alert">
        Part Number “{canonical}” already has saved details.
      </div>
    ) : !trimmed && pnAttempted ? (
      <div className="err" role="alert">
        A Part Number is required.
      </div>
    ) : canonical ? (
      <div className="pnm-fieldok">
        ✓ Will be saved as <b>{canonical}</b> · Barcode{' '}
        <span className="barcodeval">{pnBarcode(canonical)}</span>
      </div>
    ) : null;

  const controlsBlocked = busy || preparing;
  const displayPn = fixedPn ?? canonical ?? '—';

  let formArea: ReactNode;
  if (loadState.status === 'loading') {
    formArea = <LoadingState label="Loading Part Number details" />;
  } else if (loadState.status === 'error') {
    formArea = (
      <ErrorState
        message="Part Number details could not be loaded."
        detail={loadState.message}
        onRetry={() => {
          if (fixedPn !== undefined) load(fixedPn, keepInputOnRetry.current);
        }}
      />
    );
  } else if (readOnly) {
    formArea = (
      <div className="pnm-form">
        <dl className="pnm-readonly">
          <div>
            <dt>Name / Description</dt>
            <dd>{record?.name ?? '—'}</dd>
          </div>
          <div>
            <dt>Revision</dt>
            <dd className="mono">{record?.currentRevision ?? '—'}</dd>
          </div>
          <div>
            <dt>ERP ID</dt>
            <dd className="mono">{record?.erpId ?? '—'}</dd>
          </div>
        </dl>
        <div className="pnm-imgblock">
          <span className="pnm-imglabel">Image</span>
          <div className="pnm-imgrow">
            <PnImage pn={displayPn} image={shownImage} />
          </div>
        </div>
      </div>
    );
  } else {
    formArea = (
      <div className="pnm-form">
        {fixedPn === undefined ? (
          <div className="pnm-fieldcol">
            <Field label="Part Number">
              <input
                ref={pnInputRef}
                className="field mono"
                value={pnInput}
                onChange={(e) => setPnInput(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="e.g. 2027-60-8114-01"
              />
            </Field>
            {pnFeedback}
          </div>
        ) : null}
        <Field
          label={
            <>
              Name / Description{' '}
              <span className="field-optional">(optional)</span>
            </>
          }
        >
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. BRACKET, MOUNTING SS 304, 2.50 X 4.00"
          />
        </Field>
        <div className="pnm-grid2">
          <Field
            label={
              <>
                Revision <span className="field-optional">(optional)</span>
              </>
            }
          >
            <input
              className="field mono"
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
              placeholder="e.g. C"
            />
          </Field>
          <Field
            label={
              <>
                ERP ID <span className="field-optional">(optional)</span>
              </>
            }
          >
            <input
              className="field mono"
              value={erpId}
              onChange={(e) => setErpId(e.target.value)}
              placeholder="e.g. ERP-PN-40412"
            />
          </Field>
        </div>
        <div className="pnm-imgblock">
          <span className="pnm-imglabel">
            Image <span className="field-optional">(optional)</span>
          </span>
          <div className="pnm-imgrow">
            <PnImage pn={displayPn} image={shownImage} />
            <div className="pnm-imgactions">
              <label className="pnm-upload">
                {shownImage ? 'Change image…' : 'Upload image…'}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  aria-label={shownImage ? 'Change image' : 'Upload image'}
                  disabled={controlsBlocked}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    void chooseImage(file);
                  }}
                />
              </label>
              {showRemove ? (
                <button
                  type="button"
                  className="pnm-imgremove"
                  disabled={controlsBlocked}
                  onClick={removeImage}
                >
                  Remove image
                </button>
              ) : null}
            </div>
          </div>
          <p className="pnm-imghelp">
            If no image is uploaded, the default Part Number image is shown.
          </p>
          {imageError ? (
            <div className="err" role="alert">
              {imageError}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <ModalDialog label={title} onClose={requestClose} size="wide">
      <div className="pnm-dlghead">
        <h3>{title}</h3>
        {dirty ? <span className="pnm-dirty">● Unsaved changes</span> : null}
      </div>
      {fixedPn !== undefined ? (
        <IdentityHeader pn={fixedPn} onOpenLabel={() => setLabelOpen(true)} />
      ) : null}
      {formArea}
      {/* The server's answer to the last write, in one place — it stays
          visible while the details reload (the E1 recovery). */}
      {serverError ? (
        <div className="pnm-form">
          <div className="err" role="alert">
            {serverError}
          </div>
        </div>
      ) : null}
      {readOnly ? (
        <div className="row">
          <button className="bigbtn ghost" onClick={close}>
            Close (Esc)
          </button>
        </div>
      ) : (
        <div className="row">
          <button
            className="bigbtn ghost"
            disabled={busy}
            onClick={requestClose}
          >
            Cancel (Esc)
          </button>
          <button
            className="bigbtn primary"
            disabled={
              writeBlocked ||
              controlsBlocked ||
              !ready ||
              (fixedPn === undefined && duplicate)
            }
            onClick={() => void submit()}
          >
            {isEdit ? 'Save changes' : 'Add Part Number'}
          </button>
        </div>
      )}
      {ready && record && !readOnly ? (
        <div className="pnm-dangerzone">
          <div className="dz-title">Delete Part Number Details</div>
          <div className="dz-body">
            <p className="dz-live">
              This removes the saved image, description, revision, and ERP ID
              for <b>{record.partNumber}</b>. Production tracking and Work Order
              history are not affected.
            </p>
            <button
              className="dz-delete"
              disabled={writeBlocked || controlsBlocked}
              onClick={() => setDeleteConfirm(true)}
            >
              Delete details…
            </button>
          </div>
        </div>
      ) : null}
      {labelOpen && fixedPn !== undefined ? (
        <PnBarcodeLabelDialog
          pn={fixedPn}
          onClose={() => setLabelOpen(false)}
        />
      ) : null}
      {deleteConfirm && record ? (
        // The final confirmation is the strongest warning in the flow:
        // the shared attention confirmation variant in the danger tone
        // (the Machines final-question presentation, §12.4) — still one
        // plain destructive confirmation, never a typed gate or an
        // extra step. The delete section above keeps its lighter
        // danger-zone treatment.
        <ConfirmDialog
          title="Delete Part Number details?"
          confirmLabel="Delete details"
          cancelLabel="Cancel (Esc)"
          tone="danger"
          confirmDisabled={writeBlocked || busy}
          onCancel={() => {
            if (!busy) setDeleteConfirm(false);
          }}
          onConfirm={() => void confirmDelete()}
        >
          This permanently removes the saved image, description, revision, and
          ERP ID for <b>{record.partNumber}</b>
          {dirty ? ' (unsaved edits are discarded with it)' : ''}. The Part
          Number and its production history remain available.
        </ConfirmDialog>
      ) : null}
      {leaveConfirm && record ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save changes"
          discardLabel="Discard changes"
          saveDisabled={writeBlocked}
          onCancel={() => setLeaveConfirm(false)}
          onSave={() => {
            setLeaveConfirm(false);
            void submit();
          }}
          onDiscard={close}
        >
          You have unsaved changes to <b>{record.partNumber}</b>.
        </UnsavedChoiceDialog>
      ) : null}
      {leaveConfirm && !record ? (
        <ConfirmDialog
          title="Discard new Part Number?"
          confirmLabel="Discard input"
          cancelLabel="Keep editing"
          onCancel={() => setLeaveConfirm(false)}
          onConfirm={close}
        >
          Your entered information will not be saved.
        </ConfirmDialog>
      ) : null}
    </ModalDialog>
  );
}
