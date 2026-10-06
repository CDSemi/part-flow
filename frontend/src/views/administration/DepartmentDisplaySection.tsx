import { useEffect, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { listDepartments, updateDepartment } from '../../api/environment';
import type { Department } from '../../api/environment';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  BOARD_MIN_PAGE_SECONDS_RANGE,
  BOARD_SECONDS_PER_ROW_RANGE,
  isBoardMinPageSeconds,
  isBoardSecondsPerRow,
  rotationDurationMs,
} from '../production-board/board-logic';
import { AdminField, SectionHeader, ServerErrorNote } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Department display settings (Phase 13; GUI_DESIGN §9
// Policies, PROJECT_PROFILE §21): the Production Board rotation timing
// of each Department — whole seconds per displayed row and the minimum
// page dwell — configured per Department, never globally. Stored on the
// Department (`PATCH /api/departments/{id}`), audited and re-validated
// by the server; a running board applies a change at its next refresh.
// The editor sends only the fields it changed (the PATCH is partial), so
// a stale read never overwrites another administrator's change to the
// other field.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'department-display')
    ?.subtitle ?? '';

const SECONDS_PER_ROW_ERROR =
  'Seconds per displayed row must be a whole number from 1 to 60.';
const MIN_PAGE_SECONDS_ERROR =
  'The minimum page dwell must be a whole number of seconds from 1 to 300.';

/** The whole number the text holds when `isValid` admits it, else null
 * (never rounded or clamped). */
function parseSetting(
  text: string,
  isValid: (value: unknown) => value is number,
): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return isValid(value) ? value : null;
}

export function DepartmentDisplaySection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const departmentsData = useApiData(listDepartments);
  const [editing, setEditing] = useState<Department | null>(null);

  const header = (
    <SectionHeader title="Department display settings" subtitle={SUBTITLE} />
  );

  if (departmentsData.state.status === 'loading') {
    return (
      <>
        {header}
        <LoadingState label="Loading section" />
      </>
    );
  }
  if (departmentsData.state.status === 'error') {
    return (
      <>
        {header}
        <ErrorState
          message="Department display settings could not be loaded."
          detail={departmentsData.state.message}
          onRetry={departmentsData.reload}
        />
      </>
    );
  }

  const departments = departmentsData.state.data;

  return (
    <>
      {header}
      <div className="ad-config">
        <h2>Production Board rotation</h2>
        <p className="ad-confighelp">
          Each page of a Department&apos;s Production Board stays on screen for
          a time proportional to the rows it shows, and never shorter than the
          minimum page dwell. Configured per Department; a running board applies
          a change at its next refresh.
        </p>
        {departments.length === 0 ? (
          <p className="ad-confighelp">
            No Departments yet. Create one in Departments.
          </p>
        ) : (
          <table className="ad-table">
            <thead>
              <tr>
                <th>Department</th>
                <th>Seconds per displayed row</th>
                <th>Minimum page dwell</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {departments.map((department) => (
                <tr key={department.id}>
                  <td>
                    <b>{department.name}</b>
                    {department.isActive ? null : ' (inactive)'}
                  </td>
                  <td className="mono" data-label="Seconds per displayed row">
                    {department.boardSecondsPerRow} s
                  </td>
                  <td className="mono" data-label="Minimum page dwell">
                    {department.boardMinPageSeconds} s
                  </td>
                  <td>
                    <button
                      className="btn"
                      aria-label={`Edit rotation timing — ${department.name}`}
                      onClick={() => setEditing(department)}
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {editing ? (
        <RotationTimingDialog
          department={editing}
          writeBlocked={writeBlocked}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            departmentsData.reload();
          }}
        />
      ) : null}
    </>
  );
}

function RotationTimingDialog({
  department,
  writeBlocked,
  onCancel,
  onSaved,
}: {
  department: Department;
  writeBlocked: boolean;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [secondsText, setSecondsText] = useState(
    String(department.boardSecondsPerRow),
  );
  const [dwellText, setDwellText] = useState(
    String(department.boardMinPageSeconds),
  );
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // The first field takes focus once the dialog opened. Focused from an
  // effect (after ModalDialog's own), never `autoFocus`: ModalDialog
  // records the opener only while focus is still outside the dialog,
  // and closing must return focus to the row's Edit button.
  const secondsField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    secondsField.current?.focus();
  }, []);

  const secondsPerRow = parseSetting(secondsText, isBoardSecondsPerRow);
  const minPageSeconds = parseSetting(dwellText, isBoardMinPageSeconds);
  // Only the fields whose value differs from the value the dialog
  // opened with are sent.
  const patch = {
    ...(secondsPerRow !== null &&
    secondsPerRow !== department.boardSecondsPerRow
      ? { boardSecondsPerRow: secondsPerRow }
      : {}),
    ...(minPageSeconds !== null &&
    minPageSeconds !== department.boardMinPageSeconds
      ? { boardMinPageSeconds: minPageSeconds }
      : {}),
  };
  const invalid = secondsPerRow === null || minPageSeconds === null;
  const dirty = Object.keys(patch).length > 0;

  const submit = async () => {
    if (invalid || !dirty) return;
    setBusy(true);
    setServerError(null);
    try {
      await updateDepartment(department.id, patch);
      onSaved();
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const title = `Production Board rotation — ${department.name}`;
  const rotation =
    secondsPerRow !== null && minPageSeconds !== null
      ? { secondsPerRow, minPageSeconds }
      : null;
  return (
    <ModalDialog label={title} onClose={onCancel}>
      <h3>{title}</h3>
      <div className="ad-form">
        <AdminField label="Seconds per displayed row">
          <input
            className="field mono"
            type="number"
            min={BOARD_SECONDS_PER_ROW_RANGE[0]}
            max={BOARD_SECONDS_PER_ROW_RANGE[1]}
            step={1}
            ref={secondsField}
            value={secondsText}
            onChange={(event) => setSecondsText(event.target.value)}
          />
        </AdminField>
        {secondsPerRow === null ? (
          <div className="err" role="alert">
            {SECONDS_PER_ROW_ERROR}
          </div>
        ) : null}
        <AdminField label="Minimum page dwell (seconds)">
          <input
            className="field mono"
            type="number"
            min={BOARD_MIN_PAGE_SECONDS_RANGE[0]}
            max={BOARD_MIN_PAGE_SECONDS_RANGE[1]}
            step={1}
            value={dwellText}
            onChange={(event) => setDwellText(event.target.value)}
          />
        </AdminField>
        {minPageSeconds === null ? (
          <div className="err" role="alert">
            {MIN_PAGE_SECONDS_ERROR}
          </div>
        ) : null}
        {rotation !== null ? (
          <p className="ad-confighelp">
            {`A page showing 1 row stays ${rotationDurationMs(1, rotation) / 1000} s; a page showing 10 rows stays ${rotationDurationMs(10, rotation) / 1000} s.`}
          </p>
        ) : null}
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || invalid || !dirty}
          onClick={() => void submit()}
        >
          Save
        </button>
      </div>
    </ModalDialog>
  );
}
