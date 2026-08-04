import './administration.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { DevNotice } from '../../components/DevNotice';
import { useMockNotice } from '../../components/mock-notice';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  MOCK_ADMIN_AREAS,
  MOCK_ADMIN_SECTIONS,
} from '../../mocks/administration';
import { areaByKey } from '../../mocks/areas';
import { AreaDot } from '../../components/indicators';

const GROUPS = [
  'Organization',
  'Production setup',
  'Access',
  'Policies',
] as const;

// Administration shell with sidebar navigation and configuration panels.
// Every control here is presentation-only during Phase 2 — configuration
// is not persisted anywhere.
export function AdministrationView() {
  const preview = getViewStatePreview();
  const [sectionId, setSectionId] = useState('areas');
  const { showNotice, noticeElement } = useMockNotice();

  if (preview === 'loading') {
    return (
      <section className="ad" aria-label="Administration">
        <LoadingState label="Loading Administration" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="ad" aria-label="Administration">
        <ErrorState
          message="Administration data could not be loaded."
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const section =
    MOCK_ADMIN_SECTIONS.find((s) => s.id === sectionId) ??
    MOCK_ADMIN_SECTIONS[1];

  return (
    <section className="ad" aria-label="Administration">
      <div className="ad-wrap">
        <nav className="ad-nav" aria-label="Administration sections">
          {GROUPS.map((group) => (
            <div key={group}>
              <div className="grp">{group}</div>
              {MOCK_ADMIN_SECTIONS.filter((s) => s.group === group).map((s) => (
                <button
                  key={s.id}
                  className={s.id === section.id ? 'active' : ''}
                  aria-current={s.id === section.id ? 'true' : undefined}
                  onClick={() => setSectionId(s.id)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="ad-main">
          <div className="ad-top">
            <div>
              <h1>{section.label}</h1>
              <div className="sub">{section.subtitle}</div>
            </div>
            <span className="spacer" />
            <button
              className="btn primary"
              onClick={() =>
                showNotice(
                  'Configuration editing is not available yet — nothing was created or changed.',
                )
              }
            >
              + New {section.label === 'Areas' ? 'Area' : 'entry'}
            </button>
          </div>
          <DevNotice>
            Development preview — configuration values shown are sample data.
          </DevNotice>
          {section.id === 'areas' ? (
            <AreasTable empty={preview === 'empty'} />
          ) : (
            <div className="ad-placeholder">
              The <b>{section.label}</b> configuration panel follows the same
              table + editor pattern as the Areas reference table. Configuration
              management for this section arrives in a later implementation
              phase.
            </div>
          )}
        </div>
      </div>
      {noticeElement}
    </section>
  );
}

function AreasTable({ empty }: { empty: boolean }) {
  if (empty) {
    return <EmptyState message="No Areas configured yet." />;
  }
  return (
    <>
      <table className="ad-table">
        <thead>
          <tr>
            <th>Area</th>
            <th>Operations</th>
            <th>Machine assignment</th>
            <th>Machines</th>
            <th>Worker ID mode</th>
            <th>Terminal</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {MOCK_ADMIN_AREAS.map((row) => (
            <tr key={row.areaKey}>
              <td>
                <AreaDot
                  colorVar={areaByKey(row.areaKey)?.colorVar ?? 'var(--faint)'}
                  size={14}
                />{' '}
                <b>{row.name}</b>
              </td>
              <td>{row.operations}</td>
              <td className="modecell">{row.machineMode}</td>
              <td className="mono">{row.machines}</td>
              <td className="modecell">{row.workerMode}</td>
              <td>
                {row.terminal ? (
                  <span className="pillnav term">Terminal</span>
                ) : null}
              </td>
              <td>
                <span className={`pillnav ${row.active ? 'on' : 'off'}`}>
                  {row.active ? 'Active' : 'Inactive'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="ad-notice">
        Area <b>identity and barcode are stable</b> — display name, description,
        color and icon may change without affecting historical Movements. An
        Area supporting multiple Operations (e.g. External) requires Operation
        resolution or confirmation at scan time. Deactivating an Area that still
        holds quantity is blocked with an explanation.
      </div>
    </>
  );
}
