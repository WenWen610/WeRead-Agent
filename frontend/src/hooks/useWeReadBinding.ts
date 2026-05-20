import { useCallback, useEffect, useState } from "react";
import {
  cancelWeReadQrLoginSession,
  clearWeReadBinding,
  getWeReadBinding,
  getWeReadQrLoginSession,
  startWeReadQrLoginSession,
  validateWeReadBinding,
} from "../api";
import type { WeReadBinding, WeReadQrLoginSession } from "../types";
import type { AuthState } from "./useAuth";

export function isQrInProgress(session: WeReadQrLoginSession | null): boolean {
  return Boolean(session && (session.status === "pending" || session.status === "qr_ready"));
}

export function formatStatusLabel(
  binding: WeReadBinding | null,
  qrLoginSession: WeReadQrLoginSession | null,
): string {
  if (isQrInProgress(qrLoginSession)) return "等待扫码";
  if (binding?.status === "expired") return "已失效";
  if (binding?.status === "reauth_required") return "需重新验证";
  if (binding?.connected && binding.status === "active") return "已连接";
  if (binding?.connected) return "凭证已保存";
  return "未连接";
}

export function formatStatusTone(
  binding: WeReadBinding | null,
  qrLoginSession: WeReadQrLoginSession | null,
): "active" | "warning" | "idle" {
  if (isQrInProgress(qrLoginSession)) return "warning";
  if (binding?.status === "expired" || binding?.status === "reauth_required") return "warning";
  if (binding?.connected && binding.status === "active") return "active";
  return "idle";
}

export function useWeReadBinding(authState: AuthState | null) {
  const [wereadBinding, setWeReadBinding] = useState<WeReadBinding | null>(null);
  const [bindingError, setBindingError] = useState("");
  const [qrLoginSession, setQrLoginSession] = useState<WeReadQrLoginSession | null>(null);
  const [isStartingQrLogin, setIsStartingQrLogin] = useState(false);
  const [isValidatingBinding, setIsValidatingBinding] = useState(false);

  const refreshBinding = useCallback(async (sessionToken: string, silent = false) => {
    try {
      const binding = await getWeReadBinding(sessionToken);
      setWeReadBinding(binding);
      if (!silent) setBindingError("");
    } catch (error) {
      if (!silent) setBindingError(error instanceof Error ? error.message : "微信读书绑定状态加载失败");
    }
  }, []);

  useEffect(() => {
    if (!authState) {
      setWeReadBinding(null);
      setBindingError("");
      setQrLoginSession(null);
      return;
    }
    void refreshBinding(authState.sessionToken);
  }, [authState, refreshBinding]);

  useEffect(() => {
    if (!authState) return;
    const token = authState.sessionToken;
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshBinding(token, true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [authState, refreshBinding]);

  useEffect(() => {
    if (!authState || !qrLoginSession) return;
    if (["success", "expired", "failed", "cancelled"].includes(qrLoginSession.status)) return;

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          const next = await getWeReadQrLoginSession(authState.sessionToken, qrLoginSession.session_id);
          if (cancelled) return;
          setQrLoginSession(next);
          if (next.status === "success") {
            await refreshBinding(authState.sessionToken);
            if (!cancelled) {
              setQrLoginSession(null);
              setBindingError("");
            }
          } else if (next.status === "expired" || next.status === "failed") {
            setBindingError(next.last_error ?? "微信读书二维码登录未完成");
          }
        } catch (error) {
          if (!cancelled) setBindingError(error instanceof Error ? error.message : "二维码登录状态刷新失败");
        }
      })();
    }, 2000);

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [authState, qrLoginSession, refreshBinding]);

  const startPollingFromSession = useCallback((session_id: string) => {
    const now = new Date().toISOString();
    setQrLoginSession({
      session_id,
      status: "qr_ready",
      qr_image_base64: null,
      last_error: null,
      expires_at: new Date(Date.now() + 180000).toISOString(),
      created_at: now,
      updated_at: now,
      completed_at: null,
    });
  }, []);

  const handleStartQrLogin = useCallback(async () => {
    if (!authState || isStartingQrLogin) return;
    setIsStartingQrLogin(true);
    setBindingError("");
    try {
      const next = await startWeReadQrLoginSession(authState.sessionToken);
      setQrLoginSession(next);
    } catch (error) {
      setBindingError(error instanceof Error ? error.message : "微信读书二维码登录启动失败");
    } finally {
      setIsStartingQrLogin(false);
    }
  }, [authState, isStartingQrLogin]);

  const handleCancelQrLogin = useCallback(async () => {
    if (!authState || !qrLoginSession) return;
    try {
      await cancelWeReadQrLoginSession(authState.sessionToken, qrLoginSession.session_id);
      setQrLoginSession(null);
    } catch (error) {
      setBindingError(error instanceof Error ? error.message : "微信读书二维码登录取消失败");
    }
  }, [authState, qrLoginSession]);

  const handleClearBinding = useCallback(async () => {
    if (!authState) return;
    try {
      await clearWeReadBinding(authState.sessionToken);
      setQrLoginSession(null);
      await refreshBinding(authState.sessionToken);
    } catch (error) {
      setBindingError(error instanceof Error ? error.message : "微信读书绑定清除失败");
    }
  }, [authState, refreshBinding]);

  const handleValidateBinding = useCallback(async () => {
    if (!authState || isValidatingBinding) return;
    setIsValidatingBinding(true);
    setBindingError("");
    try {
      const binding = await validateWeReadBinding(authState.sessionToken);
      setWeReadBinding(binding);
    } catch (error) {
      setBindingError(error instanceof Error ? error.message : "检查连接失败");
    } finally {
      setIsValidatingBinding(false);
    }
  }, [authState, isValidatingBinding]);

  return {
    wereadBinding,
    bindingError,
    qrLoginSession,
    isStartingQrLogin,
    isValidatingBinding,
    setWeReadBinding,
    setBindingError,
    setQrLoginSession,
    refreshBinding,
    handleStartQrLogin,
    handleCancelQrLogin,
    handleClearBinding,
    handleValidateBinding,
    startPollingFromSession,
  };
}
