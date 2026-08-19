import './administration.css';

import { Suspense, lazy, useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AreasSection } from './AreasSection';
import { BarcodeConfigurationSection } from './BarcodeConfigurationSection';
import { DepartmentsSection } from './DepartmentsSection';
import { OperationsSection } from './OperationsSection';
import { ScanStationsSection } from './ScanStationsSection';
import { SectionHeader } from './section-widgets';
import { ADMIN_GROUPS, ADMIN_SECTIONS } from './sections';
import type { AdminSection } from './sections';

// Administration shell with sidebar navigation and configuration
// panels (GUI_DESIGN §9). The Phase 3.5 minimum environment setup
// sections — Departments, Areas, Operations, Scan Stations, Barcode
// configuration — read and write the real configuration through the
// /api surface. Every other section arrives with the later full
// Administration phase (Phase 13) and presents itself honestly as not
// available yet; the Worker sessions policy preview stays behind the
// development-only build boundary.

// Development-only Worker sessions preview: the lazy import sits
// behind `import.meta.env.DEV`, so production builds drop the module —
// and the mock policy datasets it renders — from the module graph.
const WorkerSessionsPreview = import.meta.env.DEV
  ? lazy(() =>
      import('./WorkerSessionsPreview').then((module) => ({
        default: module.WorkerSessionsPreview,
      })),
    )
  : null;

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
    case 'scan-stations':
      return <ScanStationsSection />;
    case 'barcode-configuration':
      return <BarcodeConfigurationSection />;
    case 'worker-sessions':
      if (WorkerSessionsPreview) {
        return (
          <>
            <SectionHeader title={section.label} subtitle={section.subtitle} />
            <Suspense fallback={<LoadingState label="Loading section" />}>
              <WorkerSessionsPreview />
            </Suspense>
          </>
        );
      }
      return <PlaceholderSection section={section} entryAction={false} />;
    default:
      return <PlaceholderSection section={section} entryAction />;
  }
}

/**
 * One later-phase section, presented honestly: the entry action that
 * does not exist yet is disabled (never made to appear functional) and
 * the panel states when the section becomes real. All Phase 3.5
 * minimum-environment sections are real above — every placeholder here
 * belongs to the later full Administration phase.
 */
function PlaceholderSection({
  section,
  entryAction,
}: {
  section: AdminSection;
  /** Settings-form sections (e.g. Worker sessions) show no entry
   * action at all. */
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
