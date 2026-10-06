import { useState } from 'react';

import { errorMessage } from '../../api/client';
import { areaColor, listAreas, updateArea } from '../../api/environment';
import type { Area } from '../../api/environment';
import {
  getWorkerSessionPolicy,
  updateBadgeConfirmation,
  updateWorkerSessionPolicy,
} from '../../api/policies';
import type { WorkerSessionPolicy } from '../../api/policies';
import type { SensitiveAction } from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { AdminField, SectionHeader, ServerErrorNote } from './section-widgets';
import { WORKER_ID_MODE_LABELS } from './worker-id-modes';

// Administration → Worker sessions (Phase 13; GUI_DESIGN §9 Policies,
// PROJECT_PROFILE §19): the sliding inactivity timeout of scanned
// Worker Sessions — one persisted default plus optional per-Area
// overrides, all in whole minutes from 1 to 720. The default lives in
// the server's application policy (`/api/policies/worker-sessions`), an
// override on its Area (`PATCH /api/areas/{id}`); both are audited and
// re-validated by the server. The section also holds the three real
// badge-confirmation options of DONE, QUEUE return and Undo (default
// On): each switch saves on click and sends ONLY its own option (the
// policy PUT is a partial merge), so a stale read never overwrites
// another administrator's change; the timeout Save sends only the
// timeout.

const TIMEOUT_MIN = 1;
const TIMEOUT_MAX = 720;
const TIMEOUT_ERROR = 'Enter a whole number of minutes from 1 to 720.';

const BADGE_CONFIRMATION_OPTIONS: {
  action: SensitiveAction;
  key: keyof WorkerSessionPolicy['badgeConfirmation'];
  label: string;
  description: string;
}[] = [
  {
    action: 'DONE',
    key: 'done',
    label: 'DONE — Complete Area processing',
    description:
      'Require a Worker badge scan as the final step of every completion.',
  },
  {
    action: 'QUEUE',
    key: 'queue',
    label: 'QUEUE — Return unfinished quantity to queue',
    description:
      'Require a Worker badge scan as the final step of every queue return.',
  },
  {
    action: 'UNDO',
    key: 'undo',
    label: 'UNDO — Reverse the last action',
    description:
      'Require a Worker badge scan as the final step of every reversal.',
  },
];

/** The whole number of minutes the text holds, or null when it is not
 * a whole number from 1 to 720 (never rounded or clamped). */
function parseTimeout(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= TIMEOUT_MIN && value <= TIMEOUT_MAX
    ? value
    : null;
}

export function WorkerSessionsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const policyData = useApiData(getWorkerSessionPolicy);
  const areasData = useApiData(listAreas);
  const [editing, setEditing] = useState<Area | null>(null);
  // One policy write at a time: a switch or the timeout Save in flight
  // disables every other policy control until the server answered.
  const [switchBusy, setSwitchBusy] = useState(false);
  const [timeoutBusy, setTimeoutBusy] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const header = (
    <SectionHeader
      title="Worker sessions"
      subtitle="Scanned-session sliding inactivity timeout and badge confirmation of sensitive actions — DONE, QUEUE and UNDO (§19)"
    />
  );

  if (
    policyData.state.status === 'loading' ||
    areasData.state.status === 'loading'
  ) {
    return (
      <>
        {header}
        <LoadingState label="Loading section" />
      </>
    );
  }
  if (
    policyData.state.status === 'error' ||
    areasData.state.status === 'error'
  ) {
    const failed =
      policyData.state.status === 'error' ? policyData.state : areasData.state;
    return (
      <>
        {header}
        <ErrorState
          message="Worker session settings could not be loaded."
          detail={failed.status === 'error' ? failed.message : undefined}
          onRetry={() => {
            policyData.reload();
            areasData.reload();
          }}
        />
      </>
    );
  }

  const policy = policyData.state.data;
  const defaultMinutes = policy.timeoutMinutes;
  const areas = areasData.state.data;

  // Toggled from the last read; the server's answer is re-read so every
  // switch and the stored timeout show the server values.
  const toggle = async (
    action: SensitiveAction,
    key: keyof WorkerSessionPolicy['badgeConfirmation'],
  ) => {
    if (writeBlocked || switchBusy || timeoutBusy) return;
    setSwitchBusy(true);
    setSwitchError(null);
    try {
      await updateBadgeConfirmation(action, !policy.badgeConfirmation[key]);
      policyData.reload();
    } catch (error) {
      setSwitchError(errorMessage(error));
    } finally {
      setSwitchBusy(false);
    }
  };

  return (
    <>
      {header}
      <div className="ad-config">
        <h2>Sliding inactivity timeout</h2>
        <p className="ad-confighelp">
          A scanned Worker Session ends after this period without a valid
          production interaction — never at a shift boundary. One default value
          with optional per-Area overrides.
        </p>
        <DefaultTimeoutForm
          savedMinutes={defaultMinutes}
          writeBlocked={writeBlocked || switchBusy}
          onBusyChange={setTimeoutBusy}
          onSaved={policyData.reload}
        />
        <h2>Per-Area overrides</h2>
        <table className="ad-table">
          <thead>
            <tr>
              <th>Area</th>
              <th>Worker ID mode</th>
              <th>Session timeout</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {areas.map((area) => (
              <tr key={area.id}>
                <td>
                  <AreaDot colorVar={areaColor(area)} size={14} />{' '}
                  <b>{area.name}</b>
                  {area.isActive ? null : ' (inactive)'}
                </td>
                <td className="modecell" data-label="Worker ID mode">
                  {WORKER_ID_MODE_LABELS[area.workerIdentificationMode]}
                </td>
                <td data-label="Session timeout">
                  {area.workerSessionTimeoutMinutes !== null
                    ? `${area.workerSessionTimeoutMinutes} min`
                    : `Default · ${defaultMinutes} min`}
                </td>
                <td>
                  <button
                    className="btn"
                    aria-label={`Edit session timeout — ${area.name}`}
                    onClick={() => setEditing(area)}
                  >
                    Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="ad-confighelp">
          The timeout applies at Scan Stations of Areas in Scanned session mode.
          A change takes effect at each session's next valid production
          interaction or badge scan.
        </p>
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
          {BADGE_CONFIRMATION_OPTIONS.map(
            ({ action, key, label, description }) => {
              const on = policy.badgeConfirmation[key];
              return (
                <button
                  key={action}
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-label={`Require badge scan — ${label}`}
                  className={`ad-switch${on ? ' on' : ''}`}
                  disabled={writeBlocked || switchBusy || timeoutBusy}
                  onClick={() => void toggle(action, key)}
                >
                  <span className="swtext">
                    <span className="swlabel">{label}</span>
                    <span className="swdesc">{description}</span>
                  </span>
                  <span className="track" aria-hidden="true">
                    <span className="knob" />
                  </span>
                  <span className="swstate">{on ? 'On' : 'Off'}</span>
                </button>
              );
            },
          )}
        </div>
        <ServerErrorNote message={switchError} />
      </div>
      {editing ? (
        <AreaTimeoutDialog
          area={editing}
          defaultMinutes={defaultMinutes}
          writeBlocked={writeBlocked}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            areasData.reload();
          }}
        />
      ) : null}
    </>
  );
}

