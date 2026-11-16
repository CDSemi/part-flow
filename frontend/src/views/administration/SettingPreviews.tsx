import type { ReactNode } from 'react';

import {
  RELEASE_IDLE_RELOAD_AFTER_MS,
  RELEASE_RELOAD_COUNTDOWN_MS,
} from '../../app/release-reload';
import { AREA_BOARD_REFRESH_MS } from '../area-board/area-board-feed';
import { formatIsoDate } from '../dates';
import { BOARD_REFRESH_MS } from '../production-board/board-logic';
import { PolicySwitch } from './section-widgets';

// Setting previews (Phase 17, owner decision of 2026-11-13; GUI_DESIGN §9):
// the candidate settings of Scan behavior and of the general Settings,
// shown as disabled controls, each marked Not available yet. A control
// shows what PartFlow does today — the constants and behavior the app
// already has — and nothing here can be changed or saved: each becomes a
// working setting only when the owner decides it. The previews look the
// same to every signed-in user, whatever the section's permission.

function NotAvailableTag() {
  return <span className="admin-preview-tag">Not available yet</span>;
}

function noop() {
  // A preview control changes nothing.
}

/** A switch-shaped candidate setting, disabled, in its current state. */
function PreviewSwitch({
  label,
  description,
  on,
}: {
  label: string;
  description: string;
  on: boolean;
}) {
  return (
    <PolicySwitch
      label={label}
      description={description}
      ariaLabel={label}
      on={on}
      disabled
      onToggle={noop}
      note={<NotAvailableTag />}
    />
  );
}

/** A value-shaped candidate setting: its disabled control on the right. */
function PreviewValue({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="admin-switch admin-preview">
      <span className="swtext">
        <span className="swlabel">{label}</span>
        <span className="swdesc">{description}</span>
        <NotAvailableTag />
      </span>
      <span className="admin-preview-control">{children}</span>
    </div>
  );
}

function seconds(milliseconds: number): string {
  return String(Math.round(milliseconds / 1000));
}

/** The candidate settings of Administration → Scan behavior. */
export function ScanBehaviorPreviews() {
  return (
    <div className="admin-switchlist admin-previews">
      <PreviewSwitch
        label="Success sound"
        description="Play a sound when a scan is accepted."
        on={false}
      />
      <PreviewSwitch
        label="Error sound"
        description="Play a sound when a scan is refused."
        on={false}
      />
      <PreviewValue
        label="Ignore a repeated scan"
        description="Ignore the same code scanned again within this many seconds (0 = never)."
      >
        <input
          aria-label="Ignore a repeated scan"
          value="0"
          disabled
          readOnly
        />
        <span className="admin-preview-unit">s</span>
      </PreviewValue>
      <PreviewSwitch
        label="Typed entry at Scan Stations"
        description="Allow typing a code when the scanner is unavailable."
        on
      />
      <PreviewValue
        label="Scanner ending key"
        description="The key the scanner sends after each code."
      >
        <select aria-label="Scanner ending key" value="ENTER" disabled>
          <option value="ENTER">Enter</option>
          <option value="TAB">Tab</option>
        </select>
      </PreviewValue>
      <PreviewValue
        label="Prefix to strip"
        description="Characters the scanner adds before each code."
      >
        <input
          aria-label="Prefix to strip"
          value=""
          placeholder="None"
          disabled
          readOnly
        />
      </PreviewValue>
      <PreviewValue
        label="Suffix to strip"
        description="Characters the scanner adds after each code."
      >
        <input
          aria-label="Suffix to strip"
          value=""
          placeholder="None"
          disabled
          readOnly
        />
      </PreviewValue>
      <PreviewSwitch
        label="Confirm a single match automatically"
        description="Skip the confirmation when exactly one result matches the scan."
        on={false}
      />
      <PreviewValue
        label="Default quantity"
        description="The quantity a step that moves existing quantity starts with. A received or added quantity always starts empty."
      >
        <select aria-label="Default quantity" value="FULL" disabled>
          <option value="FULL">Full available quantity</option>
          <option value="EMPTY">Empty</option>
        </select>
      </PreviewValue>
    </div>
  );
}

/** The candidate general settings of Administration → Settings. */
export function GeneralSettingsPreviews() {
  return (
    <div className="admin-switchlist admin-previews">
      <PreviewValue
        label="Site name"
        description="The name shown in the top navigation."
      >
        <input aria-label="Site name" value="PartFlow" disabled readOnly />
      </PreviewValue>
      <PreviewValue label="Logo" description="The image shown beside the name.">
        <button type="button" className="btn ghost" disabled>
          Choose image…
        </button>
      </PreviewValue>
      <PreviewValue
        label="Time zone"
        description="The site calendar of due dates and done dates, set when PartFlow is installed."
      >
        <select aria-label="Time zone" value="SERVER" disabled>
          <option value="SERVER">Set on the server</option>
        </select>
      </PreviewValue>
      <PreviewValue label="Date format" description="How dates are written.">
        <select aria-label="Date format" value="MONTH_DAY_YEAR" disabled>
          <option value="MONTH_DAY_YEAR">{formatIsoDate('2026-07-24')}</option>
        </select>
      </PreviewValue>
      <PreviewValue
        label="Production Board refresh"
        description="How often the Production Board reads new figures."
      >
        <input
          aria-label="Production Board refresh"
          value={seconds(BOARD_REFRESH_MS)}
          disabled
          readOnly
        />
        <span className="admin-preview-unit">s</span>
      </PreviewValue>
      <PreviewValue
        label="Area Board refresh"
        description="How often the Area Board reads new figures."
      >
        <input
          aria-label="Area Board refresh"
          value={seconds(AREA_BOARD_REFRESH_MS)}
          disabled
          readOnly
        />
        <span className="admin-preview-unit">s</span>
      </PreviewValue>
      <PreviewValue
        label="Update countdown"
        description={`The countdown before a page reloads into a new release. Pages other than the Scan Station and the Production Board also wait until nobody has used them for ${seconds(RELEASE_IDLE_RELOAD_AFTER_MS)} s.`}
      >
        <input
          aria-label="Update countdown"
          value={seconds(RELEASE_RELOAD_COUNTDOWN_MS)}
          disabled
          readOnly
        />
        <span className="admin-preview-unit">s</span>
      </PreviewValue>
    </div>
  );
}
