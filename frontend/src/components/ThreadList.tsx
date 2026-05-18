import type { ChatThreadSummary } from "../types";

function formatTimestamp(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Props = {
  threads: ChatThreadSummary[];
  activeThreadId: string | null;
  isLoading: boolean;
  onSelectThread: (id: string) => void;
  onCreateThread: () => void;
  onRenameThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
};

export function ThreadList({
  threads,
  activeThreadId,
  isLoading,
  onSelectThread,
  onCreateThread,
  onRenameThread,
  onDeleteThread,
}: Props) {
  return (
    <>
      <div style={{ marginBottom: 8 }}>
        <button className="ghost-button" onClick={onCreateThread} type="button">
          新建
        </button>
      </div>
      <div className="thread-list">
        {isLoading ? <div className="thread-empty">加载中...</div> : null}
        {!isLoading && threads.length === 0 ? (
          <div className="thread-empty">还没有线程，先新建一个。</div>
        ) : null}
        {threads.map((thread) => (
          <button
            key={thread.thread_id}
            className={`thread-card ${thread.thread_id === activeThreadId ? "is-active" : ""}`}
            onClick={() => onSelectThread(thread.thread_id)}
            type="button"
          >
            <div className="thread-card-main">
              <strong>{thread.title || "未命名线程"}</strong>
              <span>{thread.last_item_preview || "还没有消息"}</span>
            </div>
            <div className="thread-card-meta">
              <span>{formatTimestamp(thread.updated_at)}</span>
              <div className="thread-card-actions">
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    onRenameThread(thread.thread_id);
                  }}
                >
                  改名
                </span>
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteThread(thread.thread_id);
                  }}
                >
                  删除
                </span>
              </div>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}
