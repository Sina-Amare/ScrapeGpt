import { createContext, ReactNode, useContext, useEffect, useState } from "react";

const STORAGE_KEY = "scrapegpt-theme";

type ThemeContextValue = {
  dark: boolean;
  toggle: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readInitialTheme(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored === "dark";
    // Dark-first: the premium glass theme is the default for new sessions.
    return true;
  } catch {
    return true;
  }
}

/**
 * Single source of truth for the light/dark theme.
 *
 * Previously each call site used a local-state `useTheme()` hook, so the auth
 * page and the app shell held independent `dark` flags synced only through
 * localStorage + the `dark` DOM class. That desynced across the login -> app
 * remount. This provider holds the one shared state; every consumer toggles and
 * reads the same value. The initial `dark` class is applied pre-React by the
 * inline script in index.html (anti-FOUC); this effect keeps it in sync.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [dark, setDark] = useState<boolean>(readInitialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem(STORAGE_KEY, dark ? "dark" : "light");
    } catch {
      // ignore storage errors (private mode, quota, etc.)
    }
  }, [dark]);

  return (
    <ThemeContext.Provider value={{ dark, toggle: () => setDark((value) => !value) }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a <ThemeProvider>");
  }
  return ctx;
}
