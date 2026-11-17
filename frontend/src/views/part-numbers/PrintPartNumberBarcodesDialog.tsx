import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import {
  listPartNumberPage,
  listRunningWorkOrders,
} from '../../api/part-numbers';
import type { PartNumberSort } from '../../api/part-numbers';
import { useApiData } from '../../api/use-api-data';
import { Code128Svg } from '../../components/Code128Svg';
import { PnBarcodeLabel } from '../../components/PnBarcodeLabelDialog';
import { PrintBarcodesDialog } from '../../components/PrintBarcodesDialog';
import type { BarcodeSection } from '../../components/print-barcodes';
import { ErrorState, LoadingState } from '../../components/view-states';
import { pnBarcode } from '../scan-station/barcode';
import {
  PART_NUMBERS_MAX_ROWS,
  PART_NUMBERS_PAGE_SIZE,
  PART_NUMBER_SORT_OPTIONS,
  SEARCH_DEBOUNCE_MS,
  localDate,
} from './part-number-list';

/** What a card knows about a PN besides the PN itself. */
interface PnCardInfo {
  name: string | null;
  createdAt?: string;
  lastUsedAt?: string | null;
}

/**
 * Print barcodes on Management → Part Numbers (GUI_DESIGN §14.5).
 *
 * `Group by running Work Order` on (the default): one group per running
 * Work Order — released and not completed, the Work Orders the Work
 * Orders list shows as Released — with the PNs of its demand lines. A
 * PN on several Work Orders is one selection, shown selected in every
 * group, and prints once.
 *
 * Off: the saved Part Numbers as one flat list with the bounded
 * server-side search of the Part Numbers list and a sort. The selection
 * survives search, sort and grouping changes; `Print Labels` prints
 * every selected PN, shown or not. The printed label is the single
 * `Part Number barcode label`.
 */
