import { areaColor } from '../../api/environment';
import type { Area } from '../../api/environment';
import type { Machine } from '../../api/machines';
import { Code128Svg } from '../../components/Code128Svg';
import { AreaDot } from '../../components/indicators';
import { PrintBarcodesDialog } from '../../components/PrintBarcodesDialog';
import type { BarcodeSection } from '../../components/print-barcodes';
import { machineBarcode } from '../asset-tags';
import { MachineBarcodeLabel } from './MachineBarcodeLabel';

/**
 * Print barcodes on Management → Machines (GUI_DESIGN §12.6): the
 * ACTIVE Machines (retired ones never receive work and are not listed;
 * Machines under maintenance are), grouped by Area in the Area order
 * the app uses everywhere, each Machine in name order. Each card shows
 * the Machine label in the theme colors; the printed label is the
 * single `Machine barcode label`.
 */
export function PrintMachineBarcodesDialog({
  machines,
  areas,
  onClose,
}: {
  /** Every Machine; the dialog lists the active ones. */
  machines: readonly Machine[];
  /** The Areas in the app's order. */
  areas: readonly Area[];
  onClose: () => void;
}) {
  const active = machines
    .filter((machine) => machine.retiredOn === undefined)
    .sort(
      (a, b) =>
        a.name.localeCompare(b.name) || a.assetTag.localeCompare(b.assetTag),
    );
  const byKey = new Map(active.map((machine) => [String(machine.id), machine]));
  const known = new Set(areas.map((area) => area.id));
  const sections: BarcodeSection[] = areas.map((area) => ({
    key: `area-${area.id}`,
    title: (
      <>
        <AreaDot colorVar={areaColor(area)} size={11} />
        {area.name}
      </>
    ),
    titleText: area.name,
    itemKeys: active
      .filter((machine) => machine.areaId === area.id)
      .map((machine) => String(machine.id)),
  }));
  const unplaced = active.filter((machine) => !known.has(machine.areaId));
  if (unplaced.length > 0) {
    sections.push({
      key: 'area-none',
      title: (
        <>
          <AreaDot colorVar={areaColor(undefined)} size={11} />—
        </>
      ),
      titleText: 'No Area',
      itemKeys: unplaced.map((machine) => String(machine.id)),
    });
  }

  return (
    <PrintBarcodesDialog
      title="Print Machine barcodes"
      description="Select the Machine labels to print. Labels fill the page left to right, then row by row."
      sections={sections}
      emptyMessage="No active Machines to print."
      cardName={(key) => {
        const machine = byKey.get(key);
        return machine ? `${machine.name} · ${machine.assetTag}` : key;
      }}
      renderCard={(key) => {
        const machine = byKey.get(key);
        if (!machine) return null;
        const value = machineBarcode(machine.assetTag);
        return (
          <>
            <div className="pbx-ltitle">{machine.name}</div>
            <div className="pbx-lmeta">{machine.assetTag}</div>
            <Code128Svg className="pbx-lbars" value={value} />
            <div className="pbx-lvalue">{value}</div>
          </>
        );
      }}
      renderLabel={(key) => {
        const machine = byKey.get(key);
        return machine ? <MachineBarcodeLabel machine={machine} /> : null;
      }}
      onClose={onClose}
    />
  );
}
