import { useCallback, useEffect, useRef, useState } from "react";
import type { AuthState } from "./useAuth";
import {
  checkWeixinLoginStatus,
  getChannelStatus,
  startWeixinChannel,
  startWeixinLogin,
  stopWeixinChannel,
} from "../api";
import type { ChannelStatus, WeixinLoginResponse } from "../api";

type WeChatChannelState = {
  channel: ChannelStatus | null;
  error: string;
  qrData: WeixinLoginResponse | null;
  isGeneratingQr: boolean;
  isPolling: boolean;
};

export function useWeChatChannel(authState: AuthState | null, ensureThread: () => Promise<string>) {
  const [state, setState] = useState<WeChatChannelState>({
    channel: null,
    error: "",
    qrData: null,
    isGeneratingQr: false,
    isPolling: false,
  });

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatus = useCallback(async (sessionToken: string) => {
    try {
      const channels = await getChannelStatus(sessionToken);
      const wx = channels.find((c) => c.name === "weixin") ?? null;
      setState((prev) => ({ ...prev, channel: wx, error: "" }));
    } catch (e) {
      setState((prev) => ({ ...prev, error: e instanceof Error ? e.message : "状态查询失败" }));
    }
  }, []);

  useEffect(() => {
    if (authState) {
      void refreshStatus(authState.sessionToken);
    }
  }, [authState, refreshStatus]);

  const clearPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setState((prev) => ({ ...prev, isPolling: false }));
  }, []);

  const handleStartQrLogin = useCallback(async () => {
    if (!authState) return;
    setState((prev) => ({ ...prev, isGeneratingQr: true, error: "" }));
    try {
      const data = await startWeixinLogin(authState.sessionToken);
      setState((prev) => ({ ...prev, qrData: data, isGeneratingQr: false, isPolling: true }));
      clearPolling();
      pollRef.current = setInterval(async () => {
        try {
          const threadId = await ensureThread();
          const result = await checkWeixinLoginStatus(authState.sessionToken, threadId, authState.sessionId);
          if (result.status === "success") {
            clearPolling();
            setState((prev) => ({ ...prev, qrData: null, isPolling: false }));
            void refreshStatus(authState.sessionToken);
          } else if (result.status === "expired" || result.status === "no_qr") {
            clearPolling();
            setState((prev) => ({ ...prev, qrData: null, isPolling: false, error: result.message || "QR 码已过期" }));
          }
        } catch {
          // poll silently
        }
      }, 3000);
    } catch (e) {
      setState((prev) => ({
        ...prev,
        isGeneratingQr: false,
        error: e instanceof Error ? e.message : "生成二维码失败",
      }));
    }
  }, [authState, clearPolling, ensureThread, refreshStatus]);

  const handleCancelQrLogin = useCallback(() => {
    clearPolling();
    setState((prev) => ({ ...prev, qrData: null }));
  }, [clearPolling]);

  const handleStartChannel = useCallback(async () => {
    if (!authState) return;
    try {
      await startWeixinChannel(authState.sessionToken);
      void refreshStatus(authState.sessionToken);
    } catch (e) {
      setState((prev) => ({ ...prev, error: e instanceof Error ? e.message : "启动失败" }));
    }
  }, [authState, refreshStatus]);

  const handleStopChannel = useCallback(async () => {
    if (!authState) return;
    try {
      await stopWeixinChannel(authState.sessionToken);
      void refreshStatus(authState.sessionToken);
    } catch (e) {
      setState((prev) => ({ ...prev, error: e instanceof Error ? e.message : "停止失败" }));
    }
  }, [authState, refreshStatus]);

  return {
    ...state,
    refreshStatus,
    handleStartQrLogin,
    handleCancelQrLogin,
    handleStartChannel,
    handleStopChannel,
  };
}