export function PrintPartNumberBarcodesDialog({
  onClose,
}: {
  onClose: () => void;
}) {
  const [grouped, setGrouped] = useState(true);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [sort, setSort] = useState<PartNumberSort>('part_number');
  const [limit, setLimit] = useState(PART_NUMBERS_PAGE_SIZE);
  // The Part Numbers list search: debounced, back to the first page.
  useEffect(() => {
    if (search === debouncedSearch) return;
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search);
      setLimit(PART_NUMBERS_PAGE_SIZE);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search, debouncedSearch]);

  const running = useApiData(listRunningWorkOrders);
  const loadPage = useCallback(
    () =>
      grouped
        ? Promise.resolve(null)
        : listPartNumberPage(debouncedSearch, limit, sort),
    [grouped, debouncedSearch, limit, sort],
  );
  const page = useApiData(loadPage);

  const runningData =
    running.state.status === 'ready' ? running.state.data : null;
  const pageData = page.state.status === 'ready' ? page.state.data : null;

  // What the shown cards know besides the PN: the name (both lists) and
  // the two dates (the flat list).
  const known = new Map<string, PnCardInfo>();
  for (const wo of grouped ? (runningData ?? []) : []) {
    for (const part of wo.partNumbers) {
      known.set(part.partNumber, { name: part.name });
    }
  }
  for (const row of grouped ? [] : (pageData?.rows ?? [])) {
    known.set(row.partNumber, {
      name: row.name,
      createdAt: row.createdAt,
      lastUsedAt: row.lastUsedAt,
    });
  }

  let sections: BarcodeSection[] = [];
  let body: ReactNode = undefined;
  let footerNote: ReactNode = undefined;
  let emptyMessage: string;
  if (grouped) {
    emptyMessage = 'No Work Order is running right now.';
    if (running.state.status === 'error') {
      body = (
        <ErrorState
          message="Running Work Orders could not be loaded."
          detail={running.state.message}
          onRetry={running.reload}
        />
      );
    } else if (!runningData) {
      body = <LoadingState label="Loading running Work Orders" />;
    } else {
      sections = runningData.map((wo) => {
        const number = wo.workOrderNumber ?? '—';
        return {
          key: `wo-${wo.id}`,
          title: (
            <>
              WO {number}
              {wo.dueDate ? (
                <span className="meta">Due {wo.dueDate}</span>
              ) : null}
            </>
          ),
          titleText: `WO ${number}`,
          itemKeys: wo.partNumbers.map((part) => part.partNumber),
        };
      });
    }
  } else {
    const searched = debouncedSearch.trim();
    emptyMessage = searched
      ? `No saved Part Number details match “${searched}”.`
      : 'No Part Number details have been added yet.';
    if (page.state.status === 'error') {
      body = (
        <ErrorState
          message="Part Number data could not be loaded."
          detail={page.state.message}
          onRetry={page.reload}
        />
      );
    } else if (!pageData) {
      body = <LoadingState label="Loading Part Numbers" />;
    } else {
      sections = [
        { key: 'all', itemKeys: pageData.rows.map((row) => row.partNumber) },
      ];
      footerNote =
        pageData.rows.length > 0 ? (
          <div className="pbx-more" role="status">
            <span>
              Showing <b>{pageData.rows.length}</b> of <b>{pageData.total}</b>{' '}
              Part Numbers
            </span>
            {pageData.hasMore && limit < PART_NUMBERS_MAX_ROWS ? (
              <button
                type="button"
                className="btn ghost"
                onClick={() => setLimit(PART_NUMBERS_MAX_ROWS)}
              >
                Show more
              </button>
            ) : pageData.hasMore ? (
              <span>
                Only the first {PART_NUMBERS_MAX_ROWS} are listed — narrow the
                search to find the rest.
              </span>
            ) : null}
          </div>
        ) : null;
    }
  }

  /** The date line of the active date sort, on the flat list only. */
  const dateLine = (info: PnCardInfo | undefined): string | null => {
    if (grouped || !info) return null;
    if (sort === 'added' && info.createdAt) {
      return `Added ${localDate(info.createdAt)}`;
    }
    if (sort === 'last_used' && info.lastUsedAt !== undefined) {
      return info.lastUsedAt
        ? `Last used ${localDate(info.lastUsedAt)}`
        : 'Not used in a Work Order yet';
    }
    return null;
  };

  return (
    <PrintBarcodesDialog
      title="Print Part Number barcodes"
      description="Select the Part Number labels to print. Labels fill the page left to right, then row by row."
      controls={
        <div className="pbx-controls">
          <button
            type="button"
            role="switch"
            aria-checked={grouped}
            className={`pbx-switch${grouped ? ' on' : ''}`}
            onClick={() => setGrouped((value) => !value)}
          >
            <span className="knob" aria-hidden="true" />
            Group by running Work Order
          </button>
          {grouped ? null : (
            <>
              <input
                type="search"
                placeholder="Search: PN, name, revision, ERP id…"
                aria-label="Search Part Numbers"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <select
                aria-label="Sort Part Numbers"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as PartNumberSort);
                  setLimit(PART_NUMBERS_PAGE_SIZE);
                }}
              >
                {PART_NUMBER_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      }
      sections={sections}
      body={body}
      footerNote={footerNote}
      emptyMessage={emptyMessage}
      cardName={(pn) => {
        const name = known.get(pn)?.name;
        return name ? `${pn} · ${name}` : pn;
      }}
      renderCard={(pn) => {
        const info = known.get(pn);
        const value = pnBarcode(pn);
        const date = dateLine(info);
        return (
          <>
            <Code128Svg className="pbx-lbars lead" value={value} />
            <div className="pbx-ltitle mono">{pn}</div>
            <div className="pbx-lvalue">{value}</div>
            {info?.name ? <div className="pbx-lnote">{info.name}</div> : null}
            {date ? <div className="pbx-lnote faint">{date}</div> : null}
          </>
        );
      }}
      renderLabel={(pn) => <PnBarcodeLabel pn={pn} />}
      onClose={onClose}
    />
  );
}
