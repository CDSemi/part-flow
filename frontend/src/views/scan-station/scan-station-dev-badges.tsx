import { Fragment, useContext } from 'react';

import { useApiData } from '../../api/use-api-data';
import { listWorkers } from '../../api/workers';
import { SessionContext, hasPermission } from '../../app/session-context';
import { DevNotice } from '../../components/DevNotice';
import { DemoBarcode } from './scan-station-presentation';

// DEVELOPMENT-ONLY module: the demo badges of the Worker sign-in modal
// and the badge-confirmation gate. Reached only through the
// import.meta.env.DEV-guarded lazy import in
// scan-station-dev-badges-slot.tsx, so production bundles never include
// it. The badges are the REAL active Workers' badges from the Workers
// registry; a click runs the same submit path as a wedge scan. The
// server sends badges only to users who may manage Workers, so the
// badges are listed only while such a user is signed in in this browser
// (the station itself never needs a sign-in, so the session is read
// without requiring a provider).

export function DevBadges({ onScan }: { onScan: (badge: string) => void }) {
  const session = useContext(SessionContext);
  if (!hasPermission(session?.user ?? null, 'MANAGE_WORKERS')) {
    return (
      <DevNotice>
        Demo badges are listed only while a user who may manage Workers is
        signed in in this browser.
      </DevNotice>
    );
  }
  return <DevBadgeList onScan={onScan} />;
}

function DevBadgeList({ onScan }: { onScan: (badge: string) => void }) {
  const workers = useApiData(listWorkers);
  if (workers.state.status !== 'ready') return null;
  const active = workers.state.data.filter((worker) => worker.isActive);
  return (
    <DevNotice>
      Demo badges (development build only) — click one to simulate a badge scan:{' '}
      {active.length === 0
        ? 'no active Workers yet'
        : active.map((worker, index) => (
            <Fragment key={worker.id}>
              {index > 0 ? ' · ' : null}
              {worker.badgeBarcode === null ? null : (
                <>
                  <DemoBarcode
                    value={worker.badgeBarcode}
                    onScan={onScan}
                  />{' '}
                </>
              )}
              {worker.name}
            </Fragment>
          ))}
    </DevNotice>
  );
}
