import './administration.css';

import { useState } from 'react';

import { getViewStatePreview } from '../../app/view-state';
import { DevNotice } from '../../components/DevNotice';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  MOCK_ADMIN_AREAS,
  MOCK_ADMIN_SECTIONS,
  MOCK_ASSET_TAG_FORMAT,
} from '../../mocks/administration';
import { areaByKey } from '../../mocks/areas';
import { MOCK_MACHINES } from '../../mocks/machines';
import {
  MOCK_BADGE_CONFIRM_POLICY,
  MOCK_WORKER_SESSION_POLICY,
  setBadgeConfirmRequirement,
} from '../../mocks/scan-station';
import type { BadgeConfirmAction } from '../../mocks/scan-station';
import { AreaDot } from '../../components/indicators';
import {
  MACHINE_BARCODE_NAMESPACE,
  formatAssetTag,
  machineBarcode,
  nextAssetTag,
} from '../asset-tags';

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
            {/* Honest presentation: configuration editing does not
                exist yet, so the entry action is disabled instead of
                pretending to work. Barcode configuration and Worker
                sessions are settings forms, not entry tables — no
                entry action at all. */}
            {section.id !== 'barcode-configuration' &&
            section.id !== 'worker-sessions' ? (
              <button
                className="btn primary"
                disabled
                title="Configuration editing is not available yet"
              >
                + New {section.label === 'Areas' ? 'Area' : 'entry'}
              </button>
            ) : null}
          </div>
          <DevNotice>
            {section.id === 'barcode-configuration' ||
            section.id === 'worker-sessions'
              ? 'Development preview — configuration values shown are sample data, and changes affect only this preview.'
              : 'Development preview — configuration values shown are sample data, and editing is not available yet.'}
          </DevNotice>
          {section.id === 'areas' ? (
            <AreasTable empty={preview === 'empty'} />
          ) : section.id === 'barcode-configuration' ? (
            <BarcodeConfigurationPanel />
          ) : section.id === 'worker-sessions' ? (
            <WorkerSessionsPanel />
          ) : (
            <div className="ad-placeholder">
              {section.phase === 'minimum' ? (
                <>
                  The <b>{section.label}</b> configuration panel follows the
                  same table + editor pattern as the Areas reference table. It
                  is part of the <b>minimum environment setup</b> — the small
                  configuration set (Departments, Areas, Operations, Scan
                  Stations, barcode configuration) that is completed before the
                  real production workflows run. Machines, Planned Routes and
                  Part Numbers are managed in <b>Management</b> by authorized
                  production roles.
                </>
              ) : (
                <>
                  The <b>{section.label}</b> configuration panel follows the
                  same table + editor pattern as the Areas reference table. It
                  arrives with the later <b>full Administration</b> phase.
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Barcode configuration (minimum environment setup): the PF: namespace
 * reference and the Machine Asset Tag format — deliberately a simple
 * prefix + zero-padded numeric sequence, never a template engine.
 * Machine barcodes carry the Asset Tag directly
 * (`PF:MACHINE:<asset-tag>`), so no separate Machine barcode value is
 * ever configured or entered.
 */
function BarcodeConfigurationPanel() {
  const [prefix, setPrefix] = useState(MOCK_ASSET_TAG_FORMAT.prefix);
  const [digitsText, setDigitsText] = useState(
    String(MOCK_ASSET_TAG_FORMAT.digits),
  );

  const parsedDigits = Number.parseInt(digitsText, 10);
  const digits = Number.isNaN(parsedDigits)
    ? MOCK_ASSET_TAG_FORMAT.digits
    : Math.min(8, Math.max(1, parsedDigits));
  const trimmedPrefix = prefix.trim();
  const prefixError = /[\s:]/.test(trimmedPrefix)
    ? 'The prefix cannot contain spaces or “:”.'
    : null;
  const format = { prefix: trimmedPrefix, digits };
  const next = nextAssetTag(
    format,
    MOCK_MACHINES.map((m) => m.assetTag),
  );

  return (
    <div className="ad-config">
      <h2>Machine Asset Tag format</h2>
      <p className="ad-confighelp">
        Every Machine receives its Asset Tag automatically when it is created in
        Management → Machines: the configured prefix followed by the next number
        in sequence, zero-padded to the configured length. Asset Tags are
        unique, are never reused — retired Machines keep theirs — and never
        change after creation. The Machine barcode is the Asset Tag in the{' '}
        <code>{MACHINE_BARCODE_NAMESPACE}</code> namespace.
      </p>
      <div className="ad-configgrid">
        <label>
          Prefix
          <input
            className="field mono"
            value={prefix}
            onChange={(event) => setPrefix(event.target.value)}
            placeholder="e.g. CD-"
          />
        </label>
        <label>
          Number length (digits)
          <input
            className="field mono"
            type="number"
            min={1}
            max={8}
            value={digitsText}
            onChange={(event) => setDigitsText(event.target.value)}
          />
        </label>
      </div>
      {prefixError ? (
        <div className="err" role="alert">
          {prefixError}
        </div>
      ) : null}
      <div className="ad-configpreview">
        <div className="prow">
          <span className="k">Asset Tags</span>
          <span className="v">
            {formatAssetTag(format, 1)}, {formatAssetTag(format, 2)}, …
          </span>
        </div>
        <div className="prow">
          <span className="k">Next Asset Tag</span>
          <span className="v">{next}</span>
        </div>
        <div className="prow">
          <span className="k">Scanned barcode</span>
          {/* Shared app-wide barcode reading tone (global .barcodeval). */}
          <span className="v barcodeval">{machineBarcode(next)}</span>
        </div>
      </div>
      <p className="ad-confighelp">
        A format change applies to Machines created afterwards only — existing
        Asset Tags are never renamed or regenerated.
      </p>
    </div>
  );
}

/**
 * Worker sessions (Policies): the Scanned-session sliding inactivity
 * timeout values (read-only preview — one default plus per-Area
 * overrides, §19) and the badge-confirmation options for the three
 * sensitive Scan Station actions (post-v18). The three On/Off switches
 * edit the shared mock policy session-only: ON adds a final gate after
 * the action's confirmation summary in EVERY Area — a Worker badge
 * scan where Sessions are scanned, a final confirmation question in
 * Fixed-Worker and Disabled Areas; default ON for all three.
 */
const BADGE_CONFIRM_OPTIONS: {
  action: BadgeConfirmAction;
  label: string;
  description: string;
}[] = [
  {
    action: 'done',
    label: 'DONE — Complete Area processing',
    description: 'Require a final confirmation gate for every completion.',
  },
  {
    action: 'queue',
    label: 'QUEUE — Return unfinished quantity to queue',
    description: 'Require a final confirmation gate for every queue return.',
  },
  {
    action: 'undo',
    label: 'UNDO — Reverse the last action',
    description: 'Require a final confirmation gate for every reversal.',
  },
];

function WorkerSessionsPanel() {
  const [policy, setPolicy] = useState({ ...MOCK_BADGE_CONFIRM_POLICY });
  function toggle(action: BadgeConfirmAction) {
    const next = !policy[action];
    setBadgeConfirmRequirement(action, next);
    setPolicy((current) => ({ ...current, [action]: next }));
  }
  return (
    <div className="ad-config">
      <h2>Sliding inactivity timeout</h2>
      <p className="ad-confighelp">
        A scanned Worker Session ends after this period without a valid
        production interaction — never at a shift boundary. One default value
        with optional per-Area overrides.
      </p>
      <div className="ad-configpreview">
        <div className="prow">
          <span className="k">Default timeout</span>
          <span className="v">
            {MOCK_WORKER_SESSION_POLICY.defaultTimeoutMinutes} minutes
          </span>
        </div>
        {Object.entries(MOCK_WORKER_SESSION_POLICY.areaOverrides).map(
          ([area, minutes]) => (
            <div className="prow" key={area}>
              <span className="k">
                {areaByKey(area)?.name ?? area} override
              </span>
              <span className="v">{minutes} minutes</span>
            </div>
          ),
        )}
      </div>
      <h2>Badge confirmation for sensitive actions</h2>
      <p className="ad-confighelp">
        Each option below adds a final step after the action’s confirmation
        summary in every Area. Where Worker Sessions are scanned, a Worker badge
        scan records the confirming Worker and completes the action; Areas with
        a fixed or disabled Worker ask a final confirmation question instead —
        no badge exists there.
      </p>
      <div className="ad-switchlist">
        {BADGE_CONFIRM_OPTIONS.map(({ action, label, description }) => (
          <button
            key={action}
            type="button"
            role="switch"
            aria-checked={policy[action]}
            aria-label={`Require final confirmation — ${label}`}
            className={`ad-switch${policy[action] ? ' on' : ''}`}
            onClick={() => toggle(action)}
          >
            <span className="swtext">
              <span className="swlabel">{label}</span>
              <span className="swdesc">{description}</span>
            </span>
            <span className="track" aria-hidden="true">
              <span className="knob" />
            </span>
            <span className="swstate">{policy[action] ? 'On' : 'Off'}</span>
          </button>
        ))}
      </div>
    </div>
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
              {/* data-label: inline column captions in the collapsed
                  stacked layout (GUI_DESIGN §2.5) — mode values and a
                  bare Machine count are not self-evident without the
                  header row. */}
              <td data-label="Operations">{row.operations}</td>
              <td className="modecell" data-label="Machine assignment">
                {row.machineMode}
              </td>
              <td className="mono" data-label="Machines">
                {row.machines}
              </td>
              <td className="modecell" data-label="Worker ID mode">
                {row.workerMode}
              </td>
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
