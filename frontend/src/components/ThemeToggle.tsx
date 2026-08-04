import { useTheme } from '../app/theme-context';

/**
 * The global Dark/Light mode control — one shared component for the
 * top-navigation toggle and the compact Scan Station production-mode
 * variant (production mode hides the top navigation, so the control
 * moves into the station header's actions group). Both variants use
 * the same ThemeProvider state: toggling updates the entire
 * application instantly, and the choice survives switching between
 * standard and production routes (session state, GUI_DESIGN §2.1).
 */
export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      className={compact ? 'themetoggle compact' : 'navbtn'}
      onClick={toggleTheme}
      aria-pressed={theme === 'light'}
      title="Switch between Dark and Light mode — every view follows"
    >
      {theme === 'dark' ? '🌙 Dark' : '☀️ Light'}
    </button>
  );
}
