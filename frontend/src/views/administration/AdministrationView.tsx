import './administration.css';

import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

import type { SessionUser } from '../../api/session';
import { useConnectivity } from '../../app/connectivity-context';
import {
  SessionContext,
  hasPermission,
  useSession,
} from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
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
// accounts and named roles), Worker sessions (the real sliding
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
//
// Access (Phase 14 slice 2): every section needs a signed-in user, and
// no Administration request is sent before. Signed out, a sign-in panel
// replaces the sections, and entering Administration signed out opens
// the Sign-in dialog once over it (not while first-run setup is open —
// setup needs the token from the server log). Any signed-in user may
// view every section; a section whose permission the user lacks hides
// its controls and says so. The server checks every permission.
//
// Work survives an ended sign-in: when the server refuses a sign-in as
// ended, the sections stay mounted with their open editors and drafts
// (presented with the permissions they were rendered with) while the
// Sign-in dialog is open. The sections belong to the user they were
// rendered for: a different user signing in remounts them, dropping the
// drafts; an explicit sign-out shows the sign-in panel.

type Presented = 'sections' | 'checking' | 'gate';

export function AdministrationView() {
  const preview = getViewStatePreview();
  const session = useSession();
  const { status: connectivity } = useConnectivity();
  const { status, setupOpen, endedBy, openSignIn } = session;
  const [sectionId, setSectionId] = useState('areas');
  // The user the sections are rendered for (their drafts belong to them).
  const [owner, setOwner] = useState<SessionUser | null>(null);
  const signedInUser = status === 'signed-in' ? session.user : null;
  const keepAfterExpiry =
    status !== 'signed-in' && endedBy === 'expired' && owner !== null;
  if (signedInUser !== null && signedInUser !== owner) {
    setOwner(signedInUser);
  } else if (status === 'signed-out' && !keepAfterExpiry && owner !== null) {
    setOwner(null);
  }
  const presented: Presented =
    signedInUser !== null || keepAfterExpiry
      ? 'sections'
      : status === 'unknown'
        ? 'checking'
        : 'gate';

  const navRef = useRef<HTMLElement>(null);
  const gateSignInRef = useRef<HTMLButtonElement>(null);

  // Entering Administration signed out (the first known sign-in of this
  // entry) opens the Sign-in dialog once; closing it returns focus to
  // the panel's Sign in button (the dialog restores focus to its opener).
  const entryDecided = useRef(false);
  useEffect(() => {
    if (entryDecided.current || status === 'unknown') return;
    entryDecided.current = true;
    if (status === 'signed-out' && !setupOpen) {
      gateSignInRef.current?.focus();
      openSignIn();
    }
  }, [status, setupOpen, openSignIn]);

  // A sign-in from the panel moves focus to the active section's
  // navigation button.
  const previouslyPresented = useRef<Presented | null>(null);
  useEffect(() => {
    const previous = previouslyPresented.current;
    previouslyPresented.current = presented;
    if (presented === 'sections' && previous === 'gate') {
      navRef.current
        ?.querySelector<HTMLElement>('button[aria-current="true"]')
        ?.focus();
    }
  }, [presented]);

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

  if (presented === 'checking') {
    return (
      <section className="ad" aria-label="Administration">
        {session.checking || connectivity === 'connecting' ? (
          <LoadingState label="Checking your sign-in" />
        ) : connectivity === 'unavailable' ? (
          <ErrorState message="Administration needs the connection to the PartFlow server." />
        ) : (
          <ErrorState
            message="Your sign-in could not be checked."
            onRetry={() => void session.refresh()}
          />
        )}
      </section>
    );
  }

  if (presented === 'gate') {
    return (
      <section className="ad" aria-label="Administration">
        <div className="ad-gate">
          <div className="ad-gatepanel">
            <h1>Administration</h1>
            {setupOpen ? (
              <>
                <p>
                  PartFlow has no administrator yet. Set up PartFlow with the
                  setup token from the server log, or sign in if you already
                  have an account.
                </p>
                <div className="row">
                  <button className="btn primary" onClick={session.openSetup}>
                    Set up PartFlow
                  </button>
                  <button
                    ref={gateSignInRef}
                    className="btn ghost"
                    onClick={openSignIn}
                  >
                    Sign in
                  </button>
                </div>
              </>
            ) : (
              <>
                <p>Sign in to view and change PartFlow&apos;s configuration.</p>
                <div className="row">
                  <button
                    ref={gateSignInRef}
                    className="btn primary"
                    onClick={openSignIn}
                  >
                    Sign in
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    );
  }

  const section =
    ADMIN_SECTIONS.find((s) => s.id === sectionId) ?? ADMIN_SECTIONS[1];
  const sectionsUser = signedInUser ?? owner;
  // After an ended sign-in the kept sections are presented with the
  // permissions they were rendered with; the server refuses every write
  // until the user signs in again.
  const sectionsSession: SessionValue = keepAfterExpiry
    ? { ...session, can: (key) => hasPermission(owner, key) }
    : session;

  return (
    <section className="ad" aria-label="Administration">
      <SessionContext.Provider value={sectionsSession}>
        <SectionsWrap
          key={sectionsUser?.id ?? 0}
          navRef={navRef}
          section={section}
          onSelect={setSectionId}
        />
      </SessionContext.Provider>
    </section>
  );
}

/** The sidebar and the active section, mounted per sections owner. */
function SectionsWrap({
  navRef,
  section,
  onSelect,
}: {
  navRef: RefObject<HTMLElement>;
  section: AdminSection;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="ad-wrap">
      <nav ref={navRef} className="ad-nav" aria-label="Administration sections">
        {ADMIN_GROUPS.map((group) => (
          <div key={group}>
            <div className="grp">{group}</div>
            {ADMIN_SECTIONS.filter((s) => s.group === group).map((s) => (
              <button
                key={s.id}
                className={s.id === section.id ? 'active' : ''}
                aria-current={s.id === section.id ? 'true' : undefined}
                onClick={() => onSelect(s.id)}
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
