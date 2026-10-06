import './part-numbers.css';

import { useCallback, useEffect, useState } from 'react';

import { listPartNumberPage, partNumberImageUrl } from '../../api/part-numbers';
import type { PartNumberMaster } from '../../api/part-numbers';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { EditPartNumberDialog } from '../../components/EditPartNumberDialog';
import { PnImage } from '../../components/PnImage';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { pnBarcode } from '../scan-station/barcode';

// Management → Part Numbers: the single place for saved Part Number
// details (GUI_DESIGN §14; PROJECT_PROFILE §8.1, §20, §21) — a REAL view
// on the `/api/part-numbers` surface since Phase 13. Access is
// permission-based like Machines and Planned Routes. The canonical PN
// string itself is the production identity — records here are optional
// current metadata only (image, name/description, informational
// revision, ERP mapping) and never gate production use: deleting a
// record touches nothing but the metadata, and every surface keeps
// showing the canonical PN.
//
// The list is a server-side bounded search (the PN Tracking pattern):
// the first page, `Show more` up to the cap, and an explicit line when
// more records exist than are listed. The list reloads only after the
// shared `Edit Part Number` dialog closes having written something, so
// a failing refresh never unmounts an open dialog.

/** First page size and the most rows the list ever shows. */
const PART_NUMBERS_PAGE_SIZE = 100;
const PART_NUMBERS_MAX_ROWS = 200;
const SEARCH_DEBOUNCE_MS = 250;

type PendingDialog = { kind: 'new' } | { kind: 'edit'; pn: string };

// Long-data preview records (?state=long, development builds only):
// many records plus one with an over-long PN, name/description and
// metadata, to exercise dense-table and truncation behavior. Never part
// of the server data — added to the rendered list only.
const LONG_PREVIEW_PART_NUMBERS: PartNumberMaster[] | null = import.meta.env.DEV
  ? [
      ...Array.from({ length: 15 }, (_, i): PartNumberMaster => {
        const n = i + 1;
        const pn = `0114-60-${String(100 + n).padStart(4, '0')}-00`;
        return {
          partNumber: pn,
          barcodeValue: pnBarcode(pn),
          name: `LONG PREVIEW PART ${n} — AUTO-GENERATED SAMPLE FOR LAYOUT TESTING ONLY`,
          currentRevision: String.fromCharCode(65 + (n % 6)),
          erpId: `ERP-PN-LONG-${String(90000 + n)}`,
          imageUpdatedAt: null,
        };
      }),
      {
        partNumber: '0118-40-0022-07-0455-88-REV-C-SUPPLEMENTAL-LONG-PREVIEW',
        barcodeValue: pnBarcode(
          '0118-40-0022-07-0455-88-REV-C-SUPPLEMENTAL-LONG-PREVIEW',
        ),
        name: 'SUPPLEMENTAL LONG-PREVIEW PART NUMBER, MULTI-STAGE HOUSING ASSEMBLY WITH OUTSIDE PLATING AND SECONDARY DEBURR OPERATION — OVER-LONG NAME FOR LAYOUT TESTING',
        currentRevision: 'REV-SUPPLEMENTAL-LONG',
        erpId: 'ERP-PN-40412-SUPPLEMENTAL-AMENDMENT-2026-REV-B-LONG-PREVIEW',
        imageUpdatedAt: null,
      },
    ]
  : null;

export function PartNumbersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [limit, setLimit] = useState(PART_NUMBERS_PAGE_SIZE);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  // The search field reaches the server debounced; a new search starts
  // again from the first page. An unchanged search arms no timer, so it
  // never resets a `Show more` limit (on mount, or after typing back to
  // the applied search).
  useEffect(() => {
    if (search === debouncedSearch) return;
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search);
      setLimit(PART_NUMBERS_PAGE_SIZE);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search, debouncedSearch]);

  const loadPage = useCallback(
    () => listPartNumberPage(debouncedSearch, limit),
    [debouncedSearch, limit],
  );
  const pageData = useApiData(loadPage);

  const closeDialog = (result: { wroteAny: boolean }) => {
    setDialog(null);
    if (result.wroteAny) pageData.reload();
  };

  if (preview === 'loading' || pageData.state.status === 'loading') {
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
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }
  if (pageData.state.status === 'error') {
    return (
      <section className="pnm" aria-label="Part Numbers">
        <ErrorState
          message="Part Number data could not be loaded."
          detail={pageData.state.message}
          onRetry={pageData.reload}
        />
      </section>
    );
  }

  const page = pageData.state.data;
  const longRows =
    preview === 'long' && LONG_PREVIEW_PART_NUMBERS
      ? LONG_PREVIEW_PART_NUMBERS
      : [];
  const rows = preview === 'empty' ? [] : [...page.rows, ...longRows];
  const total = page.total + longRows.length;
  const searched = debouncedSearch.trim();

  return (
    <section className="pnm" aria-label="Part Numbers">
      <h1>Part Numbers</h1>
      <p className="pnm-sub">
        Manage optional Part Number details, images, ERP IDs, and barcode
        labels.
      </p>
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
          disabled={writeBlocked}
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Part Number
        </button>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          message={
            searched
              ? `No saved Part Number details match “${searched}”.`
              : 'No Part Number details have been added yet.'
          }
        />
      ) : (
        <>
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
              {rows.map((record) => (
                // The COMPLETE row opens Edit Part Number (the Machines/
                // Tracking whole-row pattern): the PN-cell button is the
                // keyboard and screen-reader entry point — its activation
                // bubbles to this row handler.
                <tr
                  key={record.partNumber}
                  className="selrow"
                  onClick={() =>
                    setDialog({ kind: 'edit', pn: record.partNumber })
                  }
                >
                  <td className="pnm-imgcol">
                    <PnImage
                      pn={record.partNumber}
                      image={partNumberImageUrl(record) ?? undefined}
                      size="sm"
                    />
                  </td>
                  <td>
                    <button
                      className="rowbtn"
                      aria-label={`Edit ${record.partNumber}`}
                    >
                      <span className="pnm-pn">{record.partNumber}</span>
                    </button>
                  </td>
                  <td className="pnm-name">{record.name ?? '—'}</td>
                  <td className="pnm-meta">{record.currentRevision ?? '—'}</td>
                  <td className="pnm-meta mono">{record.erpId ?? '—'}</td>
                  <td>
                    <span className="barcodeval">
                      {pnBarcode(record.partNumber)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pnm-paging" role="status">
            <span>
              Showing <b>{rows.length}</b> of <b>{total}</b> Part Numbers
            </span>
            {page.hasMore && limit < PART_NUMBERS_MAX_ROWS ? (
              <button
                className="btn ghost"
                onClick={() => setLimit(PART_NUMBERS_MAX_ROWS)}
              >
                Show more
              </button>
            ) : page.hasMore ? (
              <span>
                Only the first {PART_NUMBERS_MAX_ROWS} are listed — narrow the
                search to find the rest.
              </span>
            ) : null}
          </div>
        </>
      )}
      {dialog ? (
        <EditPartNumberDialog
          pn={dialog.kind === 'edit' ? dialog.pn : undefined}
          writeBlocked={writeBlocked}
          onClose={closeDialog}
        />
      ) : null}
    </section>
  );
}
