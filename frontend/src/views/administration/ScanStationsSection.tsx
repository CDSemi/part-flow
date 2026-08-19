import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  areaColor,
  createScanStation,
  listAreas,
  listScanStations,
  updateScanStation,
} from '../../api/environment';
import type { Area, ScanStation } from '../../api/environment';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  ActiveField,
  AdminField,
  SectionHeader,
  ServerErrorNote,
  StatusPill,
} from './section-widgets';

// Administration → Scan Stations (Phase 3.5): stations bound to one
// Area, identified by their stable Station ID (one URL path segment —
// letters, digits, '.', '_' and '-'). The Station ID is the identity
// and is never renamed; a station can be rebound to another active
// Area and deactivated, never deleted. Stations have no barcode.

type PendingDialog = { kind: 'new' } | { kind: 'edit'; station: ScanStation };

/** The canonical URL-safe Station ID shape (mirrors the backend). */
const STATION_ID_PATTERN = /^[A-Za-z0-9._-]+$/;

export function ScanStationsSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const stationsData = useApiData(listScanStations);
  const areasData = useApiData(listAreas);
  const [dialog, setDialog] = useState<PendingDialog | null>(null);

  const header = (ready: boolean, canCreate: boolean) => (
    <SectionHeader
      title="Scan Stations"
      subtitle="Stations bound to one Area — Station ID and active status"
      action={
        <button
          className="btn primary"
          disabled={!ready || !canCreate || writeBlocked}
          title={
            ready && !canCreate
              ? 'Scan Stations need an active Area first'
              : undefined
          }
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New Scan Station
        </button>
      }
    />
  );

  if (
    stationsData.state.status === 'loading' ||
    areasData.state.status === 'loading'
  ) {
    return (
      <>
        {header(false, false)}
        <LoadingState label="Loading Scan Stations" />
      </>
    );
  }
  if (
    stationsData.state.status === 'error' ||
    areasData.state.status === 'error'
  ) {
    const message =
      stationsData.state.status === 'error'
        ? stationsData.state.message
        : areasData.state.status === 'error'
          ? areasData.state.message
          : undefined;
    return (
      <>
        {header(false, false)}
        <ErrorState
          message="Scan Station data could not be loaded."
          detail={message}
          onRetry={() => {
            stationsData.reload();
            areasData.reload();
          }}
        />
      </>
    );
  }

  const stations = stationsData.state.data;
  const areas = areasData.state.data;
  const areaById = new Map(areas.map((area) => [area.id, area]));
  const activeAreas = areas.filter((area) => area.isActive);

  const completeWrite = () => {
    stationsData.reload();
    setDialog(null);
  };

  return (
    <>
      {header(true, activeAreas.length > 0)}
      {stations.length === 0 ? (
        <EmptyState message="No Scan Stations configured yet." />
      ) : (
        <table className="ad-table">
          <thead>
            <tr>
              <th>Station ID</th>
              <th>Area</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {stations.map((station) => {
              const area = areaById.get(station.areaId);
              return (
                <tr
                  key={station.stationId}
                  className="selrow"
                  onClick={() => setDialog({ kind: 'edit', station })}
                >
                  <td>
                    <button
                      className="rowbtn"
                      aria-label={`Edit ${station.stationId}`}
                    >
                      <b className="mono">{station.stationId}</b>
                    </button>
                  </td>
                  <td data-label="Area">
                    <AreaDot colorVar={areaColor(area)} size={11} />{' '}
                    {area?.name ?? '—'}
                  </td>
                  <td>
                    <StatusPill active={station.isActive} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="ad-notice">
        The Station ID is the station's stable identity and its address (Scan
        Station → <span className="mono">/scan-station/&lt;id&gt;</span>) — it
        is never renamed. Stations are identified by this ID and their Area
        binding; there is no station barcode.
      </div>
      {dialog?.kind === 'new' ? (
        <ScanStationDialog
          areas={activeAreas}
          areaById={areaById}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await createScanStation({
              stationId: input.stationId,
              areaId: input.areaId,
              isActive: input.isActive,
            });
            completeWrite();
          }}
        />
      ) : null}
      {dialog?.kind === 'edit' ? (
        <ScanStationDialog
          station={dialog.station}
          areas={activeAreas}
          areaById={areaById}
          writeBlocked={writeBlocked}
          onCancel={() => setDialog(null)}
          onSave={async (input) => {
            await updateScanStation(dialog.station.stationId, {
              // Rebinding is Application-controlled (active Areas
              // only) — an unchanged binding is not resubmitted.
              ...(input.areaId !== dialog.station.areaId
                ? { areaId: input.areaId }
                : {}),
              isActive: input.isActive,
            });
            completeWrite();
          }}
        />
      ) : null}
    </>
  );
}

function ScanStationDialog({
  station,
  areas,
  areaById,
  writeBlocked,
  onCancel,
  onSave,
}: {
  station?: ScanStation;
  areas: Area[];
  areaById: Map<number, Area>;
  writeBlocked: boolean;
  onCancel: () => void;
  /** Persist the entry. Rejects with the server's message. */
  onSave: (input: {
    stationId: string;
    areaId: number;
    isActive: boolean;
  }) => Promise<void>;
}) {
  const [stationId, setStationId] = useState(station?.stationId ?? '');
  const [areaId, setAreaId] = useState(station?.areaId ?? areas[0]?.id ?? 0);
  // Rebinding targets active Areas; a station currently bound to an
  // inactive Area still shows that binding as a selectable no-change
  // choice instead of a blank select.
  const currentInactiveArea =
    station && !areas.some((area) => area.id === station.areaId)
      ? areaById.get(station.areaId)
      : undefined;
  const choices = currentInactiveArea ? [currentInactiveArea, ...areas] : areas;
  const [isActive, setIsActive] = useState(station?.isActive ?? true);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const trimmedId = stationId.trim();
  const idInvalid = trimmedId !== '' && !STATION_ID_PATTERN.test(trimmedId);

  const submit = async () => {
    if (!station && (!trimmedId || idInvalid)) {
      setAttempted(true);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      await onSave({
        stationId: station?.stationId ?? trimmedId,
        areaId,
        isActive,
      });
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalDialog
      label={station ? 'Edit Scan Station' : 'New Scan Station'}
      onClose={onCancel}
    >
      <h3>{station ? 'Edit Scan Station' : 'New Scan Station'}</h3>
      <div className="ad-form">
        {station ? (
          <div className="ad-identity">
            <div className="idrow">
              <span className="k">Station ID</span>
              <span className="v mono">{station.stationId}</span>
            </div>
            <p className="ad-idnote">
              The Station ID is the stable identity and is never renamed.
            </p>
          </div>
        ) : (
          <>
            <AdminField label="Station ID">
              <input
                className="field mono"
                value={stationId}
                onChange={(event) => setStationId(event.target.value)}
                placeholder="e.g. LATHE-ST-02"
              />
            </AdminField>
            {idInvalid || (!trimmedId && attempted) ? (
              <div className="err" role="alert">
                {idInvalid
                  ? "Station IDs may only contain letters, digits, '.', '_' and '-'."
                  : 'A Station ID is required.'}
              </div>
            ) : null}
          </>
        )}
        <AdminField label="Area">
          <select
            className="field"
            value={String(areaId)}
            onChange={(event) => setAreaId(Number(event.target.value))}
          >
            {choices.map((area) => (
              <option key={area.id} value={String(area.id)}>
                {area.isActive ? area.name : `${area.name} (inactive)`}
              </option>
            ))}
          </select>
        </AdminField>
        <ActiveField
          label="Active — the station accepts scans"
          checked={isActive}
          onChange={setIsActive}
        />
        <ServerErrorNote message={serverError} />
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy}
          onClick={() => void submit()}
        >
          {station ? 'Save changes' : 'Add Scan Station'}
        </button>
      </div>
    </ModalDialog>
  );
}
