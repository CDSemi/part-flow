import './tracking.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { AreaDot, HotPn, TypeChip } from '../../components/indicators';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { areaByKey } from '../../mocks/areas';
import {
  MOCK_TRACKING_DETAIL,
  MOCK_TRACKING_ROWS,
  MOCK_TRACKING_ROWS_LONG,
} from '../../mocks/tracking';

const FILTERS: { label: string; options: string[] }[] = [
  {
    label: 'Area',
    options: [
      'All',
      'Material',
      'Cut',
      'Lathe',
      'Mill',
      'Manual',
      'Deburr',
      'External',
      'Stockroom',
    ],
  },
  {
    label: 'Operation',
    options: ['All', 'Cutting', 'Turning', 'Milling', 'Deburring', 'Plating'],
  },
  { label: 'Machine', options: ['All', 'Saw 1', 'Lathe 1–4', 'Mill 1–2'] },
  { label: 'Request Type', options: ['All', 'NEW', 'MODIFY'] },
  { label: 'Priority', options: ['All', 'Hot only'] },
  { label: 'Status', options: ['Active', 'Stocked', 'Completed', 'All'] },
  { label: 'Due', options: ['Any', 'Overdue', 'This week', 'This month'] },
];

// PN-centric management view: filterable list + read-only detail panel.
// Movement history is immutable — no edit or delete affordances exist.
export function TrackingView() {
  const preview = getViewStatePreview();
  const [search, setSearch] = useState('');
  const [selectedPn, setSelectedPn] = useState(MOCK_TRACKING_DETAIL.pn);

  if (preview === 'loading') {
    return (
      <section className="tk" aria-label="Tracking">
        <LoadingState label="Loading Tracking" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="tk" aria-label="Tracking">
        <ErrorState
          message="Tracking data could not be loaded."
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const allRows =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? MOCK_TRACKING_ROWS_LONG
        : MOCK_TRACKING_ROWS;
  const query = search.trim().toLowerCase();
  const rows = allRows.filter(
    (r) =>
      !query ||
      (r.pn + r.name + r.demand.map((d) => d.workOrder).join(' '))
        .toLowerCase()
        .includes(query),
  );

  const detail = MOCK_TRACKING_DETAIL;

  return (
    <section className="tk" aria-label="Tracking">
      <div className="tk-wrap">
        <div className="tk-left">
          <h1>Tracking</h1>
          <div className="tk-filters">
            <input
              placeholder="Search: PN, WO, Job Number…"
              aria-label="Search PN, WO, Job Number"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {FILTERS.map((f) => (
              <select key={f.label} aria-label={f.label}>
                {f.options.map((option, i) => (
                  <option key={option}>
                    {i === 0 ? `${f.label}: ${option}` : option}
                  </option>
                ))}
              </select>
            ))}
          </div>
          {rows.length === 0 ? (
            <EmptyState
              message={
                query
                  ? `No PNs match “${search.trim()}” — clear filters.`
                  : 'No PNs match the current filters — clear filters.'
              }
            />
          ) : (
            <table className="tk-table">
              <thead>
                <tr>
                  <th>Part Number</th>
                  <th>Active WO Demand</th>
                  <th>Current distribution</th>
                  <th>Active qty</th>
                  <th>Stocked</th>
                  <th>Scrapped</th>
                  <th>Due (next)</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.pn}
                    className={row.pn === selectedPn ? 'sel' : ''}
                  >
                    <td>
                      <button
                        className="rowbtn"
                        onClick={() => setSelectedPn(row.pn)}
                        aria-pressed={row.pn === selectedPn}
                      >
                        <span className="part">
                          <HotPn rank={row.hotRank} pn={row.pn} />
                          {row.archived ? (
                            <span className="archived">(archived)</span>
                          ) : null}
                        </span>
                        <span className="sub" style={{ display: 'block' }}>
                          {row.name}
                        </span>
                      </button>
                    </td>
                    <td className="demandcell">
                      {row.demand.length === 0 ? (
                        <span className="sub">—</span>
                      ) : (
                        row.demand.map((d) => (
                          <div key={`${d.workOrder}-${d.type}`}>
                            {d.workOrder} · {d.qty} <TypeChip type={d.type} />
                          </div>
                        ))
                      )}
                    </td>
                    <td>
                      <div className="distmini">
                        {row.distribution.map((d) => (
                          <span key={d.label}>
                            <AreaDot
                              colorVar={
                                areaByKey(d.area)?.colorVar ?? 'var(--faint)'
                              }
                              size={8}
                            />
                            {d.label} <b>{d.qty}</b>
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="mono">{row.activeQty}</td>
                    <td className="mono">{row.stockedQty}</td>
                    <td className={`mono ${row.scrappedQty ? 'scrapqty' : ''}`}>
                      {row.scrappedQty || '—'}
                    </td>
                    <td>{row.nextDue}</td>
                    <td>
                      <span
                        className={`status ${
                          row.status === 'Active'
                            ? 'active'
                            : row.status === 'Stocked'
                              ? 'stocked'
                              : 'done'
                        }`}
                      >
                        {row.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <aside className="tk-right" aria-label="PN detail">
          {selectedPn !== detail.pn ? (
            <EmptyState
              message="No detail available for this PN yet."
              hint={
                import.meta.env.DEV
                  ? `Development preview: detail data exists for ${detail.pn} only.`
                  : undefined
              }
            />
          ) : (
            <TrackingDetail />
          )}
        </aside>
      </div>
    </section>
  );
}

function TrackingDetail() {
  const d = MOCK_TRACKING_DETAIL;
  return (
    <>
      <div className="tk-pnrow">
        <div className="tk-img" aria-hidden="true">
          🔩
        </div>
        <div>
          <h2>{d.pn}</h2>
          <div className="jsub">
            {d.name}
            {d.revision ? (
              <>
                {' '}
                · revision <b>{d.revision}</b> (informational)
              </>
            ) : null}{' '}
            · barcode <b>{d.barcode}</b> · ERP id <b>{d.erpId}</b>
          </div>
        </div>
      </div>

      <div className="tk-sec">
        <h4>
          Active WO Demand <span className="tag">requested quantity</span>
        </h4>
        <table className="demand">
          <thead>
            <tr>
              <th>WO</th>
              <th>Type</th>
              <th>Req.</th>
              <th>Alloc.</th>
              <th>Shortage</th>
              <th>Due</th>
              <th>Priority</th>
            </tr>
          </thead>
          <tbody>
            {d.demand.map((row) => (
              <tr key={row.workOrder}>
                <td className="mono">{row.workOrder}</td>
                <td>
                  <TypeChip type={row.type} />
                </td>
                <td className="mono">{row.requested}</td>
                <td className="mono zero">{row.allocated}</td>
                <td className="mono short">{row.shortage}</td>
                <td>{row.due}</td>
                <td>{row.priority}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="prog">
          <i style={{ width: '0%' }} />
        </div>
        <div className="prognote">{d.allocationNote}</div>
      </div>

      <div className="tk-sec">
        <h4>
          Current quantity by Area{' '}
          <span className="tag">current recorded location</span>
        </h4>
        <div className="dist">
          {/* `state` keeps the holding states visually distinct: active
              Machine assignment, Area-queue waiting, and Area completion
              (`done` — READY_TO_TRANSFER). A done row names the Area as
              the location; the Machine no longer holds the quantity. */}
          {d.distribution.map((row) => (
            <div
              className={`drow${row.state === 'done' ? ' done' : ''}`}
              key={`${row.name}-${row.sub}`}
            >
              <AreaDot
                colorVar={areaByKey(row.area)?.colorVar ?? 'var(--faint)'}
              />
              <span className="nm">
                {row.name} <span className="sub">{row.sub}</span>
              </span>
              <span className="bar">
                <i
                  style={{
                    width: `${row.pct}%`,
                    background: areaByKey(row.area)?.colorVar,
                    opacity: row.state === 'queue' ? 0.55 : 1,
                  }}
                />
              </span>
              <span className="q">{row.qty}</span>
            </div>
          ))}
        </div>
        {d.distribution.some((row) => row.state === 'done') ? (
          <div className="prognote donenote">{d.readyNote}</div>
        ) : null}
      </div>

      <div className="tk-sec">
        <h4>
          Quantity Flows &amp; Routes{' '}
          <span className="tag">
            Planned Route (guidance) or Floating actual route trace — the PN is
            not at one step
          </span>
        </h4>
        {d.flows.map((flow) => (
          <div className="qflow" key={flow.id}>
            <div className="qf-head">
              <span className="qf-id">{flow.id}</span>
              <span className="qf-q">{flow.qty} pcs</span>
              <span className={`qf-mode ${flow.routeMode.toLowerCase()}`}>
                {flow.routeMode === 'FLOATING'
                  ? 'FLOATING — actual trace'
                  : 'PLANNED — snapshot'}
              </span>
              <span className="qf-pos">{flow.position}</span>
            </div>
            <div className="route">
              {/* Steps and arrows are separate sibling flex items in
                  document order (step, arrow, step, …) so an arrow can
                  never overlap a step card and wrapping stays readable.
                  Repeated Areas are preserved: the trace is Movement
                  history, so the same Area may appear more than once
                  and the step name alone is not a unique key. */}
              {flow.route.flatMap((step, i) => {
                const stepNode = (
                  <span
                    key={`step-${step.step}-${i}`}
                    className={`rstep ${
                      step.state === 'done'
                        ? 'done'
                        : step.state === 'cur'
                          ? 'cur'
                          : ''
                    } ${'repair' in step && step.repair ? 'repair' : ''}`}
                  >
                    {step.step}
                    {'repair' in step && step.repair ? (
                      <span className="repairmark"> ⟲ REPAIR</span>
                    ) : null}
                  </span>
                );
                if (i === 0) return [stepNode];
                return [
                  <span
                    key={`arrow-${i}`}
                    className="rarrow"
                    aria-hidden="true"
                  >
                    →
                  </span>,
                  stepNode,
                ];
              })}
            </div>
            {'routeNote' in flow ? (
              <div className="devnote">{flow.routeNote}</div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="tk-sec">
        <h4>
          Movement history{' '}
          <span className="tag">complete activity history</span>
        </h4>
        <ul className="mv">
          {d.movements.map((m, i) => (
            <li key={`${m.time}-${i}`}>
              <span className="t">{m.time}</span>
              <span className={`mtype ${m.typeClass}`}>{m.type}</span>
              {'repair' in m && m.repair ? (
                <span className="mtype scr">REPAIR</span>
              ) : null}
              <span className="desc">{m.description}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="tk-sec">
        <h4>
          Scrap history{' '}
          <span className="tag">
            auditable — scrap never reduces requested quantity
          </span>
        </h4>
        <div className="prognote" style={{ marginTop: 0 }}>
          {d.scrapNote}
        </div>
      </div>

      <div className="tk-sec">
        <h4>
          Stocked &amp; Allocation history{' '}
          <span className="tag">stocked quantity assigned to demand</span>
        </h4>
        <div className="prognote" style={{ marginTop: 0 }}>
          {d.stockedNote}
        </div>
      </div>

      <div className="tk-sec">
        <h4>
          Corrections{' '}
          <span className="tag">
            authorized roles · every change is audited
          </span>
        </h4>
        <div className="tk-actions">
          <button>Quantity adjustment…</button>
          <button>Edit assigned Route…</button>
          <button>Adjust WO Allocation…</button>
          <button>Change priority…</button>
          <button>View audit trail…</button>
        </div>
      </div>
    </>
  );
}
