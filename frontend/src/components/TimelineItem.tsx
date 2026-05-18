import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatTimelineItem, ClarificationPayload, WeReadArtifact } from "../types";

function formatTimelineItemLabel(itemType: ChatTimelineItem["item_type"]): string {
  switch (itemType) {
    case "user":
      return "你";
    case "assistant":
      return "助手";
    case "clarification":
      return "澄清";
    case "capability_notice":
      return "提示";
    default:
      return "记录";
  }
}

type Props = {
  item: ChatTimelineItem;
  qrArtifact?: WeReadArtifact | null;
  onSendOption: (option: string) => void;
};

export function TimelineItem({ item, qrArtifact, onSendOption }: Props) {
  return (
    <article className={`timeline-item item-${item.item_type}`}>
      <div className="item-label">{formatTimelineItemLabel(item.item_type)}</div>
      <div className="item-body">
        {item.item_type === "assistant" ? (
          <MarkdownContent content={item.content} />
        ) : (
          <p>{item.content}</p>
        )}
        {item.item_type === "clarification" && Array.isArray(item.metadata.options) ? (
          <div className="clarification-options">
            {(item.metadata.options as unknown[]).map((option, i) => (
              <button key={`${item.id}-${i}`} onClick={() => onSendOption(String(option))} type="button">
                {String(option)}
              </button>
            ))}
          </div>
        ) : null}
        {qrArtifact ? (
          <div className="qr-code-inline">
            {qrArtifact.qr_image_base64 ? (
              <img
                src={`data:image/png;base64,${qrArtifact.qr_image_base64}`}
                alt="WeRead QR Code"
                className="qr-code-image"
              />
            ) : null}
            {qrArtifact.expires_at ? (
              <div className="qr-code-expiry">
                有效期至 {new Date(qrArtifact.expires_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}

export function StreamingMessage({ content }: { content: string }) {
  return (
    <article className="timeline-item item-assistant is-streaming">
      <div className="item-label">助手</div>
      <div className="item-body">
        <MarkdownContent content={content} />
      </div>
    </article>
  );
}

export function ClarificationFloating({
  clarification,
  onSendOption,
}: {
  clarification: ClarificationPayload;
  onSendOption: (option: string) => void;
}) {
  return (
    <article className="timeline-item item-clarification is-floating">
      <div className="item-label">澄清</div>
      <div className="item-body">
        <p>{clarification.question}</p>
        <div className="clarification-options">
          {clarification.options.map((option) => (
            <button key={option} onClick={() => onSendOption(option)} type="button">
              {option}
            </button>
          ))}
        </div>
      </div>
    </article>
  );
}

export function MarkdownContent({ content }: { content: string }) {
  const [collapsedBlocks, setCollapsedBlocks] = useState<Set<string>>(new Set());

  const toggleCollapse = (key: string) => {
    setCollapsedBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          const codeStr = String(children).replace(/\n$/, "");
          const node = props as { node?: { position?: { start?: { line?: number } } } };
          const blockKey = `code-${node?.node?.position?.start?.line ?? 0}`;

          if (match) {
            const isCollapsed = collapsedBlocks.has(blockKey);
            return (
              <div className="code-block">
                <div className="code-block-header" onClick={() => toggleCollapse(blockKey)}>
                  <span className="code-block-lang">{match[1]}</span>
                  <span className="code-block-toggle">{isCollapsed ? "展开" : "折叠"}</span>
                </div>
                {!isCollapsed && (
                  <SyntaxHighlighter
                    style={oneLight}
                    language={match[1]}
                    PreTag="div"
                  >
                    {codeStr}
                  </SyntaxHighlighter>
                )}
              </div>
            );
          }

          return (
            <code className={className} {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
