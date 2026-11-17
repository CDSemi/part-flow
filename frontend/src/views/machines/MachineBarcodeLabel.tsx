import './machines.css';

import type { Machine } from '../../api/machines';
import { Code128Svg } from '../../components/Code128Svg';
import { machineBarcode } from '../asset-tags';

/**
 * The ONE printable Machine barcode label: the display name, the Asset
 * Tag and the Code 128 barcode of the scanned value
 * (`PF:MACHINE:<asset-tag>`), always white with black ink. The
 * `Machine barcode label` dialog and Print barcodes (Management →
 * Machines) both render THIS label, so a label printed either way is
 * identical.
 */
export function MachineBarcodeLabel({
  machine,
  className,
}: {
  machine: Pick<Machine, 'name' | 'assetTag'>;
  /** Extra class (the single-label print hook `mg-labelprint`). */
  className?: string;
}) {
  const value = machineBarcode(machine.assetTag);
  return (
    <div className={`mg-label${className ? ` ${className}` : ''}`}>
      <div className="lname">{machine.name}</div>
      <div className="ltag">{machine.assetTag}</div>
      <Code128Svg className="lbarcode" value={value} />
      <div className="lvalue">{value}</div>
    </div>
  );
}
