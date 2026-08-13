import './part-numbers.css';

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { DevNotice } from '../../components/DevNotice';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { PnImage } from '../../components/PnImage';
import { UnsavedChoiceDialog } from '../../components/UnsavedChoiceDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { MOCK_PART_NUMBERS } from '../../mocks/part-numbers';
import { code128ModuleCount, encodeCode128B } from '../code128';
import { normalizePartNumber, pnBarcode } from '../scan-station/barcode';
import type { MockPartNumberMaster } from '../view-models';

// Management → Part Numbers: the single place for PartNumber master
// metadata (GUI_DESIGN §14; PROJECT_PROFILE §8.1, §20, §21). Access is
// permission-based like Machines and Planned Routes. The canonical PN
// string itself is the production identity — records here are optional
// current metadata only (image, name/description, informational
// revision, ERP mapping) and never gate production use: deleting a
// record touches nothing but the metadata, and every surface keeps
// showing the canonical PN.

type PendingDialog = { kind: 'new' } | { kind: 'edit'; pn: string };

export function PartNumbersView() {
  const preview = getViewStatePreview();
  const [records, setRecords] =
    useState<MockPartNumberMaster[]>(MOCK_PART_NUMBERS);
  const [search, setSearch] = useState('');
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  if (preview === 'loading') {
    return (
      <section className="pnm" aria-label="Part Numbers">
        <LoadingState label="Loading Part Numbers" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="pnm" aria-label="Part Numbers">
        <ErrorState
          message="Part Number data could not be loaded."
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const query = search.trim().toLowerCase();
  const matches = (record: MockPartNumberMaster): boolean =>
    !query ||
    [record.pn, record.name ?? '', record.revision ?? '', record.erpId ?? '']
      .join(' ')
      .toLowerCase()
      .includes(query);
  const visible = preview === 'empty' ? [] : records.filter(matches);

  const editRecord =
    dialog?.kind === 'edit'
      ? records.find((record) => record.pn === dialog.pn)
      : undefined;

  return (
    <section className="pnm" aria-label="Part Numbers">
      <h1>Part Numbers</h1>
      <p className="pnm-sub">
        PartNumber records — image, name, revision, and ERP mapping for
        canonical Part Numbers. The Part Number itself is the identity; a PN
        stays usable with or without a record here.
      </p>
      <DevNotice>
        Development preview — Part Number records shown are sample data and
        changes affect only this preview.
      </DevNotice>
      <div className="pnm-toolbar">
        <input
          type="search"
          placeholder="Search: PN, name, revision, ERP id…"
          aria-label="Search Part Numbers"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="spacer" />
        <button
          className="btn primary"
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Part Number
        </button>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          message={
            query
              ? `No Part Numbers match “${search.trim()}”.`
              : 'No Part Number records yet.'
          }
        />
      ) : (
        <table className="pnm-table">
          <thead>
            <tr>
              <th className="pnm-imgcol">Image</th>
              <th>Part Number</th>
              <th>Name / Description</th>
              <th>Revision</th>
              <th>ERP ID</th>
              <th>Barcode</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((record) => (
              // The COMPLETE row opens Edit Part Number (the Machines/
              // Tracking whole-row pattern): the PN-cell button is the
              // keyboard and screen-reader entry point — its activation
              // bubbles to this row handler.
              <tr
                key={record.pn}
                className="selrow"
                onClick={() => setDialog({ kind: 'edit', pn: record.pn })}
              >
                <td className="pnm-imgcol">
                  <PnImage pn={record.pn} image={record.image} size="sm" />
                </td>
                <td>
                  <button className="rowbtn" aria-label={`Edit ${record.pn}`}>
                    <span className="pnm-pn">{record.pn}</span>
                  </button>
                </td>
                <td className="pnm-name">{record.name || '—'}</td>
                <td className="pnm-meta">{record.revision ?? '—'}</td>
                <td className="pnm-meta mono">{record.erpId ?? '—'}</td>
                <td>
                  <span className="barcodeval">{pnBarcode(record.pn)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <PageNote>
        Deleting a Part Number record removes only this metadata. Production
        quantities, Work Order demand and Movement history are never touched and
        keep showing the PN; a record can be created again later for the same
        PN.
      </PageNote>

      {dialog?.kind === 'new' ? (
        <PartNumberEditDialog
          records={records}
          onCancel={() => setDialog(null)}
          onSave={(record) => {
            setRecords((current) => [...current, record]);
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' && editRecord ? (
        <PartNumberEditDialog
          records={records}
          record={editRecord}
          onCancel={() => setDialog(null)}
          onSave={(record) => {
            setRecords((current) =>
              current.map((r) => (r.pn === record.pn ? record : r)),
            );
            setDialog(null);
          }}
          onDelete={() => {
            setRecords((current) =>
              current.filter((r) => r.pn !== editRecord.pn),
            );
            setDialog(null);
          }}
        />
      ) : null}
    </section>
  );
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
 * Read-only Part Number identity header at the top of the Edit dialog
 * (the Machines §12.3 idiom): the canonical PN and the barcode derived
 * from it — one value in the PF:PN: namespace, never an independently
 * editable field — plus the entry to the printable barcode label.
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
 * Printable PN barcode label: the Code 128 barcode of the scanned
 * value (`PF:PN:<part-number>`) with the PN text beneath it, plus the
 * full value as the quiet verification line. Print Label prints
 * exactly the label area (print styles hide the rest of the page).
 * Deliberately simple — no barcode configuration exists here.
 */
function BarcodeLabelDialog({
  pn,
  onClose,
}: {
  pn: string;
  onClose: () => void;
}) {
  const value = pnBarcode(pn);
  const runs = encodeCode128B(value);
  const quiet = 10;
  const moduleWidth = 2;
  const barHeight = 64;
  const totalModules = runs ? code128ModuleCount(runs) + quiet * 2 : 0;
  let x = quiet;
  return (
    <ModalDialog label="Part Number barcode label" onClose={onClose}>
      <h3>Part Number barcode label</h3>
      <div className="sub">
        The barcode carries the Part Number itself — scanning it identifies{' '}
        <b>{pn}</b>.
      </div>
      <div className="pnm-label pnm-labelprint">
        {runs ? (
          <svg
            className="lbarcode"
            viewBox={`0 0 ${totalModules * moduleWidth} ${barHeight}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={`Barcode ${value}`}
          >
            {runs.map((run, index) => {
              const rect = run.bar ? (
                <rect
                  key={index}
                  x={x * moduleWidth}
                  y={0}
                  width={run.width * moduleWidth}
                  height={barHeight}
                />
              ) : null;
              x += run.width;
              return rect;
            })}
          </svg>
        ) : null}
        <div className="lpn">{pn}</div>
        <div className="lvalue">{value}</div>
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={() => window.print()}>
          Print Label
        </button>
      </div>
    </ModalDialog>
  );
}

/**
 * Add or edit one PartNumber master record. The PN is entered once at
 * creation (canonicalized like every PN entry path: trimmed, internal
 * whitespace rejected — never silently removed — and uppercased) and
 * is never edited afterwards — the canonical PN is the identity and
 * the barcode always derives from it. All metadata stays optional.
 * Editing hosts the Danger Zone: Delete… hard-deletes only this
 * metadata record behind a plain destructive confirmation.
 */
function PartNumberEditDialog({
  records,
  record,
  onCancel,
  onSave,
  onDelete,
}: {
  records: MockPartNumberMaster[];
  record?: MockPartNumberMaster;
  onCancel: () => void;
  onSave: (record: MockPartNumberMaster) => void;
  onDelete?: () => void;
}) {
  const initial = {
    pnInput: record?.pn ?? '',
    name: record?.name ?? '',
    revision: record?.revision ?? '',
    erpId: record?.erpId ?? '',
    image: record?.image,
  };
  const [pnInput, setPnInput] = useState(initial.pnInput);
  const [name, setName] = useState(initial.name);
  const [revision, setRevision] = useState(initial.revision);
  const [erpId, setErpId] = useState(initial.erpId);
  const [image, setImage] = useState<string | undefined>(initial.image);
  const [pnAttempted, setPnAttempted] = useState(false);
  const [labelOpen, setLabelOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [leaveConfirm, setLeaveConfirm] = useState(false);

  // A NEW record starts with the PN focused — the one required field.
  // Edit keeps the dialog-root focus (the whole record is equally
  // editable).
  const pnInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!record) pnInputRef.current?.focus();
    // Initial focus only — `record` never changes while open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty =
    pnInput !== initial.pnInput ||
    name !== initial.name ||
    revision !== initial.revision ||
    erpId !== initial.erpId ||
    image !== initial.image;

  // PN entry feedback (new record only): canonicalization mirrors
  // every other PN entry path — trimmed, internal whitespace rejected
  // with an inline explanation, uppercased. One master record may
  // exist per canonical PN.
  const trimmed = pnInput.trim();
  const canonical = normalizePartNumber(pnInput);
  const duplicate =
    !record && canonical !== null
      ? records.some((r) => r.pn === canonical)
      : false;
  const pnFeedback = record ? null : trimmed && !canonical ? (
    <div className="err" role="alert">
      ✕ A Part Number cannot contain spaces or other whitespace inside the
      value, so “{trimmed}” cannot be created.
    </div>
  ) : duplicate ? (
    <div className="err" role="alert">
      ✕ A record for “{canonical}” already exists — edit it instead.
    </div>
  ) : !trimmed && pnAttempted ? (
    <div className="err" role="alert">
      A Part Number is required.
    </div>
  ) : canonical ? (
    <div className="pnm-fieldok">
      ✓ Canonical Part Number <b>{canonical}</b> · barcode{' '}
      <span className="barcodeval">{pnBarcode(canonical)}</span>
    </div>
  ) : null;

  const build = (): MockPartNumberMaster | null => {
    const pn = record?.pn ?? canonical;
    if (!pn || duplicate) return null;
    return {
      ...(record ?? {}),
      pn,
      name: name.trim() || undefined,
      revision: revision.trim() || undefined,
      erpId: erpId.trim() || undefined,
      image,
    };
  };

  const save = () => {
    if (!record && !canonical) {
      setPnAttempted(true);
      return;
    }
    const built = build();
    if (!built) return; // covered by the field feedback
    onSave(built);
  };

  /** Close request: unsaved input asks for an explicit decision. */
  const requestClose = () => {
    if (dirty) {
      setLeaveConfirm(true);
      return;
    }
    onCancel();
  };

  const uploadFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') setImage(reader.result);
    };
    reader.readAsDataURL(file);
  };

  const built = build();
  const displayPn = record?.pn ?? canonical ?? undefined;

  return (
    <ModalDialog
      label={record ? 'Edit Part Number' : 'New Part Number'}
      onClose={requestClose}
      size="wide"
    >
      <div className="pnm-dlghead">
        <h3>{record ? 'Edit Part Number' : 'New Part Number'}</h3>
        {dirty ? <span className="pnm-dirty">● Unsaved changes</span> : null}
      </div>
      {record ? (
        <IdentityHeader pn={record.pn} onOpenLabel={() => setLabelOpen(true)} />
      ) : null}
      <div className="pnm-form">
        {!record ? (
          <div className="pnm-fieldcol">
            <Field label="Part Number">
              <input
                ref={pnInputRef}
                className="field mono"
                value={pnInput}
                onChange={(e) => setPnInput(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="e.g. 2027-60-8114-00"
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
            <PnImage pn={displayPn ?? '—'} image={image} />
            <div className="pnm-imgactions">
              <label className="pnm-upload">
                {image ? 'Change image…' : 'Upload image…'}
                <input
                  type="file"
                  accept="image/*"
                  aria-label={image ? 'Change image' : 'Upload image'}
                  onChange={(e) => {
                    uploadFile(e.target.files?.[0]);
                    e.target.value = '';
                  }}
                />
              </label>
              {image ? (
                <button
                  type="button"
                  className="pnm-imgremove"
                  onClick={() => setImage(undefined)}
                >
                  Remove image
                </button>
              ) : null}
            </div>
          </div>
          <p className="pnm-imghelp">
            Without a custom image the shared default Part Number placeholder is
            shown — the same one as in PN Tracking.
          </p>
        </div>
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={requestClose}>
          Cancel (Esc)
        </button>
        <button className="bigbtn primary" onClick={save}>
          {record ? 'Save changes' : 'Add Part Number'}
        </button>
      </div>
      {record && onDelete ? (
        <div className="pnm-dangerzone">
          <div className="dz-title">Danger Zone</div>
          <div className="dz-body">
            <p className="dz-live">
              Deleting <b>{record.pn}</b> removes only this metadata record.
              Production quantities, Work Order demand and Movement history are
              never touched and keep showing the PN.
            </p>
            <button
              className="dz-delete"
              onClick={() => setDeleteConfirm(true)}
            >
              Delete…
            </button>
          </div>
        </div>
      ) : null}
      {labelOpen && record ? (
        <BarcodeLabelDialog
          pn={record.pn}
          onClose={() => setLabelOpen(false)}
        />
      ) : null}
      {deleteConfirm && record ? (
        <ConfirmDialog
          title="Delete Part Number record"
          confirmLabel="Delete record"
          cancelLabel="Cancel (Esc)"
          danger
          onCancel={() => setDeleteConfirm(false)}
          onConfirm={() => onDelete?.()}
        >
          Only the metadata record for <b>{record.pn}</b> is deleted
          {dirty ? ' (unsaved edits are discarded with it)' : ''}: Work Order
          demand, production quantities, Movement history and allocations are
          never touched, and every screen keeps showing the Part Number with its
          record fields absent (—). A record can be created again later for the
          same PN.
        </ConfirmDialog>
      ) : null}
      {leaveConfirm && record ? (
        <UnsavedChoiceDialog
          title="Unsaved changes"
          saveLabel="Save changes"
          discardLabel="Discard changes"
          onCancel={() => setLeaveConfirm(false)}
          onSave={() => {
            if (!built) return;
            onSave(built);
          }}
          onDiscard={onCancel}
        >
          This Part Number record has unsaved edits. <b>Save changes</b> saves
          them and closes, <b>Discard changes</b> closes without saving them.
        </UnsavedChoiceDialog>
      ) : null}
      {leaveConfirm && !record ? (
        <ConfirmDialog
          title="Discard new Part Number?"
          confirmLabel="Discard input"
          cancelLabel="Keep editing"
          onCancel={() => setLeaveConfirm(false)}
          onConfirm={onCancel}
        >
          Nothing has been added yet — closing now discards the entered input.
        </ConfirmDialog>
      ) : null}
    </ModalDialog>
  );
}
