import { code128ModuleCount, encodeCode128B } from '../views/code128';

/**
 * The ONE Code 128 (subset B) barcode rendering: an inline SVG built
 * from the shared dependency-free encoder (`src/views/code128.ts`).
 * Every printable label (Machine asset labels, PN labels) renders its
 * bars through this component — the encoder and the bar geometry are
 * never duplicated per surface. Renders nothing for a value the
 * encoder rejects.
 *
 * Production-safe: no mock data.
 */
export function Code128Svg({
  value,
  className,
  barHeight = 64,
}: {
  /** The exact scanned value the barcode carries. */
  value: string;
  className?: string;
  barHeight?: number;
}) {
  const runs = encodeCode128B(value);
  if (!runs) return null;
  const quiet = 10;
  const moduleWidth = 2;
  const totalModules = code128ModuleCount(runs) + quiet * 2;
  let x = quiet;
  return (
    <svg
      className={className}
      viewBox={`0 0 ${totalModules * moduleWidth} ${barHeight}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`Barcode ${value}`}
    >
      {runs.map((run, index) => {
        const rect = run.bar ? (
          <rect
            key={index}
            x={x * moduleWidth}
            y={0}
            width={run.width * moduleWidth}
            height={barHeight}
          />
        ) : null;
        x += run.width;
        return rect;
      })}
    </svg>
  );
}
