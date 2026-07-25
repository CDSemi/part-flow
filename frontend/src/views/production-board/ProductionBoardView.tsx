import './production-board.css';

import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot, HotChip } from '../../components/indicators';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { areaByKey } from '../../mocks/areas';
import {
  MOCK_BOARD_ROWS,
  MOCK_BOARD_ROWS_LONG,
} from '../../mocks/production-board';
import type { MockBoardRow } from '../view-models';

// Read-only large-display view: mock rows, no interactive elements.
export function ProductionBoardView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();

  if (preview === 'loading') {
    return (
      <section className="pb" aria-label="Production Board">
        <LoadingState label="Loading Production Board" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="pb" aria-label="Production Board">
        <ErrorState
          message="The production feed could not be loaded."
          detail="The board retries automatically; data shown after recovery is complete and consistent."
        />
      </section>
    );
  }

  const rows: MockBoardRow[] =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? MOCK_BOARD_ROWS_LONG
        : MOCK_BOARD_ROWS;
  const activePns = rows.filter((r) => !r.totalStocked).length;
  const inProduction = rows
    .filter((r) => !r.totalStocked)
    .reduce((s, r) => s + r.total, 0);
  const stocked = rows
    .filter((r) => r.totalStocked)
    .reduce((s, r) => s + r.total, 0);

  return (
    <section className="pb" aria-label="Production Board">
      <div className="pb-head">
        <h1>Machine Shop — Production</h1>
        <span className="live">
          <span className="ld" aria-hidden="true" />
          {status === 'connected'
            ? 'Live · updated 14:32:08'
            : 'Feed stale · last updated 14:32:08'}
        </span>
        <span className="spacer" />
        <span className="clock">14:32</span>
      </div>
      {rows.length === 0 ? (
        <EmptyState message="No active production in this Department." />
      ) : (
        <table className="pb-table">
          <thead>
            <tr>
              <th style={{ width: '4%' }}>No.</th>
              <th style={{ width: '25%' }}>Part Number</th>
              <th style={{ width: '29%' }}>Areas &amp; Quantities · Time</th>
              <th style={{ width: '19%' }}>Job Numbers</th>
              <th style={{ width: '13%' }}>Due Date</th>
              <th style={{ width: '10%' }}>Total Days</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={row.pn}
                className={
                  row.hotRank === 1
                    ? 'hotrow1'
                    : row.hotRank === 2
                      ? 'hotrow2'
                      : undefined
                }
              >
                <td>
                  <div className="no">{index + 1}</div>
                </td>
                <td>
                  <div className={`part ${row.blink ? 'blink' : ''}`}>
                    {row.hotRank ? '🔥 ' : ''}
                    {row.pn}
                    {row.hotRank ? (
                      <span style={{ marginLeft: 9 }}>
                        <HotChip rank={row.hotRank} showFlame={false} />
                      </span>
                    ) : null}
                  </div>
                  <div className="pname">{row.name}</div>
                </td>
                <td>
                  <div className="loc">
                    {row.locations.map((loc) => (
                      <div
                        className="locrow"
                        key={`${loc.label}-${loc.tag ?? ''}`}
                      >
                        <AreaDot
                          colorVar={
                            areaByKey(loc.area)?.colorVar ?? 'var(--faint)'
                          }
                          size={11}
                        />
                        {loc.label} <span className="q">{loc.qty}</span>
                        {loc.tag ? (
                          <span className="qtag">{loc.tag}</span>
                        ) : null}
                        <span className={`ltime ${loc.timeLong ? 'long' : ''}`}>
                          {loc.time}
                        </span>
                      </div>
                    ))}
                    <div className="totline">
                      total <b>{row.total}</b> pcs
                      {row.totalStocked ? ' stocked' : ''}
                    </div>
                  </div>
                </td>
                <td className="jobs">
                  {row.jobs.map((job) => (
                    <div className="j" key={job.job + job.meta}>
                      {job.job} <span className="jm">{job.meta}</span>
                    </div>
                  ))}
                </td>
                <td className="due">
                  <div className="d1">{row.due}</div>
                  <div className={`d2 ${row.dueClass}`}>{row.dueNote}</div>
                </td>
                <td>
                  <div className="dtotal">{row.totalDays}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="pb-foot">
        <span>Page 1 / 3 · rotates every 12 s</span>
        <span className="pgdot on" aria-hidden="true" />
        <span className="pgdot" aria-hidden="true" />
        <span className="pgdot" aria-hidden="true" />
        <span className="spacer" />
        <span>
          🔥 #n = Hot priority rank · blinking PN = due soon / overdue ·{' '}
          {activePns} active PNs · {inProduction} pcs in production · {stocked}{' '}
          pcs stocked
        </span>
      </div>
    </section>
  );
}
