import './administration.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AreasSection } from './AreasSection';
import { BarcodeConfigurationSection } from './BarcodeConfigurationSection';
import { CorrectionPermissionsSection } from './CorrectionPermissionsSection';
import { DepartmentDisplaySection } from './DepartmentDisplaySection';
import { DepartmentsSection } from './DepartmentsSection';
import { HistoryArchivalSection } from './HistoryArchivalSection';
import { MachineAssignmentSection } from './MachineAssignmentSection';
import { OperationsSection } from './OperationsSection';
import { RolesSection } from './RolesSection';
import { ScanStationsSection } from './ScanStationsSection';
import { SettingsSection } from './SettingsSection';
import { UsersSection } from './UsersSection';
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
// sections: Workers, Users and Roles & permissions (application
// accounts and named roles — configuration only, nothing is enforced
// before users can sign in), Worker sessions (the real sliding
// inactivity timeout — default and per-Area overrides), Correction
// permissions (the real Undo reason policy and the role ×
// correction-permission table), Department display settings (the
// per-Department Production Board rotation timing), Settings (the real
// Due Soon warning policy; the rest of Settings says it is not
// available yet) and History archival & purge (the real retention
// period; archival and purge runs say they are not available yet).
// Machine assignment is a read-only statement of the two Area modes.
// Scan behavior has no defined content yet and presents itself
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
    case 'users':
      return <UsersSection />;
    case 'roles':
      return <RolesSection />;
    case 'scan-stations':
      return <ScanStationsSection />;
    case 'barcode-configuration':
      return <BarcodeConfigurationSection />;
    case 'worker-sessions':
      return <WorkerSessionsSection />;
    case 'correction-permissions':
      return <CorrectionPermissionsSection />;
    case 'department-display':
      return <DepartmentDisplaySection />;
    case 'settings':
      return <SettingsSection />;
    case 'machine-assignment':
      return <MachineAssignmentSection />;
    case 'data-retention':
      return <HistoryArchivalSection />;
    default:
      return <PlaceholderSection section={section} entryAction />;
  }
}

/**
 * One section that is not available yet, presented honestly: the entry
 * action that does not exist yet is disabled (never made to appear
 * functional). All Phase 3.5 minimum-environment sections, Workers,
 * Users, Roles & permissions, Worker sessions, Machine assignment
 * (statement), Correction permissions, Department display settings,
 * History archival & purge (retention period) and Settings are real
 * above, so only a `deferred` section (Scan behavior) reaches this: it
 * has no defined settings and promises no phase.
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
        The <b>{section.label}</b> configuration is not available yet. Its
        settings have not been defined. Machines, Planned Routes and Part
        Numbers are managed in <b>Management</b> by authorized production roles.
      </div>
    </>
  );
}
