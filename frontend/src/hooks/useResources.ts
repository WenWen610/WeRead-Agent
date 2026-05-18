import { useCallback, useEffect, useMemo, useState } from "react";
import type { BookNode, MemoryFileMeta, SavedFileMeta, UserWereadBook } from "../types";
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

export type TreeNode =
  | { kind: "book"; book: BookNode }
  | { kind: "weread_doc"; book: BookNode; doc: NonNullable<BookNode["weread_docs"]>[number] }
  | { kind: "saved_file"; book: BookNode; file: NonNullable<BookNode["saved_files"]>[number] }
  | { kind: "memory"; file: MemoryFileMeta }
  | { kind: "section"; label: string };

export type ContentView =
  | { kind: "weread"; docId: string; title: string; sourceType: string }
  | { kind: "saved"; filePath: string; title: string }
  | { kind: "memory"; filePath: string; title: string }
  | null;

export function useResources(sessionToken: string) {
  const [treeNodes, setTreeNodes] = useState<TreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<ContentView>(null);
  const [content, setContent] = useState("");
  const [editorContent, setEditorContent] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const savedFileTitle = useCallback((path: string): string => {
    const name = path.split("/").pop() ?? path;
    return name.replace(/\.md$/i, "");
  }, []);

  const memoryFileTitle = useCallback((path: string): string => {
    return path.replace(/\.md$/i, "");
  }, []);

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
  }, [sessionToken, savedFileTitle]);

  useEffect(() => {
    if (sessionToken) void loadTree();
  }, [sessionToken, loadTree]);

  const toggleExpand = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
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

  const selectedTitle = useMemo(() => {
    if (!selected) return "";
    return selected.title;
  }, [selected]);

  const isEditable = selected?.kind === "saved" || selected?.kind === "memory";

  return {
    treeNodes,
    expanded,
    selected,
    content,
    editorContent,
    isEditing,
    loading,
    error,
    saving,
    loadTree,
    toggleExpand,
    openContent,
    startEdit,
    cancelEdit,
    saveEdit,
    setEditorContent,
    savedFileTitle,
    memoryFileTitle,
    selectedTitle,
    isEditable,
  };
}
