import type { RequestType } from '../views/view-models';

/** Stable Area identity color dot (same color in both themes). */
export function AreaDot({
  colorVar,
  size = 10,
}: {
  colorVar: string;
  size?: number;
}) {
  return (
    <span
      className="areadot"
      style={{ background: colorVar, width: size, height: size }}
      aria-hidden="true"
    />
  );
}

/**
 * Standard Hot Part presentation: `🔥#n PN` — the flame and rank appear
 * immediately before the PN, with no separate rank chip after it. Every
 * view renders Hot PNs through this component so the visual order stays
 * identical everywhere.
 */
export function HotPn({
  rank,
  pn,
  pnClassName,
}: {
  rank?: number;
  pn: string;
  pnClassName?: string;
}) {
  const tier = rank !== undefined && rank <= 3 ? rank : 3;
  return (
    <>
      {rank !== undefined ? (
        <span className={`hot h${tier}`}>🔥#{rank}</span>
      ) : null}
      {rank !== undefined ? ' ' : null}
      <span className={pnClassName} title={pn}>
        {pn}
      </span>
    </>
  );
}

/** Request Type chip: NEW / REWORK / MODIFY (canonical vocabulary). */
export function TypeChip({ type }: { type: RequestType }) {
  return <span className={`typechip ${type.toLowerCase()}`}>{type}</span>;
}
