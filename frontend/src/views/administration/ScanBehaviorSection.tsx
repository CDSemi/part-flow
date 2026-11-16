import { SectionHeader } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';
import { ScanBehaviorPreviews } from './SettingPreviews';

// Administration → Scan behavior (Phase 17; GUI_DESIGN §9): the
// candidate scan settings as disabled previews, each marked Not
// available yet and showing what the Scan Stations do today. Nothing is
// stored or changed here, so the section needs no permission note.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'scan-behavior')?.subtitle ??
  '';

export function ScanBehaviorSection() {
  return (
    <>
      <SectionHeader title="Scan behavior" subtitle={SUBTITLE} />
      <div className="admin-config">
        <p className="admin-confighelp">
          These settings are not available yet. Each control shows what the Scan
          Stations do today; none of them can be changed here.
        </p>
        <ScanBehaviorPreviews />
      </div>
    </>
  );
}
