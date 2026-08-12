import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/**
 * Accessible dialog primitive: role="dialog", aria-modal, an accessible
 * name (aria-labelledby for a visible heading, or aria-label), keyboard
 * focus trapping, Escape and backdrop close requests, initial focus
 * inside the dialog, and focus restoration to the opener on close.
 *
 * `onClose` is a close REQUEST — the owner decides whether to close
 * immediately or first confirm discarding unsaved input. Dialogs never
 * perform production writes in Phase 2.
 */
export function ModalDialog({
  label,
  labelledBy,
  onClose,
  children,
  size,
  className,
  onKeyDown,
}: {
  /** Accessible name when no visible heading is referenced. */
  label?: string;
  /** id of the visible heading that names the dialog (preferred). */
  labelledBy?: string;
  onClose: () => void;
  children: ReactNode;
  /** Responsive width step; every size stays within the viewport. */
  size?: 'wide' | 'xwide';
  /** Extra class on the dialog surface (e.g. `msgdlg` for the
      message/confirmation spacing variant). */
  className?: string;
  /**
   * Dialog-wide key handling (e.g. physical-keyboard quantity entry).
   * Attached at the dialog root so keys work while the dialog itself
   * holds focus; Escape and focus trapping stay owned by ModalDialog.
   */
  onKeyDown?: (event: React.KeyboardEvent) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);

  useEffect(() => {
    // Capture the opener only while focus is still outside the dialog —
    // under StrictMode's double-invoked effects, content may already
    // have claimed initial focus inside the dialog.
    if (!dialogRef.current?.contains(document.activeElement)) {
      openerRef.current = document.activeElement;
      dialogRef.current?.focus();
    }
    return () => {
      if (openerRef.current instanceof HTMLElement) {
        openerRef.current.focus();
      }
    };
  }, []);

  function trapFocus(event: React.KeyboardEvent) {
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE),
    );
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialogRef.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={`dlg ${size ?? ''} ${className ?? ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onClose();
            return;
          }
          trapFocus(event);
          onKeyDown?.(event);
        }}
      >
        {children}
      </div>
    </div>
  );
}
