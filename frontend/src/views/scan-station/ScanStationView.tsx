import './scan-station.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { useConnectivity } from '../../app/connectivity';
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { LoadingState } from '../../components/view-states';
import {
  MOCK_INVENTORY_ASSIGNED,
  MOCK_INVENTORY_QUEUED,
  MOCK_INVENTORY_QUEUED_LONG,
  MOCK_MACHINES,
  MOCK_RECENT_SCANS,
  MOCK_SCAN_OUTCOMES,
  MOCK_STATION,
} from '../../mocks/scan-station';
import type { MockScanRecord } from '../../mocks/types';

type Feedback = {
  kind: 'idle' | 'ok' | 'warn' | 'err';
  icon?: string;
  title: string;
  detail?: string;
};

const IDLE_FEEDBACK: Feedback = { kind: 'idle', title: 'Ready to scan…' };

// Mock-only "clock" for newly added list rows — deterministic on purpose.
const MOCK_SCAN_TIME = '14:32';

export function ScanStationView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const disconnected = status === 'unavailable';
  const writeBlocked = status !== 'connected';

  const inputRef = useRef<HTMLInputElement>(null);
  const [machineSession, setMachineSession] = useState<string | null>(null);
  const [worker, setWorker] = useState(MOCK_STATION.worker);
  const [pending, setPending] = useState<string | null>(null);
  const [lastPn, setLastPn] = useState<{ pn: string; desc: string } | null>(
    null,
  );
  const [feedback, setFeedback] = useState<Feedback>(IDLE_FEEDBACK);
  const [scans, setScans] = useState<MockScanRecord[]>(MOCK_RECENT_SCANS);
  const [undoCount, setUndoCount] = useState(0);
  const [qtyDialog, setQtyDialog] = useState<{
    pn: string;
    available: number;
  } | null>(null);
  const [qtyValue, setQtyValue] = useState('');
  const [ambiguityPn, setAmbiguityPn] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  const focusScan = useCallback(() => {
    // §3.1 focus discipline: the barcode input regains focus after every
    // completed operation and dialog close.
    setTimeout(() => inputRef.current?.focus(), 30);
  }, []);

  useEffect(() => {
    if (!writeBlocked) focusScan();
  }, [writeBlocked, focusScan]);

  const addScan = useCallback((record: MockScanRecord, undoable: boolean) => {
    setScans((current) => [record, ...current]);
    if (undoable) setUndoCount((n) => n + 1);
  }, []);

  const blockedFeedback = useCallback(() => {
    setFeedback({
      kind: 'err',
      icon: '✕',
      title: 'Disconnected — production writes are blocked',
      detail:
        'No scan is recorded or queued while offline. Restore the connection to resume scanning.',
    });
  }, []);

  function handleScan() {
    const input = inputRef.current;
    if (!input) return;
    if (writeBlocked) {
      blockedFeedback();
      return;
    }
    const raw = input.value.trim();
    input.value = '';
    focusScan();
    if (!raw) return;
    const outcome = MOCK_SCAN_OUTCOMES[raw.toUpperCase()];
    if (!outcome) {
      setFeedback({
        kind: 'err',
        icon: '✕',
        title: raw.toUpperCase().startsWith('PF:PN:')
          ? 'Unknown PN barcode'
          : 'Unrecognized barcode',
        detail:
          'Rejected — unknown or invalid scans never update tracking data. Nothing recorded (mock).',
      });
      return;
    }
    switch (outcome.kind) {
      case 'machine':
        setMachineSession(outcome.machine);
        if (pending) {
          setPending(null);
          addScan(
            {
              pn: 'PF-BRACKET-001',
              description: `ASSIGNED_TO_MACHINE · Lathe queue → ${outcome.machine} (mock)`,
              time: MOCK_SCAN_TIME,
            },
            true,
          );
          setFeedback({
            kind: 'ok',
            icon: '✓',
            title: `Assigned to ${outcome.machine}`,
            detail:
              'ASSIGNED_TO_MACHINE recorded (mock presentation only). Machine session active.',
          });
        } else {
          setFeedback({
            kind: 'ok',
            icon: '✓',
            title: `Machine session: ${outcome.machine}`,
            detail:
              'Session persists until changed, cleared or expired — next PN scans assign here.',
          });
        }
        break;
      case 'machine-inactive':
        setFeedback({
          kind: 'err',
          icon: '✕',
          title: `${outcome.machine} is inactive (maintenance)`,
          detail: 'Inactive entities must not accept production updates.',
        });
        break;
      case 'worker':
        setWorker(outcome.worker);
        setFeedback({
          kind: 'ok',
          icon: '✓',
          title: `Worker session: ${outcome.worker}`,
          detail:
            'Previous Worker signed out. Subsequent scans record this Worker.',
        });
        break;
      case 'action':
        setFeedback({
          kind: 'warn',
          icon: '!',
          title: `${outcome.action} armed for next PN scan`,
          detail:
            'Scan a PN barcode next — order is flexible (PN → Action ≡ Action → PN). Confirmation follows.',
        });
        break;
      case 'pn-single':
        setLastPn({ pn: outcome.pn, desc: outcome.description });
        setQtyValue('');
        setQtyDialog({ pn: outcome.pn, available: 8 });
        break;
      case 'pn-ambiguous':
        setLastPn({
          pn: outcome.pn,
          desc: 'Mounting bracket · multiple valid contexts',
        });
        setAmbiguityPn(outcome.pn);
        break;
    }
  }

  function confirmQty() {
    if (!qtyDialog) return;
    const qty = parseInt(qtyValue || '0', 10);
    if (!qty) return;
    const { pn, available } = qtyDialog;
    setQtyDialog(null);
    if (qty > available) {
      setFeedback({
        kind: 'err',
        icon: '✕',
        title: 'Quantity exceeds available source quantity',
        detail: `Requested ${qty} > available ${available} at Material. Movement rejected (quantity integrity).`,
      });
      focusScan();
      return;
    }
    if (machineSession) {
      addScan(
        {
          pn,
          description: `TRANSFERRED · Material → Lathe · qty ${qty} (mock)`,
          time: MOCK_SCAN_TIME,
        },
        true,
      );
      setFeedback({
        kind: 'ok',
        icon: '✓',
        title: `${pn} × ${qty} → ${machineSession}`,
        detail:
          'Mock presentation of TRANSFERRED + ASSIGNED_TO_MACHINE — no production write occurred.',
      });
    } else {
      addScan(
        {
          pn,
          description: `TRANSFERRED · Material → Lathe queue · qty ${qty} (mock)`,
          time: MOCK_SCAN_TIME,
        },
        true,
      );
      setPending(
        `Pending context: ${pn} × ${qty} received into Lathe queue — scan a Machine barcode to assign, or continue scanning.`,
      );
      setFeedback({
        kind: 'ok',
        icon: '✓',
        title: `${pn} × ${qty} → Lathe queue`,
        detail:
          'Received into Area queue (mock). Scan a Machine to assign — either order works.',
      });
    }
    focusScan();
  }

  function cancelDialog() {
    setQtyDialog(null);
    setAmbiguityPn(null);
    setManualOpen(false);
    setFeedback({ kind: 'idle', title: 'Cancelled — nothing recorded.' });
    focusScan();
  }

  function pickAmbiguity(choice: 'assign' | 'transfer' | 'rework' | 'modify') {
    const pn = ambiguityPn ?? 'PF-BRACKET-001';
    setAmbiguityPn(null);
    if (choice === 'assign') {
      setPending(
        `Pending context: ${pn} × 2 (Lathe queue) — scan a Machine barcode to assign.`,
      );
      setFeedback({
        kind: 'warn',
        icon: '!',
        title: 'Scan Machine barcode to assign',
        detail: `${pn} × 2 selected from Lathe queue — nothing recorded until the Machine scan.`,
      });
    } else if (choice === 'transfer') {
      setQtyValue('');
      setQtyDialog({ pn, available: 4 });
      return;
    } else {
      setFeedback({
        kind: 'warn',
        icon: '!',
        title: `${choice.toUpperCase()} intake confirmation`,
        detail:
          'The intake confirmation flow arrives with the domain workflows in a later phase — nothing recorded (mock).',
      });
    }
    focusScan();
  }

  function confirmManual(pnText: string) {
    setManualOpen(false);
    const pn = pnText.trim().toUpperCase();
    if (!pn) {
      focusScan();
      return;
    }
    if (pn === 'PF-SHAFT-014') {
      setLastPn({ pn, desc: 'manual entry · single valid context' });
      setQtyValue('');
      setQtyDialog({ pn, available: 8 });
    } else if (pn === 'PF-BRACKET-001') {
      setLastPn({ pn, desc: 'manual entry · multiple valid contexts' });
      setAmbiguityPn(pn);
    } else {
      setFeedback({
        kind: 'err',
        icon: '✕',
        title: 'Unknown PartNumber',
        detail: `“${pn}” matches no PN — nothing recorded. Manual entry is validated exactly like a scan.`,
      });
      focusScan();
    }
  }

  function undoLast() {
    if (!undoCount) return;
    setUndoCount((n) => n - 1);
    addScan(
      {
        pn: scans[0]?.pn ?? '—',
        description:
          'REVERSED · compensates previous scan · reason: operator undo (mock)',
        time: MOCK_SCAN_TIME,
        reversed: true,
      },
      false,
    );
    setFeedback({
      kind: 'warn',
      icon: '⟲',
      title: 'Undo recorded as REVERSED',
      detail:
        'Original Movement is preserved — a compensating event restores derived quantities (mock presentation).',
    });
    focusScan();
  }

  if (preview === 'loading') {
    return (
      <section className="ss" aria-label="Scan Station">
        <LoadingState label="Loading Scan Station" />
        <div className="ss-scanwrap">
          <div className="ss-scanrow">
            <input
              className="ss-scaninput"
              disabled
              placeholder="Connecting…"
              aria-label="Scan barcode"
            />
            <button className="ss-scanbtn" disabled>
              ENTER
            </button>
          </div>
        </div>
      </section>
    );
  }

  const queued =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? MOCK_INVENTORY_QUEUED_LONG
        : MOCK_INVENTORY_QUEUED;
  const assigned = preview === 'empty' ? [] : MOCK_INVENTORY_ASSIGNED;
  const total =
    queued.reduce((s, r) => s + r.qty, 0) +
    assigned.reduce((s, r) => s + r.qty, 0);
  const shownFeedback: Feedback =
    preview === 'error'
      ? {
          kind: 'err',
          icon: '✕',
          title: 'Unknown PN barcode',
          detail:
            'Rejected — unknown or invalid scans never update tracking data. Nothing recorded.',
        }
      : feedback;

  return (
    <section className="ss" aria-label="Scan Station">
      <header className="ss-head">
        <div>
          <div className="dept">{MOCK_STATION.department}</div>
          <div className="area">
            <AreaDot colorVar={MOCK_STATION.areaColorVar} size={16} />
            {MOCK_STATION.areaName}
          </div>
          <div className="op">
            Operations: <b>{MOCK_STATION.operations}</b> · Station{' '}
            <b className="mono">{MOCK_STATION.stationId}</b>
          </div>
        </div>
        <span className="spacer" />
        <div className="ss-pill">
          <span className="lbl">Machine session</span>
          <span className={`val ${machineSession ? '' : 'none'}`}>
            {machineSession ? (
              <>
                <span className="sdot" aria-hidden="true" />
                {machineSession}
              </>
            ) : (
              '— none'
            )}
          </span>
          <span className="sub">held until changed · cleared · expired</span>
        </div>
        <div className="ss-pill">
          <span className="lbl">Worker session</span>
          <span className="val">
            <span className="sdot" aria-hidden="true" />
            {worker}
          </span>
          <span className="sub">{MOCK_STATION.workerNote}</span>
        </div>
      </header>

      <div className="ss-body">
        <div className="ss-main">
          <div className="panel">
            <div className="ph">
              Machine status
              <span className="spacer" />
              <span className="note">
                select by scanning a Machine barcode — session persists until
                changed, cleared or expired
              </span>
            </div>
            <div className="ss-machines">
              {MOCK_MACHINES.map((m) => (
                <div
                  key={m.name}
                  className={`mtile ${m.maintenance ? 'maint' : ''} ${
                    machineSession === m.name ? 'session' : ''
                  }`}
                >
                  {m.name}
                  <span className="sub">{m.note}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="ph">
              Scan barcode
              <span className="spacer" />
              <span className="note">
                one input for every barcode type — the system classifies the
                scan
              </span>
            </div>
            <div className="ss-scanwrap">
              <div className="ss-scanrow">
                <input
                  ref={inputRef}
                  className="ss-scaninput"
                  autoComplete="off"
                  disabled={writeBlocked}
                  placeholder={
                    disconnected
                      ? 'Disconnected — scanning blocked'
                      : status === 'connecting'
                        ? 'Connecting…'
                        : 'Scan PN / Machine / Worker / Action barcode…'
                  }
                  aria-label="Scan barcode"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleScan();
                  }}
                />
                <button
                  className="ss-scanbtn"
                  onClick={handleScan}
                  disabled={writeBlocked}
                >
                  ENTER
                </button>
              </div>
              <div className="ss-hint">
                Demo barcodes — <code>PF:PN:1014</code> single context ·{' '}
                <code>PF:PN:1001</code> multiple contexts ·{' '}
                <code>PF:MACHINE:L1</code> machine session ·{' '}
                <code>PF:WORKER:88</code> worker · <code>PF:ACTION:REWORK</code>{' '}
                request type · <code>PF:PN:9999</code> unknown
              </div>
              <div className="ss-manual">
                Scanner unavailable?{' '}
                <button
                  onClick={() => setManualOpen(true)}
                  disabled={writeBlocked}
                >
                  Manual PN entry…
                </button>{' '}
                — a separate explicit fallback; raw PN text is never treated as
                a barcode
              </div>
            </div>
            {pending && (
              <div className="ss-pending" role="status">
                <span className="pulse" aria-hidden="true" />
                <span>{pending}</span>
              </div>
            )}
            <div className="ss-lastpn">
              <span className="l">Last scanned PN</span>
              <span className="p">{lastPn?.pn ?? '—'}</span>
              <span className="d">{lastPn?.desc ?? 'no scans yet'}</span>
            </div>
            <div className={`ss-feedback ${shownFeedback.kind}`} role="status">
              {shownFeedback.kind !== 'idle' && (
                <div className="fic" aria-hidden="true">
                  {shownFeedback.icon}
                </div>
              )}
              <div>
                <div
                  className="t1"
                  style={
                    shownFeedback.kind === 'idle'
                      ? { color: 'var(--faint)', fontWeight: 400 }
                      : undefined
                  }
                >
                  {shownFeedback.title}
                </div>
                {shownFeedback.detail && (
                  <div className="t2">{shownFeedback.detail}</div>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="ss-side">
          <button
            className="ss-undo"
            onClick={undoLast}
            disabled={writeBlocked || undoCount === 0}
          >
            ⟲ UNDO LAST SCAN
          </button>
          <div className="panel">
            <div className="ph">
              Recent scans
              <span className="spacer" />
              <span className="note">{scans.length} today</span>
            </div>
            <ul className="scanlist">
              {scans.map((s, i) => (
                <li key={`${s.pn}-${i}`}>
                  <span
                    className={`sic ${s.reversed ? 'undo' : ''}`}
                    aria-hidden="true"
                  />
                  <span className="what">
                    <span className="p" title={s.pn}>
                      {s.pn}
                    </span>
                    <span className="d">{s.description}</span>
                  </span>
                  <span className="when">{s.time}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="panel">
            <div className="ph">In this Area now</div>
            {assigned.length === 0 && queued.length === 0 ? (
              <div className="invempty">No production in this Area</div>
            ) : (
              <>
                <div className="invgrp">Assigned to Machines</div>
                <ul className="invlist">
                  {assigned.map((r) => (
                    <li key={`${r.pn}-${r.where}`}>
                      <span className="p" title={r.pn}>
                        {r.pn}
                      </span>
                      <span className="m">{r.where}</span>
                      <span className="q">{r.qty}</span>
                    </li>
                  ))}
                </ul>
                <div className="invgrp">Area queue — awaiting Machine</div>
                <ul className="invlist">
                  {queued.map((r) => (
                    <li key={`${r.pn}-${r.where}`}>
                      <span className="p" title={r.pn}>
                        {r.pn}
                      </span>
                      <span className="m">{r.where}</span>
                      <span className="q">{r.qty}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="invtotal">
              Total in {MOCK_STATION.areaName}
              <span className="q">{total}</span>
            </div>
          </div>
        </div>
      </div>

      {qtyDialog && (
        <ModalDialog label="Enter quantity" onClose={cancelDialog}>
          <h3>Enter quantity</h3>
          <div className="big mono">{qtyDialog.pn}</div>
          <div className="sub">
            Transfer Material → Lathe · available at source:{' '}
            <b>{qtyDialog.available}</b>
          </div>
          <div
            className="qtydisplay"
            role="status"
            aria-label={`Quantity: ${qtyValue || 'none'}`}
          >
            {qtyValue || ' '}
          </div>
          <div className="keypad">
            {['7', '8', '9', '4', '5', '6', '1', '2', '3'].map((k) => (
              <button
                key={k}
                onClick={() => setQtyValue((v) => (v.length < 4 ? v + k : v))}
              >
                {k}
              </button>
            ))}
            <button className="act" onClick={() => setQtyValue('')}>
              CLEAR
            </button>
            <button
              onClick={() => setQtyValue((v) => (v.length < 4 ? v + '0' : v))}
            >
              0
            </button>
            <button
              className="act"
              onClick={() => setQtyValue((v) => v.slice(0, -1))}
              aria-label="Backspace"
            >
              ⌫
            </button>
          </div>
          <div className="row">
            <button className="bigbtn ghost" onClick={cancelDialog}>
              Cancel
            </button>
            <button
              className="bigbtn primary"
              onClick={confirmQty}
              disabled={!parseInt(qtyValue || '0', 10)}
            >
              Confirm
            </button>
          </div>
        </ModalDialog>
      )}

      {ambiguityPn && (
        <ModalDialog
          label="Multiple valid contexts — confirm intent"
          onClose={cancelDialog}
        >
          <h3>Multiple valid contexts — confirm intent</h3>
          <div className="big mono">{ambiguityPn}</div>
          <div className="sub">
            This PN has more than one valid context at this station. Nothing is
            recorded until you choose:
          </div>
          <button className="choice" onClick={() => pickAmbiguity('assign')}>
            <span className="cic que" aria-hidden="true">
              QUE
            </span>
            <span>
              <span className="ct1">Assign queued quantity to a Machine</span>
              <br />
              <span className="ct2">
                2 pcs waiting in Lathe queue · scan Machine barcode next
              </span>
            </span>
          </button>
          <button className="choice" onClick={() => pickAmbiguity('transfer')}>
            <span className="cic run" aria-hidden="true">
              MOVE
            </span>
            <span>
              <span className="ct1">Receive more quantity from Cut</span>
              <br />
              <span className="ct2">
                4 pcs available at Cut · quantity entry follows
              </span>
            </span>
          </button>
          <button className="choice" onClick={() => pickAmbiguity('rework')}>
            <span className="cic rwk" aria-hidden="true">
              RWK
            </span>
            <span>
              <span className="ct1">Create REWORK demand</span>
              <br />
              <span className="ct2">
                Opens intake confirmation — quantity, PO link (or temporary PO{' '}
                <span className="mono">TMP-YYYYMMDD-HHMM-REWORK</span>), Route,
                starting Area. Nothing recorded yet.
              </span>
            </span>
          </button>
          <button className="choice" onClick={() => pickAmbiguity('modify')}>
            <span className="cic mod" aria-hidden="true">
              MOD
            </span>
            <span>
              <span className="ct1">Create MODIFY demand</span>
              <br />
              <span className="ct2">
                Opens intake confirmation — new Quantity Flow with its own
                Route. Nothing recorded yet.
              </span>
            </span>
          </button>
          <div className="row">
            <button className="bigbtn ghost" onClick={cancelDialog}>
              Cancel (Esc) — nothing recorded
            </button>
          </div>
        </ModalDialog>
      )}

      {manualOpen && (
        <ManualEntryDialog onCancel={cancelDialog} onConfirm={confirmManual} />
      )}
    </section>
  );
}

function ManualEntryDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: (pn: string) => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);
  return (
    <ModalDialog label="Manual PN entry — explicit fallback" onClose={onCancel}>
      <h3>Manual PN entry — explicit fallback</h3>
      <div className="sub">
        Type the <b>exact PartNumber</b> (not a barcode). It is validated
        exactly like a scan — raw PN text is never interpreted as a barcode.
        Nothing is recorded by this step.
      </div>
      <input
        aria-label="Exact PartNumber"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        placeholder="Exact PartNumber, e.g. PF-SHAFT-014"
        onKeyDown={(e) => {
          if (e.key === 'Enter') onConfirm(e.currentTarget.value);
        }}
      />
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          onClick={() => onConfirm(fieldRef.current?.value ?? '')}
        >
          Look up PN
        </button>
      </div>
    </ModalDialog>
  );
}
