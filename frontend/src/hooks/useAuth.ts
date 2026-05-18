import { useCallback, useEffect, useState } from "react";
import { createAuthSession, login, register } from "../api";

export type AuthState = {
  sessionToken: string;
  sessionId: string;
};

const AUTH_STORAGE_KEY = "weread-agent-auth";

function readStoredAuthState(): AuthState | null {
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthState) : null;
  } catch {
    return null;
  }
}

function persistAuthState(value: AuthState | null): void {
  if (value === null) {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(value));
}

export function useAuth() {
  const [authState, setAuthState] = useState<AuthState | null>(() => readStoredAuthState());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    persistAuthState(authState);
  }, [authState]);

  const handleAuthenticate = useCallback(async () => {
    setAuthError("");
    try {
      const userToken =
        authMode === "login"
          ? await login(email.trim(), password)
          : await register(email.trim(), password);
      const session = await createAuthSession(userToken);
      setAuthState({
        sessionToken: session.token.access_token,
        sessionId: session.session_id,
      });
      setEmail("");
      setPassword("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "认证失败");
    }
  }, [authMode, email, password]);

  const handleLogout = useCallback(() => {
    setAuthState(null);
    persistAuthState(null);
  }, []);

  return {
    authState,
    email,
    password,
    authMode,
    authError,
    setEmail,
    setPassword,
    setAuthMode,
    handleAuthenticate,
    handleLogout,
  };
}
