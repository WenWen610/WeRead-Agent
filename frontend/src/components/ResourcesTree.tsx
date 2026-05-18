import type { TreeNode, ContentView } from "../hooks/useResources";

type Props = {
  treeNodes: TreeNode[];
  expanded: Set<string>;
  selected: ContentView;
  loading: boolean;
  error: string;
  onToggleExpand: (key: string) => void;
  onOpenContent: (view: ContentView) => void;
  onLoadTree: () => void;
  savedFileTitle: (path: string) => string;
  memoryFileTitle: (path: string) => string;
};

function sourceLabel(st: string): string {
  return st === "marks" ? "划线" : "想法";
}

function fileIcon(st: string): string {
  return st === "marks" ? "📝" : "💡";
}

export function ResourcesTree({
  treeNodes,
  expanded,
  selected,
  loading,
  error,
  onToggleExpand,
  onOpenContent,
  onLoadTree,
  savedFileTitle,
  memoryFileTitle,
}: Props) {
  return (
    <div className="resources-tree">
      <div className="resources-tree-header">
        <h3>资源目录</h3>
        <button className="ghost-button" onClick={onLoadTree} type="button" disabled={loading}>
          刷新
        </button>
      </div>
      {error ? <div className="binding-error">{error}</div> : null}
      <div className="resources-tree-list">
        {treeNodes.map((node, i) => {
          if (node.kind === "section") {
            return <div key={`s-${node.label}-${i}`} className="resource-section-label">{node.label}</div>;
          }
          if (node.kind === "saved_file") {
            const title = savedFileTitle(node.file.path);
            const isSel = selected?.kind === "saved" && selected.filePath === node.file.path;
            return (
              <button
                key={`sf-${node.file.path}`}
                className={`resource-item ${isSel ? "is-active" : ""}`}
                onClick={() => onOpenContent({ kind: "saved", filePath: node.file.path, title })}
                type="button"
              >
                <span>📊 {title}</span>
              </button>
            );
          }
          if (node.kind === "memory") {
            const title = memoryFileTitle(node.file.path);
            const isSel = selected?.kind === "memory" && selected.filePath === node.file.path;
            const todayName = new Date().toISOString().slice(0, 10) + ".md";
            const isToday = node.file.path.endsWith(todayName);
            return (
              <button
                key={`m-${node.file.path}`}
                className={`resource-item resource-memory ${isSel ? "is-active" : ""}`}
                onClick={() => onOpenContent({ kind: "memory", filePath: node.file.path, title })}
                type="button"
              >
                <span>{isToday ? "📝" : "📄"} {title}</span>
              </button>
            );
          }
          if (node.kind === "book") {
            const key = `b-${node.book.book_id}`;
            const isExpanded = expanded.has(key);
            return (
              <div key={key}>
                <button
                  className="resource-item resource-book"
                  onClick={() => onToggleExpand(key)}
                  type="button"
                >
                  <span className={`resource-chevron ${isExpanded ? "open" : ""}`}>›</span>
                  <span>📖 {node.book.book_title}</span>
                  <span className="resource-count">
                    {node.book.weread_docs.length + node.book.saved_files.length}
                  </span>
                </button>
                {isExpanded && (
                  <div className="resource-children">
                    {node.book.weread_docs.map((doc) => {
                      const label = fileIcon(doc.source_type) + " " + sourceLabel(doc.source_type) + "笔记";
                      const isSel = selected?.kind === "weread" && selected.docId === doc.doc_id;
                      return (
                        <button
                          key={`wd-${doc.doc_id}`}
                          className={`resource-item resource-child ${isSel ? "is-active" : ""}`}
                          onClick={() => onOpenContent({ kind: "weread", docId: doc.doc_id, title: label, sourceType: doc.source_type })}
                          type="button"
                        >
                          {label}
                        </button>
                      );
                    })}
                    {node.book.saved_files.map((sf) => {
                      const title = "📊 " + savedFileTitle(sf.path);
                      const isSel = selected?.kind === "saved" && selected.filePath === sf.path;
                      return (
                        <button
                          key={`sf-${sf.path}`}
                          className={`resource-item resource-child ${isSel ? "is-active" : ""}`}
                          onClick={() => onOpenContent({ kind: "saved", filePath: sf.path, title })}
                          type="button"
                        >
                          {title}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          }
          return null;
        })}
      </div>
    </div>
  );
}
