import { SectionHeader, SettingsCard } from './section-widgets';
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
      <div className="admin-cards admin-cards-stack">
        <SettingsCard
          title="Scan settings"
          description="How Scan Stations read and accept scans. These settings are not available yet: each control shows what the Scan Stations do today, and none of them can be changed here."
        >
          <ScanBehaviorPreviews />
        </SettingsCard>
      </div>
    </>
  );
}
