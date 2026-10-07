import type { ReactNode } from 'react';

import type { Permission } from '../../api/roles';
import { PERMISSION_LABELS } from './permissions';

// Small shared presentation pieces of the Administration sections —
// the section header row, the view-only note, the table + editor form
// primitives, the status pill, the policy switch and its read-only
// form. Components only (React Fast Refresh), no data fetching and no
// business rules.

/**
 * One section's heading row: title, subtitle, and the section-owned
 * entry action on the right (the `+ New …` button of an entry table;
 * settings forms render none).
 */
export function SectionHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle: string;
  action?: ReactNode;
}) {
  return (
    <div className="ad-top">
      <div>
        <h1>{title}</h1>
        <div className="sub">{subtitle}</div>
      </div>
      <span className="spacer" />
      {action}
    </div>
  );
}

/**
 * The one line a view-only section shows under its header: the
 * signed-in user may read the section, and changing it needs the named
 * permission (the server checks it; the controls are hidden).
 */
export function ViewOnlyNote({ permission }: { permission: Permission }) {
  return (
    <p className="ad-confighelp">
      View only — changing this needs the {PERMISSION_LABELS[permission]}{' '}
      permission.
    </p>
  );
}

/**
 * The name cell content of a configuration table row: the keyboard and
 * screen-reader entry point of the row's editor when the row is
 * editable, plain content otherwise.
 */
export function RowOpener({
  editable,
  label,
  children,
}: {
  editable: boolean;
  /** Accessible name of the editor entry point (e.g. "Edit Lathe"). */
  label: string;
  children: ReactNode;
}) {
  if (!editable) return <>{children}</>;
  return (
    <button className="rowbtn" aria-label={label}>
      {children}
    </button>
  );
}

/** Stacked label + control of the Administration editor dialogs. */
export function AdminField({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  // The label text (including any parenthesized qualifier span) is ONE
  // inline flex item — the stacked flex-column label must never place
  // a qualifier on its own row between the text and the control.
  return (
    <label>
      <span>{label}</span>
      {children}
    </label>
  );
}

/** Active / Inactive status pill of the configuration tables. */
export function StatusPill({ active }: { active: boolean }) {
  return (
    <span className={`pillnav ${active ? 'on' : 'off'}`}>
      {active ? 'Active' : 'Inactive'}
    </span>
  );
}

/**
 * The Active checkbox row of an editor dialog. Activation rules
 * (hierarchy, held quantity) are enforced by the server — a rejected
 * save renders its explanation, nothing is silently confirmed through.
 */
export function ActiveField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="ad-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

/**
 * One On/Off policy switch of a settings section (the `ad-switchlist`
 * presentation). The owner saves on click and re-reads the stored
 * value; the switch only renders what it is given.
 */
export function PolicySwitch({
  label,
  description,
  ariaLabel,
  on,
  disabled,
  onToggle,
}: {
  label: string;
  description: string;
  ariaLabel: string;
  on: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={ariaLabel}
      className={`ad-switch${on ? ' on' : ''}`}
      disabled={disabled}
      onClick={onToggle}
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
}

/**
 * Stored settings shown as text (the read-only form of a settings panel
 * for a user who may not change them).
 */
export function ReadOnlyValues({
  rows,
}: {
  rows: readonly { label: string; value: ReactNode }[];
}) {
  return (
    <div className="ad-configpreview">
      {rows.map((row) => (
        <div key={row.label} className="prow">
          <span className="k">{row.label}</span>
          <span className="v">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

/** The server's message for a rejected configuration write. */
export function ServerErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="err" role="alert">
      {message}
    </div>
  );
}
