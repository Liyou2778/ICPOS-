import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { clearSession, getUser, setSession } from '../api/client';
import { api } from '../api';

interface AuthUser {
  username: string;
  display_name: string;
  role: string;
}

interface AuthCtx {
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => void;
  ready: boolean;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => getUser());

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setSession(res.token, res.user);
    setUser(res.user);
    return res.user;
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
  }, []);

  const value = useMemo<AuthCtx>(() => ({ user, login, logout, ready: true }), [user, login, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return ctx;
}
