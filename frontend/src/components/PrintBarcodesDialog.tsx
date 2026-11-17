import './print-barcodes.css';

import { useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { ModalDialog } from './ModalDialog';
import {
  checkState,
  printOrder,
  selectedCountText,
  shownKeys,
  toggleKey,
  toggleKeys,
} from './print-barcodes';
import type { BarcodeSection, CheckState } from './print-barcodes';

/**
 * The ONE Print barcodes dialog (GUI_DESIGN §12.6, §14.5): a grid of
 * selectable label cards, optionally grouped, with a group checkbox per
 * group, one `Select all`, the selected count, and the fixed footer
 * `Cancel (Esc)` / `Print Labels`. Only the card area scrolls.
 *
 * Each caller supplies its cards (`renderCard`, an on-screen card in
 * the theme colors), its printed label (`renderLabel`, the SAME label
 * as its single-label dialog) and its groups; the Part Numbers caller
 * adds its own controls (`controls`). The selection lives here, keyed
 * by card: it survives changes of the shown cards (search, sort,
 * grouping), a key shown in several groups is ONE selection, and
 * `Print Labels` prints every selected label once — shown or not.
 *
 * Printing renders the selected labels into a print sheet that is a
 * direct child of <body> (a portal, hidden on screen, present only
 * while the dialog is open); the print styles show only that sheet,
 * label after label left to right, row after row, page after page,
 * never splitting a label.
 */
export function PrintBarcodesDialog({
  title,
  description,
  controls,
  sections,
  body,
  emptyMessage,
  footerNote,
  renderCard,
  cardName,
  renderLabel,
  onClose,
}: {
  /** Dialog title, e.g. `Print Machine barcodes`. */
  title: string;
  description: ReactNode;
  /** Extra controls above the selection bar (grouping, search, sort). */
  controls?: ReactNode;
  /** The cards shown, grouped or as one untitled section. */
  sections: readonly BarcodeSection[];
  /** Replaces the card area (loading or error state). */
  body?: ReactNode;
  /** Shown when no card is listed. */
  emptyMessage: string;
  /** After the cards, inside the scrolling area (bounded-list line). */
  footerNote?: ReactNode;
  renderCard: (key: string) => ReactNode;
  /** Accessible name of one card. */
  cardName: (key: string) => string;
  /** The printed label of one key. */
  renderLabel: (key: string) => ReactNode;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const shown = shownKeys(sections);
  const shownSet = new Set(shown);
  const hiddenCount = [...selected].filter((key) => !shownSet.has(key)).length;
  const toPrint = printOrder(selected, shown);

  const cards = body ?? (
    <>
      {shown.length === 0 ? (
        <p className="pbx-empty">{emptyMessage}</p>
      ) : (
        sections
          .filter((section) => section.itemKeys.length > 0)
          .map((section) => {
            const keys = [...new Set(section.itemKeys)];
            const groupState = checkState(selected, keys);
            const groupCount = keys.filter((key) => selected.has(key)).length;
            return (
              <section
                key={section.key}
                className="pbx-group"
                aria-label={section.titleText}
              >
                {section.title !== undefined ? (
                  <div className="pbx-grouphead">
                    <CheckButton
                      state={groupState}
                      label={`Select all in ${section.titleText ?? ''}`}
                      onToggle={() => setSelected((s) => toggleKeys(s, keys))}
                    >
                      <span className="pbx-grouptitle">{section.title}</span>
                    </CheckButton>
                    <span className="pbx-groupcount">
                      {groupCount} of {keys.length}
                    </span>
                  </div>
                ) : null}
                <div className="pbx-grid">
                  {keys.map((key) => {
                    const on = selected.has(key);
                    const toggle = () => setSelected((s) => toggleKey(s, key));
                    return (
                      <div
                        key={key}
                        role="checkbox"
                        aria-checked={on}
                        aria-label={cardName(key)}
                        tabIndex={0}
                        className={`pbx-card${on ? ' on' : ''}`}
                        onClick={toggle}
                        onKeyDown={(event) => {
                          if (event.key === ' ' || event.key === 'Enter') {
                            event.preventDefault();
                            toggle();
                          }
                        }}
                      >
                        <CheckBox state={on ? 'on' : 'off'} />
                        {renderCard(key)}
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })
      )}
      {footerNote}
    </>
  );

  return (
    <ModalDialog
      label={title}
      onClose={onClose}
      size="xwide"
      className="pbx-dlg"
    >
      <div className="pbx-head">
        <h3>{title}</h3>
        <div className="sub">{description}</div>
        {controls}
        <div className="pbx-bar">
          <CheckButton
            state={shown.length === 0 ? 'off' : checkState(selected, shown)}
            disabled={shown.length === 0}
            onToggle={() => setSelected((s) => toggleKeys(s, shown))}
          >
            Select all
          </CheckButton>
          <span className="pbx-count" role="status">
            <b>{selectedCountText(selected.size)}</b>
            {hiddenCount > 0 ? ` · ${hiddenCount} not shown` : null}
          </span>
        </div>
      </div>
      <div className="pbx-scroll">{cards}</div>
      <div className="row pbx-foot">
        <button type="button" className="bigbtn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <button
          type="button"
          className="bigbtn primary"
          disabled={selected.size === 0}
          onClick={() => window.print()}
        >
          Print Labels
        </button>
      </div>
      {createPortal(
        <div className="pbx-sheet" aria-hidden="true">
          {toPrint.map((key) => (
            <div key={key} className="pbx-plabel">
              {renderLabel(key)}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ModalDialog>
  );
}

/** The visual checkbox square (on, off or mixed). */
function CheckBox({ state }: { state: CheckState }) {
  return <span className={`pbx-box ${state}`} aria-hidden="true" />;
}

/** A group or `Select all` checkbox: `mixed` while partly selected. */
function CheckButton({
  state,
  label,
  disabled,
  onToggle,
  children,
}: {
  state: CheckState;
  /** Accessible name when the visible text is not enough. */
  label?: string;
  disabled?: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={state === 'mixed' ? 'mixed' : state === 'on'}
      aria-label={label}
      disabled={disabled}
      className="pbx-checkbtn"
      onClick={onToggle}
    >
      <CheckBox state={state} />
      {children}
    </button>
  );
}
