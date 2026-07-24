import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { ThemeContext } from './theme-context';
import type { Theme } from './theme-context';

// Dark is the default: PartFlow is shop-floor first (GUI_DESIGN §2.1).
// The choice is session-only — persistence (per user / per station) is an
// open decision (GUI_DESIGN §14) and is intentionally not invented here.
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
