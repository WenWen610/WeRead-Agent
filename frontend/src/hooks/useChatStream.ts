import { useCallback, useState } from "react";
import { listThreadItems, streamChat } from "../api";
import type {
  ChatTimelineItem,
  ClarificationPayload,
  StreamEvent,
  WeReadArtifact,
} from "../types";
import type { AuthState } from "./useAuth";

export function useChatStream(
  authState: AuthState | null,
  activeThreadId: string | null,
  ensureThread: () => Promise<string>,
  onTimelineRefresh: () => void,
  onThreadsRefresh: () => void,
  onArtifactsRefresh: () => void,
  onBindingRefresh: () => void,
) {
  const [composerValue, setComposerValue] = useState("");
  const [streamingAssistant, setStreamingAssistant] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [clarification, setClarification] = useState<ClarificationPayload | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toolCallActive, setToolCallActive] = useState(false);
  const [streamError, setStreamError] = useState("");
  const [timelineItems, setTimelineItems] = useState<ChatTimelineItem[]>([]);
  const [artifactsByThread, setArtifactsByThread] = useState<Record<string, WeReadArtifact[]>>({});

  const mergeArtifacts = useCallback(
    (existing: WeReadArtifact[], incoming: WeReadArtifact[]): WeReadArtifact[] => {
      const merged = [...existing, ...incoming];
      const deduped: WeReadArtifact[] = [];
      const seen = new Set<string>();
      for (let i = merged.length - 1; i >= 0; i--) {
        const a = merged[i];
        if (seen.has(a.artifact_id)) continue;
        seen.add(a.artifact_id);
        deduped.push(a);
      }
      return deduped.reverse();
    },
    [],
  );

  const handleSendMessage = useCallback(
    async (nextMessage?: string) => {
      if (!authState || isSubmitting) return;
      const message = (nextMessage ?? composerValue).trim();
      if (!message) return;

      setIsSubmitting(true);
      setStatusMessage("");
      setClarification(null);
      setStreamingAssistant("");
      setToolCallActive(false);
      setStreamError("");

      try {
        const threadId = await ensureThread();
        setTimelineItems((prev) => [
          ...prev,
          {
            id: Date.now(),
            thread_id: threadId,
            seq: prev.length + 1,
            item_type: "user",
            content: message,
            metadata: {},
            created_at: new Date().toISOString(),
          },
        ]);
        setComposerValue("");

        await streamChat(authState.sessionToken, threadId, message, (event: StreamEvent) => {
          switch (event.type) {
            case "status":
              setStatusMessage(event.data.message ?? "");
              break;
            case "chunk":
              setToolCallActive(false);
              setStreamingAssistant((prev) => prev + (event.data.content ?? ""));
              break;
            case "clarification":
              setClarification(event.data);
              break;
            case "capability_required":
              if (event.data.provider === "weread") onBindingRefresh();
              break;
            case "artifact": {
              const next = Array.isArray(event.data.artifacts) ? event.data.artifacts : [];
              if (next.length > 0) {
                setArtifactsByThread((prev) => ({
                  ...prev,
                  [threadId]: mergeArtifacts(prev[threadId] ?? [], next),
                }));
              }
              break;
            }
            case "tool_call":
              setToolCallActive(true);
              break;
            case "error":
              setStreamError(event.data.message);
              break;
            case "done":
              setStatusMessage("");
              setToolCallActive(false);
              break;
          }
        });

        onThreadsRefresh();
        const items = await listThreadItems(authState.sessionToken, threadId);
        setTimelineItems(items);
        const lastClarification = [...items]
          .reverse()
          .find((item) => item.item_type === "clarification");
        if (lastClarification) {
          const meta = lastClarification.metadata ?? {};
          setClarification({
            question: String(meta.question ?? lastClarification.content),
            options: Array.isArray(meta.options)
              ? meta.options.map((v) => String(v))
              : [],
            clarification_type:
              typeof meta.clarification_type === "string" ? meta.clarification_type : undefined,
          });
        } else {
          setClarification(null);
        }
        onArtifactsRefresh();
      } catch (error) {
        setStatusMessage(error instanceof Error ? error.message : "发送失败");
      } finally {
        setStreamingAssistant("");
        setIsSubmitting(false);
        setToolCallActive(false);
      }
    },
    [
      authState,
      isSubmitting,
      composerValue,
      ensureThread,
      mergeArtifacts,
      onBindingRefresh,
      onThreadsRefresh,
      onArtifactsRefresh,
    ],
  );

  return {
    composerValue,
    streamingAssistant,
    statusMessage,
    clarification,
    isSubmitting,
    toolCallActive,
    streamError,
    timelineItems,
    artifactsByThread,
    setComposerValue,
    setClarification,
    setTimelineItems,
    setStreamingAssistant,
    setStatusMessage,
    setArtifactsByThread,
    handleSendMessage,
  };
}
