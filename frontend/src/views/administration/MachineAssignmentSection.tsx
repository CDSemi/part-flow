import { SectionHeader } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';

// Administration → Machine assignment (Phase 13; GUI_DESIGN §9
// Policies, PROJECT_PROFILE §8.4 / §12): a read-only policy statement
// of the two Area ownership modes, never a Machine registry and never a
// per-Area setting. An Area's mode follows only from whether it has
// Machines, so there is nothing to read or write here — the Areas
// section shows each Area's current mode, and Machines are managed in
// Management → Machines.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'machine-assignment')
    ?.subtitle ?? '';

const MODES: { areaHas: string; mode: string; atStation: string }[] = [
  {
    areaHas: 'No Machines',
    mode: 'Direct processing (no Machines)',
    atStation:
      'Quantity scanned into the Area is processed there directly; no Machine is recorded.',
  },
  {
    areaHas: 'One or more Machines',
    mode: 'Queue → assign (one-shot)',
    atStation:
      'Quantity enters the Area queue and is assigned to a Machine through an explicit one-shot assignment — never automatically, even when the Area has a single Machine.',
  },
];

export function MachineAssignmentSection() {
  return (
    <>
      <SectionHeader title="Machine assignment" subtitle={SUBTITLE} />
      <div className="ad-config">
        <h2>Machine assignment follows from the Area&apos;s Machines</h2>
        <p className="ad-confighelp">
          Machine assignment is not configured per Area. Each Area works in one
          of two modes, decided only by whether it has Machines.
        </p>
        <table className="ad-table">
          <thead>
            <tr>
              <th>Area has</th>
              <th>Mode</th>
              <th>At the Scan Station</th>
            </tr>
          </thead>
          <tbody>
            {MODES.map((row) => (
              <tr key={row.areaHas}>
                <td data-label="Area has">
                  <b>{row.areaHas}</b>
                </td>
                <td className="modecell" data-label="Mode">
                  {row.mode}
                </td>
                <td data-label="At the Scan Station">{row.atStation}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="ad-confighelp">
          Machines are managed in Management → Machines; an Area&apos;s mode
          changes only when its Machines change. The Areas section shows each
          Area&apos;s current mode.
        </p>
      </div>
    </>
  );
}
