import { Fragment } from 'react';

import { useApiData } from '../../api/use-api-data';
import { listWorkers } from '../../api/workers';
import { DevNotice } from '../../components/DevNotice';
import { DemoBarcode } from './scan-station-presentation';

// DEVELOPMENT-ONLY module: the demo badges of the Worker sign-in modal.
// Reached only through the import.meta.env.DEV-guarded lazy import in
// scan-station-sign-in-dialog.tsx, so production bundles never include
// it. The badges are the REAL active Workers' badges from the Workers
// registry; a click runs the same submit path as a wedge scan.

export function DevBadges({ onScan }: { onScan: (badge: string) => void }) {
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
              <DemoBarcode value={worker.badgeBarcode} onScan={onScan} />{' '}
              {worker.name}
            </Fragment>
          ))}
    </DevNotice>
  );
}
