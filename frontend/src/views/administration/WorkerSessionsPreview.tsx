import { useState } from 'react';

import { areaByKey } from '../../mocks/areas';
import {
  MOCK_BADGE_CONFIRM_POLICY,
  MOCK_WORKER_SESSION_POLICY,
  setBadgeConfirmRequirement,
} from '../../mocks/scan-station';
import type { BadgeConfirmAction } from '../../mocks/scan-station';
import { DevNotice } from '../../components/DevNotice';

// DEVELOPMENT-ONLY module: the Worker sessions policy preview. Worker
// sessions become real with the later full Administration phase — this
// panel renders the sample policy from src/mocks/ and is reachable
// only through the import.meta.env.DEV-guarded lazy import in
// AdministrationView, so production bundles never include it (or the
// mock datasets it drags in).

/**
 * Worker sessions (Policies): the Scanned-session sliding inactivity
 * timeout values (read-only preview — one default plus per-Area
 * overrides, §19) and the badge-confirmation options for the three
 * sensitive Scan Station actions (post-v18). Every sensitive action
 * always ends in a final confirmation question; the three On/Off
 * switches only decide whether a Worker badge scan is REQUIRED as
 * that final step in Areas with scanned Worker Sessions (the shared
 * mock policy, edited session-only; default ON for all three).
 */
const BADGE_CONFIRM_OPTIONS: {
  action: BadgeConfirmAction;
  label: string;
  description: string;
}[] = [
  {
    action: 'done',
    label: 'DONE — Complete Area processing',
    description:
      'Require a Worker badge scan as the final step of every completion.',
  },
  {
    action: 'queue',
    label: 'QUEUE — Return unfinished quantity to queue',
    description:
      'Require a Worker badge scan as the final step of every queue return.',
  },
  {
    action: 'undo',
    label: 'UNDO — Reverse the last action',
    description:
      'Require a Worker badge scan as the final step of every reversal.',
  },
];

export function WorkerSessionsPreview() {
  const [policy, setPolicy] = useState({ ...MOCK_BADGE_CONFIRM_POLICY });
  function toggle(action: BadgeConfirmAction) {
    const next = !policy[action];
    setBadgeConfirmRequirement(action, next);
    setPolicy((current) => ({ ...current, [action]: next }));
  }
  return (
    <>
      <DevNotice>
        Development preview — configuration values shown are sample data, and
        changes affect only this preview.
      </DevNotice>
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
          Every sensitive action always ends in a final confirmation question
          restating the key facts. Each option below upgrades that final step to
          a required Worker badge scan in Areas with scanned Worker Sessions —
          the badge records the confirming Worker and completes the action.
          Areas with a fixed or disabled Worker always keep the question; no
          badge exists there.
        </p>
        <div className="ad-switchlist">
          {BADGE_CONFIRM_OPTIONS.map(({ action, label, description }) => (
            <button
              key={action}
              type="button"
              role="switch"
              aria-checked={policy[action]}
              aria-label={`Require badge scan — ${label}`}
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
    </>
  );
}
