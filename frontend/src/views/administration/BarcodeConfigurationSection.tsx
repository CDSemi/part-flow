import { useState } from 'react';

import { errorMessage } from '../../api/client';
import {
  getMachineAssetTagFormat,
  putMachineAssetTagFormat,
} from '../../api/environment';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { ErrorState, LoadingState } from '../../components/view-states';
import {
  MACHINE_BARCODE_NAMESPACE,
  formatAssetTag,
  machineBarcode,
} from '../asset-tags';
import { SectionHeader, ServerErrorNote } from './section-widgets';

/**
 * Administration → Barcode configuration (Phase 3.5): the PF:
 * namespace reference and the Machine Asset Tag format — deliberately
 * a simple prefix + zero-padded numeric sequence, never a template
 * engine. Machine barcodes carry the Asset Tag directly
 * (`PF:MACHINE:<asset-tag>`), so no separate Machine barcode value is
 * ever configured or entered. The Next Asset Tag preview reads the
 * server's persisted never-reuse counter; allocation itself is owned
 * by Machine creation. A settings form, not an entry table.
 */
export function BarcodeConfigurationSection() {
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const formatData = useApiData(getMachineAssetTagFormat);

  const header = (
    <SectionHeader
      title="Barcode configuration"
      subtitle="PF: prefix scheme, Machine Asset Tag format, label printing"
    />
  );

  if (formatData.state.status === 'loading') {
    return (
      <>
        {header}
        <LoadingState label="Loading barcode configuration" />
      </>
    );
  }
  if (formatData.state.status === 'error') {
    return (
      <>
        {header}
        <ErrorState
          message="The barcode configuration could not be loaded."
          detail={formatData.state.message}
          onRetry={formatData.reload}
        />
      </>
    );
  }

  return (
    <>
      {header}
      <FormatPanel
        saved={formatData.state.data}
        writeBlocked={writeBlocked}
        onSaved={formatData.reload}
      />
    </>
  );
}

function FormatPanel({
  saved,
  writeBlocked,
  onSaved,
}: {
  /** The persisted configuration, or null while none exists yet. */
  saved: { prefix: string; digits: number; nextSequence: number } | null;
  writeBlocked: boolean;
  onSaved: () => void;
}) {
  const [prefix, setPrefix] = useState(saved?.prefix ?? '');
  const [digitsText, setDigitsText] = useState(String(saved?.digits ?? 4));
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const parsedDigits = Number.parseInt(digitsText, 10);
  const digits = Number.isNaN(parsedDigits)
    ? (saved?.digits ?? 4)
    : Math.min(8, Math.max(1, parsedDigits));
  const trimmedPrefix = prefix.trim();
  const prefixError = /[\s:]/.test(trimmedPrefix)
    ? 'The prefix cannot contain spaces or “:”.'
    : null;
  const format = { prefix: trimmedPrefix, digits };
  // The next tag against the server's persisted counter — a format
  // change never resets the sequence, so the preview under an edited
  // format still uses the same next number.
  const next = formatAssetTag(format, saved?.nextSequence ?? 1);
  const dirty =
    saved === null || trimmedPrefix !== saved.prefix || digits !== saved.digits;

  const submit = async () => {
    if (prefixError) return;
    setBusy(true);
    setServerError(null);
    setSavedNote(false);
    try {
      await putMachineAssetTagFormat({ prefix: trimmedPrefix, digits });
      setSavedNote(true);
      onSaved();
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ad-config">
      <h2>Machine Asset Tag format</h2>
      {saved === null ? (
        <div className="ad-notice">
          The Machine Asset Tag format has not been configured yet. Machines
          cannot be created until it is saved here.
        </div>
      ) : null}
      <p className="ad-confighelp">
        Every Machine receives its Asset Tag automatically when it is created in
        Management → Machines: the configured prefix followed by the next number
        in sequence, zero-padded to the configured length. Asset Tags are
        unique, are never reused — retired Machines keep theirs — and never
        change after creation. The Machine barcode is the Asset Tag in the{' '}
        <code>{MACHINE_BARCODE_NAMESPACE}</code> namespace.
      </p>
      <div className="ad-configgrid">
        <label>
          Prefix
          <input
            className="field mono"
            value={prefix}
            onChange={(event) => {
              setPrefix(event.target.value);
              setSavedNote(false);
            }}
            placeholder="e.g. CD-"
          />
        </label>
        <label>
          Number length (digits)
          <input
            className="field mono"
            type="number"
            min={1}
            max={8}
            value={digitsText}
            onChange={(event) => {
              setDigitsText(event.target.value);
              setSavedNote(false);
            }}
          />
        </label>
      </div>
      {prefixError ? (
        <div className="err" role="alert">
          {prefixError}
        </div>
      ) : null}
      <div className="ad-configpreview">
        <div className="prow">
          <span className="k">Asset Tags</span>
          <span className="v">
            {formatAssetTag(format, 1)}, {formatAssetTag(format, 2)}, …
          </span>
        </div>
        <div className="prow">
          <span className="k">Next Asset Tag</span>
          <span className="v">{next}</span>
        </div>
        <div className="prow">
          <span className="k">Scanned barcode</span>
          {/* Shared app-wide barcode reading tone (global .barcodeval). */}
          <span className="v barcodeval">{machineBarcode(next)}</span>
        </div>
      </div>
      <p className="ad-confighelp">
        A format change applies to Machines created afterwards only — existing
        Asset Tags are never renamed or regenerated.
      </p>
      <ServerErrorNote message={serverError} />
      {savedNote ? (
        <div className="ad-savednote" role="status">
          ✓ Format saved.
        </div>
      ) : null}
      <div className="row">
        <button
          className="bigbtn primary"
          disabled={writeBlocked || busy || !dirty || prefixError !== null}
          onClick={() => void submit()}
        >
          Save format
        </button>
      </div>
    </div>
  );
}
