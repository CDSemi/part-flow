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

/** Hot PO Demand rank chip (🔥 #n). */
export function HotChip({
  rank,
  showFlame = true,
}: {
  rank: number;
  showFlame?: boolean;
}) {
  const tier = rank <= 3 ? rank : 3;
  return (
    <span className={`hot h${tier}`}>
      {showFlame ? '🔥 ' : ''}#{rank}
    </span>
  );
}

/** Request Type chip: NEW / REWORK / MODIFY (canonical vocabulary). */
export function TypeChip({ type }: { type: RequestType }) {
  return <span className={`typechip ${type.toLowerCase()}`}>{type}</span>;
}
