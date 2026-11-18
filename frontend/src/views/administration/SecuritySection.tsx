import { useSession } from '../../app/session-context';
import { PasswordRulesPanel } from './PasswordRulesPanel';
import { RememberMePanel } from './RememberMePanel';
import { SectionHeader, ViewOnlyNote } from './section-widgets';
import { ADMIN_SECTIONS } from './sections';
import { SignInSettingsPanel } from './SignInSettingsPanel';
import { TwoStepSettingsPanel } from './TwoStepSettingsPanel';

// Administration → Security (owner request of 2026-11-17): every setting
// of how users sign in, gathered in one section — User sign-in (how long a
// sign-in lasts and when failed sign-ins lock an account), Remember me,
// the Password rules and Two-step sign-in (authenticator app codes,
// passkeys, emailed codes and recovery codes), each its own card that
// loads its own data. The outgoing mail server the emailed codes go
// through stays in Settings, as general infrastructure PartFlow also uses
// for other messages. Changing anything here needs the Configure system
// settings permission (the server checks it); every signed-in user reads
// it, with one view-only line under the header when the user may not
// change it.

const SUBTITLE =
  ADMIN_SECTIONS.find((section) => section.id === 'security')?.subtitle ?? '';

export function SecuritySection() {
  const canWrite = useSession().can('CONFIGURE_SYSTEM_SETTINGS');
  return (
    <>
      <SectionHeader title="Security" subtitle={SUBTITLE} />
      {canWrite ? null : (
        <ViewOnlyNote permission="CONFIGURE_SYSTEM_SETTINGS" />
      )}
      <div className="admin-cards">
        <SignInSettingsPanel />
        <RememberMePanel />
        <PasswordRulesPanel />
        <TwoStepSettingsPanel />
      </div>
    </>
  );
}
