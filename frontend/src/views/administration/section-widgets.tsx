import type { ReactNode } from 'react';

// Small shared presentation pieces of the Administration sections —
// the section header row, the table + editor form primitives, and the
// status pill. Components only (React Fast Refresh), no data fetching
// and no business rules.

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

/** The server's message for a rejected configuration write. */
export function ServerErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="err" role="alert">
      {message}
    </div>
  );
}
