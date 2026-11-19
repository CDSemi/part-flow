import './administration.css';

import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

import { useRouter } from '../../app/router-context';
import { SignInGate } from '../../app/SignInGate';
import { getViewStatePreview } from '../../app/view-state';
import { ErrorState, LoadingState } from '../../components/view-states';
import { ArchiveViewerPage } from './ArchiveViewerPage';
import { AreasSection } from './AreasSection';
import { BackupsSection } from './BackupsSection';
import { BarcodeConfigurationSection } from './BarcodeConfigurationSection';
import { CorrectionPermissionsSection } from './CorrectionPermissionsSection';
import { DepartmentDisplaySection } from './DepartmentDisplaySection';
import { DepartmentsSection } from './DepartmentsSection';
import { HistoryArchivalSection } from './HistoryArchivalSection';
import { MachineAssignmentSection } from './MachineAssignmentSection';
import { OperationsSection } from './OperationsSection';
import { RolesSection } from './RolesSection';
import { ScanBehaviorSection } from './ScanBehaviorSection';
import { ScanStationsSection } from './ScanStationsSection';
import { SecuritySection } from './SecuritySection';
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
// accounts and named roles), Worker sessions (the real sliding
// inactivity timeout — default and per-Area overrides), Correction
// permissions (the real Undo reason policy and the role ×
// correction-permission table), Department display settings (the
// per-Department Production Board rotation timing), Settings (the real
// Due Soon warning and the outgoing mail server; the other general
// settings are disabled previews marked Not available yet), Security
// (every sign-in setting: User sign-in, Remember me, the password rules
// and two-step sign-in; owner request of 2026-11-17) and History
// archival & purge (its settings, the preview, the request of a run and
// the runs, which export history without deleting it; Phase 16).
// Machine assignment is a read-only
// statement of the two Area modes. Backups (Phase 16) reads and writes
// the real interval and retention of the scheduled backups. Scan
// behavior shows its candidate settings as disabled previews marked Not
// available yet (Phase 17).
//
// Access (Phase 14 slice 2): every section needs a signed-in user, and
// no Administration request is sent before — the shared sign-in gate
// (app/SignInGate.tsx) owns the sign-in panel, the kept work of an
// ended sign-in and the per-user remount. Any signed-in user may view
// every section; a section whose permission the user lacks hides its
// controls and says so. The server checks every permission.
//
// The archive viewer of History archival & purge (Phase 16, build step 5)
// is a page of its own address inside this view
// (`/administration/history-archival/views/:id`, router-core): the
// sidebar stays, with History archival & purge active, and choosing a
// section, or the page's way back, returns to `/administration` with that
// section shown. The viewer itself needs Run history archival and purge.

export function AdministrationView() {
  const preview = getViewStatePreview();
  const { route } = useRouter();
  const archiveViewId =
    route.view === 'administration' ? (route.archiveViewId ?? null) : null;
  // Entering on the viewer's address, the way back leads to its section.
  const [sectionId, setSectionId] = useState(
    archiveViewId === null ? 'areas' : 'data-retention',
  );
  const navRef = useRef<HTMLElement>(null);

  if (preview === 'loading') {
    return (
      <section className="admin" aria-label="Administration">
        <LoadingState label="Loading Administration" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="admin" aria-label="Administration">
        <ErrorState
          message="Administration data could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  // The viewer belongs to History archival & purge, whatever was shown
  // before it.
  const shownId = archiveViewId === null ? sectionId : 'data-retention';
  const section =
    ADMIN_SECTIONS.find((s) => s.id === shownId) ?? ADMIN_SECTIONS[1];

  // A sign-in from the panel moves focus to the active section's
  // navigation button.
  return (
    <section className="admin" aria-label="Administration">
      <SignInGate
        area="Administration"
        onSignedIn={() =>
          navRef.current
            ?.querySelector<HTMLElement>('button[aria-current="true"]')
            ?.focus()
        }
      >
        <SectionsWrap
          navRef={navRef}
          section={section}
          archiveViewId={archiveViewId}
          onSelect={setSectionId}
        />
      </SignInGate>
    </section>
  );
}

/** The sidebar and the active section — or the archive viewer — mounted
 * per sections owner. */
function SectionsWrap({
  navRef,
  section,
  archiveViewId,
  onSelect,
}: {
  navRef: RefObject<HTMLElement>;
  section: AdminSection;
  /** The view the archive viewer reads; null for the sections. */
  archiveViewId: number | null;
  onSelect: (id: string) => void;
}) {
  const { navigate } = useRouter();
  // From the viewer, a section is shown at `/administration`.
  const select = (id: string) => {
    onSelect(id);
    if (archiveViewId !== null) navigate('/administration');
  };
  // Leaving the viewer (its way back, a section, or browser back) moves
  // focus to the active section's navigation button.
  const viewerShown = archiveViewId !== null;
  const wasViewerShown = useRef(viewerShown);
  useEffect(() => {
    if (wasViewerShown.current && !viewerShown) {
      navRef.current
        ?.querySelector<HTMLElement>('button[aria-current="true"]')
        ?.focus();
    }
    wasViewerShown.current = viewerShown;
  }, [viewerShown, navRef]);
  return (
    <div className="admin-wrap">
      <nav
        ref={navRef}
        className="admin-nav"
        aria-label="Administration sections"
      >
        {ADMIN_GROUPS.map((group) => (
          <div key={group}>
            <div className="grp">{group}</div>
            {ADMIN_SECTIONS.filter((s) => s.group === group).map((s) => (
              <button
                key={s.id}
                className={s.id === section.id ? 'active' : ''}
                aria-current={s.id === section.id ? 'true' : undefined}
                onClick={() => select(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="admin-main">
        {archiveViewId !== null ? (
          <ArchiveViewerPage
            viewId={archiveViewId}
            onShowSection={() => onSelect('data-retention')}
          />
        ) : (
          <SectionBody section={section} />
        )}
      </div>
    </div>
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
    case 'security':
      return <SecuritySection />;
    case 'machine-assignment':
      return <MachineAssignmentSection />;
    case 'scan-behavior':
      return <ScanBehaviorSection />;
    case 'data-retention':
      return <HistoryArchivalSection />;
    case 'backups':
      return <BackupsSection />;
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
 * History archival & purge, Settings and Scan
 * behavior (disabled previews, Phase 17) are real above, so no section
 * reaches this today: it stays the honest fallback of a section that
 * has no defined settings, and it promises no phase.
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
      <div className="admin-placeholder">
        The <b>{section.label}</b> configuration is not available yet. Its
        settings have not been defined. Machines, Planned Routes and Part
        Numbers are managed in <b>Management</b> by authorized production roles.
      </div>
    </>
  );
}
