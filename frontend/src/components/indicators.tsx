import type { RequestType, RouteMode } from '../views/view-models';

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

/** Request Type chip: NEW / MODIFY (canonical vocabulary). */
export function TypeChip({ type }: { type: RequestType }) {
  return <span className={`typechip ${type.toLowerCase()}`}>{type}</span>;
}

/**
 * Route Mode chip — the ONE presentation of a Quantity Flow's route
 * mode everywhere it appears (Tracking flows, Scan Station receive
 * recap/confirmation, …). Mode and route information live in the same
 * chip, separated by an em dash: `FLOATING — actual trace`,
 * `PLANNED — <route name>` (Tracking's flow history uses
 * `PLANNED — snapshot`). The color follows the mode (styles/global.css)
 * and the mode word stays in the text — color is never the only
 * distinction.
 */
export function RouteModeChip({
  mode,
  detail,
}: {
  mode: RouteMode;
  detail: string;
}) {
  return (
    <span className={`routechip ${mode.toLowerCase()}`}>
      {mode} — {detail}
    </span>
  );
}
