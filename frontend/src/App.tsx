import { useCallback, useEffect, useMemo, useState } from "react";
import { createThread, getWeReadDocumentBlob, getWeReadDocumentPreview, listThreadItems, listThreadArtifacts } from "./api";
import type { WeReadArtifact } from "./types";
import { useAuth } from "./hooks/useAuth";
import { useThreads } from "./hooks/useThreads";
import { useWeReadBinding } from "./hooks/useWeReadBinding";
import { useWeChatChannel } from "./hooks/useWeChatChannel";
import { useChatStream } from "./hooks/useChatStream";
import { AuthPanel } from "./components/AuthPanel";
import { Sidebar } from "./components/Sidebar";
import { ThreadList } from "./components/ThreadList";
import { WeReadBindingCard } from "./components/WeReadBindingCard";
import { WeChatBindingCard } from "./components/WeChatBindingCard";
import { Composer } from "./components/Composer";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { ResourcesTree } from "./components/ResourcesTree";
import { ResourcesViewer } from "./components/ResourcesViewer";
import { useResources } from "./hooks/useResources";
import { TimelineItem, StreamingMessage, ClarificationFloating } from "./components/TimelineItem";
import { ToolCallIndicator } from "./components/ToolCallIndicator";

function App() {
  const auth = useAuth();
  const threads = useThreads(auth.authState);
  const weread = useWeReadBinding(auth.authState);

  const ensureThread = useCallback(async (): Promise<string> => {
    if (!auth.authState) throw new Error("未登录");
    if (threads.activeThreadId) return threads.activeThreadId;
    const thread = await createThread(auth.authState.sessionToken, "");
    threads.setThreads((prev) => [thread, ...prev]);
    threads.setActiveThreadId(thread.thread_id);
    return thread.thread_id;
  }, [auth.authState, threads]);

  const wechat = useWeChatChannel(auth.authState, ensureThread);
  const resources = useResources(auth.authState?.sessionToken ?? "");

  const [artifactPreview, setArtifactPreview] = useState("");
  const [artifactPreviewError, setArtifactPreviewError] = useState("");
  const [artifactListError, setArtifactListError] = useState("");
  const [isArtifactLoading, setIsArtifactLoading] = useState(false);
  const [selectedArtifact, setSelectedArtifact] = useState<WeReadArtifact | null>(null);
  const [activeTab, setActiveTab] = useState<"chat" | "resources">("chat");

  const onBindingRefresh = useCallback(() => {
    if (auth.authState) void weread.refreshBinding(auth.authState.sessionToken, true);
  }, [auth.authState, weread]);

  const {
    composerValue,
    streamingAssistant,
    statusMessage,
    clarification,
    isSubmitting,
    toolCallActive,
    streamError,
    timelineItems,
    setComposerValue,
    setTimelineItems,
    setStreamingAssistant,
    setStatusMessage,
    setArtifactsByThread,
    setClarification,
    handleSendMessage,
    artifactsByThread,
  } = useChatStream(
    auth.authState,
    threads.activeThreadId,
    ensureThread,
    () => { if (auth.authState) threads.refreshThreads(auth.authState.sessionToken); },
    () => { if (auth.authState) threads.refreshThreads(auth.authState.sessionToken); },
    () => {},
    onBindingRefresh,
  );

  const activeArtifacts = useMemo(
    () => (threads.activeThreadId ? artifactsByThread[threads.activeThreadId] ?? [] : []),
    [threads.activeThreadId, artifactsByThread],
  );
  const lastAssistantIndex = useMemo(
    () =>
      timelineItems.reduce(
        (lastIndex, item, index) => (item.item_type === "assistant" ? index : lastIndex),
        -1,
      ),
    [timelineItems],
  );

  useEffect(() => {
    if (!auth.authState || !threads.activeThreadId) {
      setSelectedArtifact(null);
      setArtifactPreview("");
      setArtifactPreviewError("");
      setArtifactListError("");
      return;
    }
    void (async () => {
      const items = await listThreadItems(auth.authState!.sessionToken, threads.activeThreadId!);
      setTimelineItems(items);
      setStreamingAssistant("");
      setStatusMessage("");
      setClarification(null);
      try {
        const arts = await listThreadArtifacts(auth.authState!.sessionToken, threads.activeThreadId!);
        setArtifactsByThread((prev: Record<string, WeReadArtifact[]>) => ({ ...prev, [threads.activeThreadId!]: arts }));
        setArtifactListError("");
      } catch (e) {
        setArtifactListError(e instanceof Error ? e.message : "文件列表加载失败");
      }
    })();
  }, [auth.authState, threads.activeThreadId]);

  useEffect(() => {
    const markdownArtifacts = activeArtifacts.filter((a) => a.kind !== "weread_qr_code");
    if (markdownArtifacts.length === 0) {
      setSelectedArtifact(null);
      setArtifactPreview("");
      setArtifactPreviewError("");
      return;
    }
    if (!selectedArtifact || selectedArtifact.kind === "weread_qr_code" || !markdownArtifacts.some((a) => a.artifact_id === selectedArtifact.artifact_id)) {
      setSelectedArtifact(markdownArtifacts[0]);
    }
  }, [activeArtifacts, selectedArtifact]);

  useEffect(() => {
    const docId = selectedArtifact?.doc_id;
    if (!auth.authState || !selectedArtifact || selectedArtifact.kind === "weread_qr_code" || !docId) {
      setArtifactPreview("");
      setArtifactPreviewError("");
      return;
    }
    let cancelled = false;
    setIsArtifactLoading(true);
    setArtifactPreviewError("");
    void (async () => {
      try {
        const preview = await getWeReadDocumentPreview(auth.authState!.sessionToken, docId);
        if (!cancelled) setArtifactPreview(preview);
      } catch (e) {
        if (!cancelled) {
          setArtifactPreview("");
          setArtifactPreviewError(e instanceof Error ? e.message : "文件预览加载失败");
        }
      } finally {
        if (!cancelled) setIsArtifactLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth.authState, selectedArtifact]);

  useEffect(() => {
    if (auth.authState) return;
    threads.setThreads([]);
    setTimelineItems([]);
    setStreamingAssistant("");
    setStatusMessage("");
    setClarification(null);
    setArtifactsByThread({});
    setSelectedArtifact(null);
    setArtifactPreview("");
    setArtifactPreviewError("");
    setArtifactListError("");
  }, [auth.authState]);

  const handleOpenArtifact = useCallback(async () => {
    const docId = selectedArtifact?.doc_id;
    if (!auth.authState || !selectedArtifact || selectedArtifact.kind === "weread_qr_code" || !docId) return;
    try {
      const blob = await getWeReadDocumentBlob(auth.authState.sessionToken, docId);
      const url = window.URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener,noreferrer");
      setTimeout(() => window.URL.revokeObjectURL(url), 60000);
    } catch (e) {
      setArtifactPreviewError(e instanceof Error ? e.message : "文件打开失败");
    }
  }, [auth.authState, selectedArtifact]);

  const handleDownloadArtifact = useCallback(async () => {
    const docId = selectedArtifact?.doc_id;
    if (!auth.authState || !selectedArtifact || selectedArtifact.kind === "weread_qr_code" || !docId) return;
    try {
      const blob = await getWeReadDocumentBlob(auth.authState.sessionToken, docId, true);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = selectedArtifact.name;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(() => window.URL.revokeObjectURL(url), 60000);
    } catch (e) {
      setArtifactPreviewError(e instanceof Error ? e.message : "文件下载失败");
    }
  }, [auth.authState, selectedArtifact]);

  if (!auth.authState) {
    return (
      <AuthPanel
        authState={auth.authState}
        email={auth.email}
        password={auth.password}
        authMode={auth.authMode}
        authError={auth.authError}
        onChangeEmail={auth.setEmail}
        onChangePassword={auth.setPassword}
        onChangeAuthMode={auth.setAuthMode}
        onSubmit={auth.handleAuthenticate}
      />
    );
  }

  return (
    <div className="app-shell">
      <Sidebar sessionId={auth.authState.sessionId} onLogout={auth.handleLogout} activeTab={activeTab} onTabChange={(tab) => { setActiveTab(tab); }}>
        {activeTab === "chat" ? (
          <>
            <WeReadBindingCard
          wereadBinding={weread.wereadBinding}
          bindingError={weread.bindingError}
          qrLoginSession={weread.qrLoginSession}
          isStartingQrLogin={weread.isStartingQrLogin}
          isValidatingBinding={weread.isValidatingBinding}
          onStartQrLogin={weread.handleStartQrLogin}
          onCancelQrLogin={weread.handleCancelQrLogin}
          onValidateBinding={weread.handleValidateBinding}
          onClearBinding={weread.handleClearBinding}
        />
        <WeChatBindingCard
          channel={wechat.channel}
          error={wechat.error}
          qrData={wechat.qrData}
          isGeneratingQr={wechat.isGeneratingQr}
          isPolling={wechat.isPolling}
          onStartQrLogin={wechat.handleStartQrLogin}
          onCancelQrLogin={wechat.handleCancelQrLogin}
          onStartChannel={wechat.handleStartChannel}
          onStopChannel={wechat.handleStopChannel}
        />
        <ThreadList
          threads={threads.threads}
          activeThreadId={threads.activeThreadId}
          isLoading={threads.isLoadingThreads}
          onSelectThread={threads.setActiveThreadId}
          onCreateThread={threads.handleCreateThread}
          onRenameThread={threads.handleRenameThread}
          onDeleteThread={(id) => {
            threads.handleDeleteThread(id);
            setArtifactsByThread((prev: Record<string, WeReadArtifact[]>) => {
              const next = { ...prev };
              delete next[id];
              return next;
            });
          }}
            />
          </>
        ) : (
          <ResourcesTree
            treeNodes={resources.treeNodes}
            expanded={resources.expanded}
            selected={resources.selected}
            loading={resources.loading}
            error={resources.error}
            onToggleExpand={resources.toggleExpand}
            onOpenContent={resources.openContent}
            onLoadTree={resources.loadTree}
            savedFileTitle={resources.savedFileTitle}
            memoryFileTitle={resources.memoryFileTitle}
          />
        )}
      </Sidebar>

      {activeTab === "chat" ? (
        <main className="chat-panel">
        <header className="chat-header">
          <div>
            <div className="chat-header-kicker">当前线程</div>
            <h1>{threads.activeThread?.title || "请选择或新建一个线程"}</h1>
          </div>
          {statusMessage ? <div className="status-pill">{statusMessage}</div> : null}
        </header>

        <section className="timeline">
          <ArtifactPanel
            artifacts={activeArtifacts}
            selectedArtifact={selectedArtifact}
            artifactPreview={artifactPreview}
            artifactPreviewError={artifactPreviewError}
            artifactListError={artifactListError}
            isArtifactLoading={isArtifactLoading}
            onSelectArtifact={setSelectedArtifact}
            onOpenArtifact={handleOpenArtifact}
            onDownloadArtifact={handleDownloadArtifact}
          />

          {timelineItems.length === 0 && !streamingAssistant ? (
            <div className="empty-state">
              <h3>和微信读书助手聊一聊</h3>
              <p>试试问：书架总览、查看划线想法、笔记解读、导出分析文档...</p>
            </div>
          ) : null}

          {timelineItems.map((item, idx) => {
            const isLastAssistant = item.item_type === "assistant" && lastAssistantIndex === idx;
            const qrArtifact = isLastAssistant && !weread.wereadBinding?.connected
              ? activeArtifacts.find((a) => a.kind === "weread_qr_code") ?? null
              : null;
            return (
              <TimelineItem
                key={`${item.thread_id}-${item.seq}`}
                item={item}
                qrArtifact={qrArtifact}
                onSendOption={handleSendMessage}
              />
            );
          })}

          {toolCallActive && <ToolCallIndicator active={toolCallActive} />}

          {streamingAssistant ? <StreamingMessage content={streamingAssistant} /> : null}

          {streamError ? (
            <article className="timeline-item item-capability_notice">
              <div className="item-label">错误</div>
              <div className="item-body">
                <p className="stream-error">{streamError}</p>
              </div>
            </article>
          ) : null}

          {clarification &&
          !timelineItems.some(
            (it) => it.item_type === "clarification" && it.content === clarification.question,
          ) ? (
            <ClarificationFloating
              clarification={clarification}
              onSendOption={handleSendMessage}
            />
          ) : null}
        </section>

        <Composer
          value={composerValue}
          isSubmitting={isSubmitting}
          onChange={setComposerValue}
          onSend={() => handleSendMessage()}
        />
      </main>
      ) : (
        <ResourcesViewer
          selected={resources.selected}
          content={resources.content}
          editorContent={resources.editorContent}
          isEditing={resources.isEditing}
          loading={resources.loading}
          saving={resources.saving}
          selectedTitle={resources.selectedTitle}
          isEditable={resources.isEditable}
          onStartEdit={resources.startEdit}
          onCancelEdit={resources.cancelEdit}
          onSaveEdit={resources.saveEdit}
          onEditorContentChange={resources.setEditorContent}
        />
      )}
    </div>
  );
}

export default App;
