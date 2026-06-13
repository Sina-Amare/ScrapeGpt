import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState
} from "react";
import { api, setAccessToken, setAuthFailureHandler } from "./api";
import { markAuthChanged } from "./session";
import {
  clearStoredRefreshToken,
  clearStoredUserEmail,
  getStoredRefreshToken,
  getStoredUserEmail,
  setStoredRefreshToken,
  setStoredUserEmail
} from "./storage";

type AuthContextValue = {
  booting: boolean;
  authenticated: boolean;
  displayEmail: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [booting, setBooting] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [displayEmail, setDisplayEmail] = useState<string | null>(
    getStoredUserEmail()
  );

  const logout = useCallback(() => {
    setAccessToken(null);
    clearStoredRefreshToken();
    clearStoredUserEmail();
    setDisplayEmail(null);
    setAuthenticated(false);
    // Mark the session boundary so any in-flight mutation that resolves after
    // this point is suppressed, and drop cached data so the previous user's
    // records (e.g. provider names) can't leak to the next session.
    markAuthChanged();
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setAuthFailureHandler(logout);
    return () => setAuthFailureHandler(null);
  }, [logout]);

  useEffect(() => {
    let active = true;
    const refreshToken = getStoredRefreshToken();
    if (!refreshToken) {
      setBooting(false);
      return;
    }

    api
      .refreshAccessToken()
      .then(() => {
        if (!active) return;
        setAuthenticated(true);
        setDisplayEmail(getStoredUserEmail());
      })
      .catch(() => {
        if (!active) return;
        logout();
      })
      .finally(() => {
        if (active) setBooting(false);
      });

    return () => {
      active = false;
    };
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api.login(email, password);
    setAccessToken(tokens.access_token);
    setStoredRefreshToken(tokens.refresh_token);
    setStoredUserEmail(email);
    setDisplayEmail(email);
    markAuthChanged();
    setAuthenticated(true);
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const response = await api.register(email, password);
    setAccessToken(response.tokens.access_token);
    setStoredRefreshToken(response.tokens.refresh_token);
    setStoredUserEmail(response.user.email);
    setDisplayEmail(response.user.email);
    markAuthChanged();
    setAuthenticated(true);
  }, []);

  const value = useMemo(
    () => ({
      booting,
      authenticated,
      displayEmail,
      login,
      register,
      logout
    }),
    [authenticated, booting, displayEmail, login, logout, register]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
