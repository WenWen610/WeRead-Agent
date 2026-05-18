import { useCallback, useEffect, useMemo, useState } from "react";
import type { BookNode, MemoryFileMeta, SavedFileMeta, WereadDocMeta, UserWereadBook } from "../types";
import {
  getWeReadDocumentPreview,
  listMemoryFiles,
  listSavedFiles,
  listUserWereadBooks,
  readMemoryFile,
  readSavedFile,
  rebuildMemoryIndex,
  writeMemoryFile,
  writeSavedFile,
} from "../api";
import { MarkdownContent } from "./TimelineItem";

type Props = {
  sessionToken: string;
};

type TreeNode =
  | { kind: "book"; book: BookNode }
  | { kind: "weread_doc"; book: BookNode; doc: NonNullable<BookNode["weread_docs"]>[number] }
  | { kind: "saved_file"; book: BookNode; file: NonNullable<BookNode["saved_files"]>[number] }
  | { kind: "memory"; file: MemoryFileMeta }
  | { kind: "section"; label: string };

type ContentView =
  | { kind: "weread"; docId: string; title: string; sourceType: string }
  | { kind: "saved"; filePath: string; title: string }
  | { kind: "memory"; filePath: string; title: string }
  | null;

function formatTime(ts: string | undefined): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function sourceLabel(st: string): string {
  return st === "marks" ? "划线" : "想法";
}

function fileIcon(st: string): string {
  return st === "marks" ? "📝" : "💡";
}

