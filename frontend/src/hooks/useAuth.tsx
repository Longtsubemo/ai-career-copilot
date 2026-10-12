import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  register as registerRequest,
} from "../api/auth";
import { UNAUTHORIZED_EVENT } from "../api/client";
import { ApiError, toApiError } from "../api/errors";
import type { LoginRequest, RegisterRequest, User } from "../types/api";
import type { AuthStatus } from "../utils/authRouting";

interface AuthContextValue {
  user: User | null;
  status: AuthStatus;
  error: ApiError | null;
  login: (payload: LoginRequest) => Promise<User>;
  register: (payload: RegisterRequest) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [error, setError] = useState<ApiError | null>(null);

  const restore = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const current = await getCurrentUser();
      setUser(current);
      setStatus("authenticated");
    } catch (err) {
      const apiError = toApiError(err);
      setUser(null);
      if (apiError.status === 401) {
        setStatus("unauthenticated");
      } else {
        setError(apiError);
        setStatus("unauthenticated");
      }
    }
  }, []);

  useEffect(() => {
    void restore();
  }, [restore]);

  useEffect(() => {
    function handleUnauthorized() {
      setUser(null);
      setStatus((current) => (current === "loading" ? current : "unauthenticated"));
    }
    window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
  }, []);

  const login = useCallback(async (payload: LoginRequest) => {
    const current = await loginRequest(payload);
    setUser(current);
    setError(null);
    setStatus("authenticated");
    return current;
  }, []);

  const register = useCallback(async (payload: RegisterRequest) => {
    const current = await registerRequest(payload);
    setUser(current);
    setError(null);
    setStatus("authenticated");
    return current;
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Even a failed revoke clears local state; the cookie stays HttpOnly.
    } finally {
      setUser(null);
      setError(null);
      setStatus("unauthenticated");
    }
  }, []);

  const value = useMemo(
    () => ({ user, status, error, login, register, logout, refresh: restore }),
    [user, status, error, login, register, logout, restore],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}
