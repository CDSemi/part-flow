import './administration.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AreasSection } from './AreasSection';
import { BarcodeConfigurationSection } from './BarcodeConfigurationSection';
import { DepartmentsSection } from './DepartmentsSection';
import { OperationsSection } from './OperationsSection';
import { ScanStationsSection } from './ScanStationsSection';
import { WorkerSessionsSection } from './WorkerSessionsSection';
import { WorkersSection } from './WorkersSection';
import { SectionHeader } from './section-widgets';
import { ADMIN_GROUPS, ADMIN_SECTIONS } from './sections';
import type { AdminSection } from './sections';

// Administration shell with sidebar navigation and configuration
// panels (GUI_DESIGN §9). The Phase 3.5 minimum environment setup
// sections — Departments, Areas, Operations, Scan Stations, Barcode
// configuration — read and write the real configuration through the
// /api surface, and so do the full Administration phase (Phase 13)
// sections built so far: Workers, and Worker sessions (the real
// sliding inactivity timeout — default and per-Area overrides). Every
// other section arrives later in that phase and presents itself
// honestly as not available yet.

export function AdministrationView() {
  const preview = getViewStatePreview();
  const [sectionId, setSectionId] = useState('areas');

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
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  const section =
    ADMIN_SECTIONS.find((s) => s.id === sectionId) ?? ADMIN_SECTIONS[1];

  return (
    <section className="ad" aria-label="Administration">
      <div className="ad-wrap">
        <nav className="ad-nav" aria-label="Administration sections">
          {ADMIN_GROUPS.map((group) => (
            <div key={group}>
              <div className="grp">{group}</div>
              {ADMIN_SECTIONS.filter((s) => s.group === group).map((s) => (
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
          <SectionBody section={section} />
        </div>
      </div>
    </section>
  );
}

function SectionBody({ section }: { section: AdminSection }) {
  switch (section.id) {
    case 'departments':
      return <DepartmentsSection />;
    case 'areas':
      return <AreasSection />;
    case 'operations':
      return <OperationsSection />;
    case 'workers':
      return <WorkersSection />;
    case 'scan-stations':
      return <ScanStationsSection />;
    case 'barcode-configuration':
      return <BarcodeConfigurationSection />;
    case 'worker-sessions':
      return <WorkerSessionsSection />;
    default:
      return <PlaceholderSection section={section} entryAction />;
  }
}

/**
 * One later-phase section, presented honestly: the entry action that
 * does not exist yet is disabled (never made to appear functional) and
 * the panel states when the section becomes real. All Phase 3.5
 * minimum-environment sections, Workers and Worker sessions are real
 * above — every placeholder here belongs to the later full
 * Administration phase.
 */
function PlaceholderSection({
  section,
  entryAction,
}: {
  section: AdminSection;
  /** Settings-form sections show no entry action at all. */
  entryAction: boolean;
}) {
  return (
    <>
      <SectionHeader
        title={section.label}
        subtitle={section.subtitle}
        action={
          entryAction ? (
            <button
              className="btn primary"
              disabled
              title="This configuration is not available yet"
            >
              + New entry
            </button>
          ) : undefined
        }
      />
      <div className="ad-placeholder">
        The <b>{section.label}</b> configuration is not available yet. It
        follows the same table + editor pattern as the Areas reference table and
        arrives with the later <b>full Administration</b> phase. Machines,
        Planned Routes and Part Numbers are managed in <b>Management</b> by
        authorized production roles.
      </div>
    </>
  );
}
