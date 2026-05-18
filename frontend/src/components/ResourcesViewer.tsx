import type { ContentView } from "../hooks/useResources";
import { MarkdownContent } from "./TimelineItem";

type Props = {
  selected: ContentView;
  content: string;
  editorContent: string;
  isEditing: boolean;
  loading: boolean;
  saving: boolean;
  selectedTitle: string;
  isEditable: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onEditorContentChange: (v: string) => void;
};

export function ResourcesViewer({
  selected,
  content,
  editorContent,
  isEditing,
  loading,
  saving,
  selectedTitle,
  isEditable,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onEditorContentChange,
}: Props) {
  return (
    <div className="resources-viewer">
      {selected ? (
        <>
          <div className="resources-viewer-header">
            <h4>{selectedTitle}</h4>
            <div className="resources-viewer-actions">
              {isEditable && !isEditing && (
                <button className="ghost-button" onClick={onStartEdit} type="button">编辑</button>
              )}
              {isEditing && (
                <>
                  <button className="ghost-button" onClick={onSaveEdit} type="button" disabled={saving}>
                    {saving ? "保存中..." : "保存"}
                  </button>
                  <button className="ghost-button" onClick={onCancelEdit} type="button">取消</button>
                </>
              )}
            </div>
          </div>
          {isEditing ? (
            <textarea
              className="resources-editor"
              value={editorContent}
              onChange={(e) => onEditorContentChange(e.target.value)}
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
  );
}
