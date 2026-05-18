import { useCallback, useEffect, useMemo, useState } from "react";
import { createThread, deleteThread, listThreads, renameThread } from "../api";
import type { ChatThreadSummary } from "../types";
import type { AuthState } from "./useAuth";

export function useThreads(authState: AuthState | null) {
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);

  const activeThread = useMemo(
    () => threads.find((t) => t.thread_id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );

  const refreshThreads = useCallback(async (sessionToken: string) => {
    setIsLoadingThreads(true);
    try {
      const next = await listThreads(sessionToken);
      setThreads(next);
      if (next.length > 0) {
        setActiveThreadId((prev) => {
          if (prev && next.some((t) => t.thread_id === prev)) return prev;
          return next[0].thread_id;
        });
      }
    } finally {
      setIsLoadingThreads(false);
    }
  }, []);

  useEffect(() => {
    if (!authState) return;
    void refreshThreads(authState.sessionToken);
  }, [authState, refreshThreads]);

  useEffect(() => {
    if (!authState) return;
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void refreshThreads(authState.sessionToken);
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [authState, refreshThreads]);

  const handleCreateThread = useCallback(async () => {
    if (!authState) return;
    const thread = await createThread(authState.sessionToken, "");
    setThreads((prev) => [thread, ...prev]);
    setActiveThreadId(thread.thread_id);
  }, [authState]);

  const handleDeleteThread = useCallback(
    async (threadId: string) => {
      if (!authState) return;
      await deleteThread(authState.sessionToken, threadId);
      const next = threads.filter((t) => t.thread_id !== threadId);
      setThreads(next);
      if (activeThreadId === threadId) {
        setActiveThreadId(next[0]?.thread_id ?? null);
      }
    },
    [authState, threads, activeThreadId],
  );

  const handleRenameThread = useCallback(
    async (threadId: string) => {
      if (!authState) return;
      const current = threads.find((t) => t.thread_id === threadId);
      const nextTitle = window.prompt("请输入新的会话标题", current?.title ?? "");
      if (!nextTitle?.trim()) return;
      const updated = await renameThread(authState.sessionToken, threadId, nextTitle.trim());
      setThreads((prev) => prev.map((t) => (t.thread_id === threadId ? updated : t)));
    },
    [authState, threads],
  );

  return {
    threads,
    activeThreadId,
    setActiveThreadId,
    activeThread,
    isLoadingThreads,
    refreshThreads,
    handleCreateThread,
    handleDeleteThread,
    handleRenameThread,
    setThreads,
  };
}
