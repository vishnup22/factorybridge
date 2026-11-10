import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { request, setUnauthorizedHandler, tokenStore } from "../api/client";
import type { LoginResponse, Me, Role } from "../api/types";

interface AuthState {
  me: Me | null;
  role: Role | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(() => tokenStore.get() !== null);

  const logout = useCallback(() => {
    tokenStore.clear();
    setMe(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe(null);
      qc.clear();
    });
    return () => setUnauthorizedHandler(null);
  }, [qc]);

  useEffect(() => {
    if (!tokenStore.get()) return;
    request<Me>("/auth/me")
      .then(setMe)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await request<LoginResponse>("/auth/login", { body: { email, password } });
    tokenStore.set(r.access_token);
    setMe(await request<Me>("/auth/me"));
  }, []);

  const value = useMemo<AuthState>(
    () => ({ me, role: (me?.user.role as Role | undefined) ?? null, loading, login, logout }),
    [me, loading, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function can(role: Role | null, ...allowed: Role[]): boolean {
  return role !== null && allowed.includes(role);
}
