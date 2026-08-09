"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthAPI, TOKEN_KEY, USER_KEY } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  ready: boolean; // false until localStorage has been read on the client
  login: (identifier: string, password: string) => Promise<void>;
  register: (b: { email: string; username: string; password: string; full_name?: string }) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();

  // Read persisted session on the client only — avoids hydration mismatch.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(USER_KEY);
      if (raw && window.localStorage.getItem(TOKEN_KEY)) setUser(JSON.parse(raw));
    } catch {
      window.localStorage.removeItem(USER_KEY);
    }
    setReady(true);
  }, []);

  const persist = useCallback((token: string, u: User) => {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(u));
    setUser(u);
  }, []);

  const login = useCallback(
    async (identifier: string, password: string) => {
      const r = await AuthAPI.login(identifier, password);
      persist(r.access_token, r.user);
    },
    [persist],
  );

  const register = useCallback(
    async (b: { email: string; username: string; password: string; full_name?: string }) => {
      const r = await AuthAPI.register(b);
      persist(r.access_token, r.user);
    },
    [persist],
  );

  const logout = useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    setUser(null);
    router.push("/login");
  }, [router]);

  const value = useMemo(() => ({ user, ready, login, register, logout }), [user, ready, login, register, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