export function ResourcesPanel({ sessionToken }: Props) {
  const [treeNodes, setTreeNodes] = useState<TreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<ContentView>(null);
  const [content, setContent] = useState("");
  const [editorContent, setEditorContent] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const loadTree = useCallback(async () => {
    setError("");
    try {
      const [savedFiles, memoryFiles, wereadBooks] = await Promise.all([
        listSavedFiles(sessionToken).catch((): SavedFileMeta[] => []),
        listMemoryFiles(sessionToken).catch((): MemoryFileMeta[] => []),
        listUserWereadBooks(sessionToken).catch((): UserWereadBook[] => []),
      ]);

      const bookMap = new Map<string, BookNode>();
      for (const wb of wereadBooks) {
        if (!wb || !wb.book_id) continue;
        bookMap.set(wb.book_id, {
          book_id: wb.book_id,
          book_title: wb.book_title || wb.book_id,
          weread_docs: wb.documents || [],
          saved_files: [],
        });
      }

      const orphanSavedFiles: SavedFileMeta[] = [];

      for (const sf of savedFiles) {
        const bookId = sf.book_id;
        if (bookId && bookMap.has(bookId)) {
          bookMap.get(bookId)!.saved_files.push(sf);
        } else {
          orphanSavedFiles.push(sf);
        }
      }

      const nodes: TreeNode[] = [{ kind: "section", label: "微信读书" }];
      for (const book of bookMap.values()) {
        nodes.push({ kind: "book", book });
      }
      if (orphanSavedFiles.length > 0) {
        nodes.push({ kind: "section", label: "存档分析" });
        for (const sf of orphanSavedFiles) {
          const title = savedFileTitle(sf.path);
          nodes.push({
            kind: "saved_file",
            book: { book_id: "", book_title: title, weread_docs: [], saved_files: [] },
            file: sf,
          });
        }
      }
      if (memoryFiles.length > 0) {
        const longTermFiles = memoryFiles.filter((mf) => !mf.path.startsWith("memory/"));
        const dailyFiles = memoryFiles.filter((mf) => mf.path.startsWith("memory/"));
        const todayName = new Date().toISOString().slice(0, 10) + ".md";

        if (longTermFiles.length > 0) {
          nodes.push({ kind: "section", label: "长期记忆" });
          for (const mf of longTermFiles) {
            nodes.push({ kind: "memory", file: mf });
          }
        }
        if (dailyFiles.length > 0) {
          nodes.push({ kind: "section", label: "日记记录" });
          for (const mf of dailyFiles) {
            nodes.push({ kind: "memory", file: mf });
          }
        }
      }
      setTreeNodes(nodes);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, [sessionToken]);

  useEffect(() => {
    if (sessionToken) void loadTree();
  }, [sessionToken, loadTree]);

  const toggleExpand = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const openContent = useCallback(async (view: ContentView) => {
    setSelected(view);
    setIsEditing(false);
    setEditorContent("");
    if (!view) { setContent(""); return; }
    setLoading(true);
    setError("");
    try {
      let text = "";
      if (view.kind === "weread") {
        text = await getWeReadDocumentPreview(sessionToken, view.docId);
      } else if (view.kind === "saved") {
        text = await readSavedFile(sessionToken, view.filePath);
      } else if (view.kind === "memory") {
        text = await readMemoryFile(sessionToken, view.filePath);
      }
      setContent(text);
      setEditorContent(text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取失败");
    } finally {
      setLoading(false);
    }
  }, [sessionToken]);

  const startEdit = useCallback(() => {
    setEditorContent(content);
    setIsEditing(true);
  }, [content]);

  const cancelEdit = useCallback(() => {
    setEditorContent(content);
    setIsEditing(false);
  }, [content]);

  const saveEdit = useCallback(async () => {
    if (!selected || selected.kind === "weread") return;
    setSaving(true);
    setError("");
    try {
      if (selected.kind === "saved") {
        await writeSavedFile(sessionToken, selected.filePath, editorContent);
      } else if (selected.kind === "memory") {
        await writeMemoryFile(sessionToken, selected.filePath, editorContent);
        try { await rebuildMemoryIndex(sessionToken); } catch { /* ok */ }
      }
      setContent(editorContent);
      setIsEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }, [selected, editorContent, sessionToken]);

  const savedFileTitle = useCallback((path: string): string => {
    const name = path.split("/").pop() ?? path;
    return name.replace(/\.md$/i, "");
  }, []);

  const memoryFileTitle = useCallback((path: string): string => {
    return path.replace(/\.md$/i, "");
  }, []);

  const selectedTitle = useMemo(() => {
    if (!selected) return "";
    return selected.title;
  }, [selected]);

  const isEditable = selected?.kind === "saved" || selected?.kind === "memory";

  return (
    <div className="resources-panel">
      <div className="resources-tree">
        <div className="resources-tree-header">
          <h3>资源目录</h3>
          <button className="ghost-button" onClick={loadTree} type="button" disabled={loading}>
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
                  onClick={() => openContent({ kind: "saved", filePath: node.file.path, title })}
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
                  onClick={() => openContent({ kind: "memory", filePath: node.file.path, title })}
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
                    onClick={() => toggleExpand(key)}
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
                            onClick={() => openContent({ kind: "weread", docId: doc.doc_id, title: label, sourceType: doc.source_type })}
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
                            onClick={() => openContent({ kind: "saved", filePath: sf.path, title })}
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
      <div className="resources-viewer">
        {selected ? (
          <>
            <div className="resources-viewer-header">
              <h4>{selectedTitle}</h4>
              <div className="resources-viewer-actions">
                {isEditable && !isEditing && (
                  <button className="ghost-button" onClick={startEdit} type="button">编辑</button>
                )}
                {isEditing && (
                  <>
                    <button className="ghost-button" onClick={saveEdit} type="button" disabled={saving}>
                      {saving ? "保存中..." : "保存"}
                    </button>
                    <button className="ghost-button" onClick={cancelEdit} type="button">取消</button>
                  </>
                )}
              </div>
            </div>
            {isEditing ? (
              <textarea
                className="resources-editor"
                value={editorContent}
                onChange={(e) => setEditorContent(e.target.value)}
              />
            ) : loading ? (
              <div className="artifact-preview-empty">正在加载...</div>
            ) : (
              <div className="artifact-preview-content"><MarkdownContent content={content} /></div>
            )}
          </>
        ) : (
          <div className="empty-state">
            <h3>选择一个文件查看</h3>
            <p>左侧目录包含微信读书的划线/想法笔记、已保存的分析，以及长期记忆。</p>
          </div>
        )}
      </div>
    </div>
  );
}
