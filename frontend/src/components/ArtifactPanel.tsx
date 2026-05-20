import type { WeReadArtifact } from "../types";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

function formatTimestamp(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatArtifactSourceLabel(sourceType: WeReadArtifact["source_type"]): string {
  return sourceType === "marks" ? "划线" : "想法";
}

type Props = {
  artifacts: WeReadArtifact[];
  selectedArtifact: WeReadArtifact | null;
  artifactPreview: string;
  artifactPreviewError: string;
  artifactListError: string;
  isArtifactLoading: boolean;
  onSelectArtifact: (a: WeReadArtifact) => void;
  onOpenArtifact: () => void;
  onDownloadArtifact: () => void;
};

export function ArtifactPanel({
  artifacts,
  selectedArtifact,
  artifactPreview,
  artifactPreviewError,
  artifactListError,
  isArtifactLoading,
  onSelectArtifact,
  onOpenArtifact,
  onDownloadArtifact,
}: Props) {
  const markdownArtifacts = artifacts.filter((a) => a.kind !== "weread_qr_code");

  if (markdownArtifacts.length === 0 && artifacts.filter((a) => a.kind === "weread_qr_code").length === 0) return null;

  return (
    <section className="artifacts-panel">
      {markdownArtifacts.length > 0 ? (
        <>
          <div className="artifacts-panel-header">
            <div>
              <div className="chat-header-kicker">Artifacts</div>
              <h3>本地笔记文件</h3>
            </div>
            <span className="artifacts-count">{markdownArtifacts.length} 个文件</span>
          </div>
          {artifactListError ? <div className="binding-error">{artifactListError}</div> : null}
          <div className="artifact-card-list">
            {markdownArtifacts.map((artifact) => (
              <button
                key={artifact.artifact_id}
                className={`artifact-card ${selectedArtifact?.artifact_id === artifact.artifact_id ? "is-active" : ""}`}
                onClick={() => onSelectArtifact(artifact)}
                type="button"
              >
                <div className="artifact-card-meta">
                  <span className={`artifact-source is-${artifact.source_type}`}>
                    {formatArtifactSourceLabel(artifact.source_type)}
                  </span>
                  <span>{formatTimestamp(artifact.generated_at ?? null)}</span>
                </div>
                <strong>{artifact.book_title}</strong>
                <span>{artifact.name}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}
      {selectedArtifact && selectedArtifact.kind !== "weread_qr_code" ? (
        <article className="artifact-preview">
          <div className="artifact-preview-header">
            <div>
              <div className="sidebar-kicker">
                {formatArtifactSourceLabel(selectedArtifact.source_type)}
              </div>
              <h4>{selectedArtifact.book_title}</h4>
              <p>{selectedArtifact.name}</p>
            </div>
            <div className="artifact-preview-actions">
              <button className="ghost-button" onClick={onOpenArtifact} type="button">
                打开
              </button>
              <button className="ghost-button" onClick={onDownloadArtifact} type="button">
                下载
              </button>
            </div>
          </div>
          {isArtifactLoading ? (
            <div className="artifact-preview-empty">正在加载 markdown 预览...</div>
          ) : artifactPreviewError ? (
            <div className="binding-error">{artifactPreviewError}</div>
          ) : (
            <div className="artifact-preview-content">
              <ReactMarkdown>{artifactPreview}</ReactMarkdown>
            </div>
          )}
        </article>
      ) : null}
    </section>
  );
}
