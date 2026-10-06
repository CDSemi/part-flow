import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  getCorrectionPermissionsPolicy,
  updateUndoReasonRequired,
} from '../../api/policies';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  PolicySwitch,
  SectionHeader,
  ServerErrorNote,
} from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Correction permissions (Phase 13; GUI_DESIGN §9
// Policies, PROJECT_PROFILE §16 "require a reason when configured"):
// the real Undo reason policy — one global On/Off switch (default Off)
// stored in the server's application policy
// (`/api/policies/correction-permissions`), audited and enforced by the
// server's Undo command. The switch saves on click and the stored value
// is re-read. Role-based correction permissions (who may undo or
// correct) are not configurable yet and the section says so — no
// control pretends otherwise.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'correction-permissions')
    ?.subtitle ?? '';

export function CorrectionPermissionsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const policyData = useApiData(getCorrectionPermissionsPolicy);
  const [busy, setBusy] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const header = (
    <SectionHeader title="Correction permissions" subtitle={SUBTITLE} />
  );

  if (policyData.state.status === 'loading') {
    return (
      <>
        {header}
        <LoadingState label="Loading section" />
      </>
    );
  }
  if (policyData.state.status === 'error') {
    return (
      <>
        {header}
        <ErrorState
          message="Correction permission settings could not be loaded."
          detail={policyData.state.message}
          onRetry={policyData.reload}
        />
      </>
    );
  }

  const required = policyData.state.data.undoReasonRequired;

  // Toggled from the last read; the server's answer is re-read so the
  // switch shows the stored value.
  const toggle = async () => {
    if (writeBlocked || busy) return;
    setBusy(true);
    setSwitchError(null);
    try {
      await updateUndoReasonRequired(!required);
      policyData.reload();
    } catch (error) {
      setSwitchError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {header}
      <div className="ad-config">
        <h2>Undo reason</h2>
        <p className="ad-confighelp">
          When On, every Undo at a Scan Station asks for a reason before the
          reversal can be confirmed, and a reversal without a reason is refused.
          The reason is recorded with the reversal and shown in Tracking. When
          Off, Undo asks for no reason.
        </p>
        <div className="ad-switchlist">
          <PolicySwitch
            label="Require a reason for every Undo"
            description="Applies to every Area and every Scan Station."
            ariaLabel="Require a reason for every Undo"
            on={required}
            disabled={writeBlocked || busy}
            onToggle={() => void toggle()}
          />
        </div>
        <ServerErrorNote message={switchError} />
        <h2>Who may undo or correct</h2>
        <p className="ad-confighelp">
          Role-based correction permissions are not configurable yet.
        </p>
      </div>
    </>
  );
}
