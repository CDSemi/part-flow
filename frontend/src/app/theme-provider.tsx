import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { ThemeContext } from './theme-context';
import type { Theme } from './theme-context';

// Dark is the default: PartFlow is shop-floor first (GUI_DESIGN §2.1).
// The decided persistence model (GUI_DESIGN §2.1, post-v18) is per User
// AND per Scan Station, resolved authenticated User preference → Scan
// Station preference → Dark default; Worker Sessions never affect the
// theme. Users and Scan Station configuration do not exist in Phase 2,
// so the mock keeps the choice session-only until they do
// (IMPLEMENTATION_ROADMAP Phase 3.5 / Phase 13).
const DEFAULT_THEME: Theme = 'dark';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME);

  // The theme class lives on <body> so every surface — navigation,
  // dialogs, banners and view content — follows the selected mode.
  useEffect(() => {
    document.body.classList.remove('dark', 'light');
    document.body.classList.add(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
