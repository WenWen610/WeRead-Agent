---
name: artifact-presentation
description: 主 agent 用于展示或导出微信读书 markdown artifact，正文由前端展示，不进入模型上下文。Use when the user wants to show or export markdown artifacts.
---

# Artifact 展示

## 使用场景

用户明确要求展示、导出、打开、预览某本书的微信读书 markdown。

## 规则

- 使用 `present_weread_book_document`。这是本 skill 唯一使用的工具。
- 工具只返回 artifact metadata，不返回 markdown 正文。
- **调用该工具成功后，任务即已完成。立马给出简短回答告知用户文档已准备好，停止调用其他工具或 subagent。不要描述、引用或概括文档内的任何具体划线/想法内容——你只能看到 metadata，正文由前端渲染，不应出现在回答里。**
- 不要把 markdown 正文复制进回答。
- 根据用户要求传正确的 `source_type`：说"笔记"→`both`，说"划线"→`marks`，说"想法/点评"→`reviews`。
- 如果用户要求分析 markdown 内容，而不是展示文档，交给 `weread_notes_analyst`。
- 不要调用 `get_weread_book_note_counts`、`get_weread_book_reading_status`、`search_weread_notes`、`read_weread_document_section` 等工具——展示文档只需要 `present_weread_book_document`。