function DefaultTimeoutForm({
  savedMinutes,
  writeBlocked,
  onBusyChange,
  onSaved,
}: {
  savedMinutes: number;
  /** Disconnected, or another policy write is in flight. */
  writeBlocked: boolean;
  onBusyChange: (busy: boolean) => void;
  onSaved: () => void;
}) {
  const [text, setText] = useState(String(savedMinutes));
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const minutes = parseTimeout(text);
  const dirty = minutes !== savedMinutes;

  const submit = async () => {
    if (minutes === null) return;
    setBusy(true);
    onBusyChange(true);
    setServerError(null);
    setSavedNote(false);
    try {
      await updateWorkerSessionPolicy(minutes);
      setSavedNote(true);
      onSaved();
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  };

  return (
    <>
      <div className="ad-configgrid">
        <label>
          Default timeout (minutes)
          <input
            className="field mono"
            type="number"
            min={TIMEOUT_MIN}
            max={TIMEOUT_MAX}
            step={1}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setSavedNote(false);
            }}
          />
        </label>
      </div>
      {minutes === null ? (
        <div className="err" role="alert">
          {TIMEOUT_ERROR}
        </div>
      ) : null}
      <ServerErrorNote message={serverError} />
      {savedNote ? (
        <div className="ad-savednote" role="status">
          ✓ Default timeout saved.
        </div>
      ) : null}
      <div className="row">
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || !dirty || minutes === null}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </>
  );
}

function AreaTimeoutDialog({
  area,
  defaultMinutes,
  writeBlocked,
  onCancel,
  onSaved,
}: {
  area: Area;
  defaultMinutes: number;
  writeBlocked: boolean;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [override, setOverride] = useState(
    area.workerSessionTimeoutMinutes !== null,
  );
  const [text, setText] = useState(
    String(area.workerSessionTimeoutMinutes ?? defaultMinutes),
  );
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const minutes = override ? parseTimeout(text) : null;
  const invalid = override && minutes === null;

  const submit = async () => {
    if (invalid) return;
    setBusy(true);
    setServerError(null);
    try {
      await updateArea(area.id, { workerSessionTimeoutMinutes: minutes });
      onSaved();
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const title = `Session timeout — ${area.name}`;
  return (
    <ModalDialog label={title} onClose={onCancel}>
      <h3>{title}</h3>
      <div className="ad-form">
        <label className="ad-check">
          <input
            type="radio"
            name="session-timeout-choice"
            checked={!override}
            onChange={() => setOverride(false)}
          />
          <span>Use the default ({defaultMinutes} minutes)</span>
        </label>
        <label className="ad-check">
          <input
            type="radio"
            name="session-timeout-choice"
            checked={override}
            onChange={() => setOverride(true)}
          />
          <span>Override</span>
        </label>
        {override ? (
          <AdminField label="Timeout (minutes)">
            <input
              className="field mono"
              type="number"
              min={TIMEOUT_MIN}
              max={TIMEOUT_MAX}
              step={1}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </AdminField>
        ) : null}
        {invalid ? (
          <div className="err" role="alert">
            {TIMEOUT_ERROR}
          </div>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || invalid}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </ModalDialog>
  );
}
