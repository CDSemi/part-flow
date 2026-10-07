import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { loadAuditTrail } from '../../api/audit-trail';
import type {
  AuditTrailCursor,
  AuditTrailEntry,
  AuditTrailPage,
} from '../../api/audit-trail';
import { errorMessage } from '../../api/client';
import { userAvatarUrl } from '../../api/users';
import { Avatar } from '../../components/Avatar';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  allocationText,
  changeLines,
  kindLabel,
  reasonText,
  routeLines,
  subjectText,
} from './audit-trail-text';
import { timestamp } from './tracking-logic';

type FirstPage =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

interface Trail {
  entries: AuditTrailEntry[];
  /** From the latest page read. */
  total: number;
  next: AuditTrailCursor | null;
}

const entryKey = (entry: { source: string; id: number }) =>
  `${entry.source}:${entry.id}`;

/** The lines below an entry's headline: allocation facts, route steps,
 * recorded changes and the reason. */
function detailLines(entry: AuditTrailEntry): string[] {
  return [
    ...(entry.allocation
      ? [allocationText(entry.allocation, entry.subject)]
      : []),
    ...(entry.route ? routeLines(entry.route) : []),
    ...changeLines(entry),
    ...(entry.reason !== null ? [reasonText(entry.reason)] : []),
  ];
}

/** Who recorded the entry: avatar and name, the legacy text, or nothing. */
function EntryActor({ entry }: { entry: AuditTrailEntry }) {
  if (entry.actorUser !== null) {
    return (
      <>
        {' · '}
        <span className="tk-trail-actor">
          <Avatar
            name={entry.actorUser.displayName}
            size="sm"
            src={userAvatarUrl(entry.actorUser)}
          />
          {entry.actorUser.displayName}
        </span>
      </>
    );
  }
  if (entry.legacyActor !== null) {
    return (
      <>
        {' · '}
        <span className="tk-trail-actor">{entry.legacyActor}</span>
      </>
    );
  }
  return null;
}

function TrailEntry({ entry }: { entry: AuditTrailEntry }) {
  const subject = subjectText(entry.subject);
  return (
    <li>
      <div className="tk-trail-head">
        <span className="t">{timestamp(entry.occurredAt)}</span>
        {' · '}
        <b className="tk-trail-kind">{kindLabel(entry)}</b>
        {subject ? (
          <>
            {' · '}
            <span className="tk-trail-subject">{subject}</span>
          </>
        ) : null}
        <EntryActor entry={entry} />
      </div>
      {detailLines(entry).map((line, index) => (
        <div className="tk-trail-line" key={index}>
          {line}
        </div>
      ))}
    </li>
  );
}

/**
 * The read-only audit trail of ONE PN (GUI_DESIGN §7.4; Phase 14 slice
 * 7): its recorded changes newest first, paged like the other Tracking
 * histories. A snapshot read when opened — no polling; `Show older
 * entries` continues below the last entry delivered. Only the latest
 * request may present its answer. A read: it stays available while
 * disconnected (a failed read shows its error), and nothing in it can
 * be edited.
 */
export function AuditTrailDialog({
  pn,
  onClose,
}: {
  pn: string;
  onClose: () => void;
}) {
  const headingId = useId();
  const [first, setFirst] = useState<FirstPage>({ status: 'loading' });
  const [trail, setTrail] = useState<Trail>({
    entries: [],
    total: 0,
    next: null,
  });
  const [olderLoading, setOlderLoading] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  // Only the latest request may present its answer.
  const generation = useRef(0);
  // After an older page arrives focus stays on `Show older entries`, or
  // moves to `Close` once the last page removed that button.
  const [arrivals, setArrivals] = useState(0);
  const olderButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  const readFirst = useCallback(() => {
    const requested = ++generation.current;
    loadAuditTrail(pn).then(
      (page) => {
        if (generation.current !== requested) return;
        setTrail({
          entries: page.entries,
          total: page.total,
          next: page.hasMore ? page.next : null,
        });
        setOlderError(null);
        setOlderLoading(false);
        setFirst({ status: 'ready' });
      },
      (error: unknown) => {
        if (generation.current !== requested) return;
        setFirst({ status: 'error', message: errorMessage(error) });
      },
    );
  }, [pn]);

  useEffect(() => {
    readFirst();
    return () => {
      // A late answer never reaches a closed dialog.
      generation.current += 1;
    };
  }, [readFirst]);

  function retry() {
    setFirst({ status: 'loading' });
    readFirst();
  }

  function showOlder() {
    const before = trail.next;
    if (before === null) return;
    const requested = ++generation.current;
    setOlderLoading(true);
    setOlderError(null);
    loadAuditTrail(pn, before).then(
      (page: AuditTrailPage) => {
        if (generation.current !== requested) return;
        setTrail((current) => {
          // Defensive: the keyset never repeats an entry.
          const seen = new Set(current.entries.map(entryKey));
          return {
            entries: [
              ...current.entries,
              ...page.entries.filter((entry) => !seen.has(entryKey(entry))),
            ],
            total: page.total,
            next: page.hasMore ? page.next : null,
          };
        });
        setOlderLoading(false);
        setArrivals((count) => count + 1);
      },
      (error: unknown) => {
        if (generation.current !== requested) return;
        setOlderLoading(false);
        setOlderError(errorMessage(error));
      },
    );
  }

  useEffect(() => {
    if (arrivals === 0) return;
    (olderButton.current ?? closeButton.current)?.focus();
  }, [arrivals]);

  const shown = trail.entries.length;
  const hasOlder = trail.next !== null;

  let body: ReactNode;
  if (first.status === 'loading') {
    body = <LoadingState label="Loading the audit trail…" />;
  } else if (first.status === 'error') {
    body = <ErrorState message={first.message} onRetry={retry} />;
  } else if (trail.total === 0 && shown === 0) {
    body = <p className="tk-trail-empty">No recorded changes for {pn} yet.</p>;
  } else {
    body = (
      <>
        <ol className="tk-trail">
          {trail.entries.map((entry) => (
            <TrailEntry entry={entry} key={entryKey(entry)} />
          ))}
        </ol>
        <div className="tk-paging">
          <span>
            Showing <b>{shown}</b> of <b>{trail.total}</b> entries
            {!hasOlder && shown < trail.total
              ? ' — reopen the audit trail to include changes recorded since it was opened.'
              : ''}
          </span>
          {hasOlder ? (
            <button
              ref={olderButton}
              className="btn ghost"
              onClick={showOlder}
              disabled={olderLoading}
            >
              {olderLoading ? 'Loading…' : 'Show older entries'}
            </button>
          ) : null}
          {olderError ? (
            <span className="tk-error" role="alert">
              {olderError}
            </span>
          ) : null}
        </div>
      </>
    );
  }

  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={onClose}
      size="wide"
      className="tk-trail-dlg"
    >
      <h3 id={headingId}>Audit trail — {pn}</h3>
      <p className="tk-trail-intro">
        Recorded changes to this PN&apos;s details, its Work Orders, Work Order
        Demand and priority, its allocation corrections and its assigned routes
        — newest first. Production Movements, Undo included, are in the Movement
        history; nothing here can be edited.
      </p>
      {body}
      <div className="row">
        <button ref={closeButton} className="bigbtn ghost" onClick={onClose}>
          Close
        </button>
      </div>
    </ModalDialog>
  );
}
