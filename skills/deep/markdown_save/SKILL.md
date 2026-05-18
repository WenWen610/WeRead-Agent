---
name: markdown-save
description: Save AI analyses as local markdown files for later retrieval.
---

**注意**：微信读书的划线/想法笔记已由 `present_weread_book_document` 持久化在 weread 文档库中，不需要本 skill 重复保存。

## 使用场景

- 用户说"保存这个分析"、"存下来"、"存档"
- 用户说"读一下上次那个分析"、"之前存的 XX 再讨论一下"
- 用户说"我存了哪些分析"

## 工具说明

使用 deepagent 内置的 `write_file`、`read_file`、`ls` 工具。文件路径以 `/saved/` 开头。

| 操作 | 工具 | 路径示例 |
|------|------|------|
| 保存分析 | `write_file` | `/saved/{book_id}/{YYYY-MM-DD} - {主题}.md` |
| 读回分析 | `read_file` | `/saved/{book_id}/{YYYY-MM-DD} - {主题}.md` |
| 列出存档 | `ls` | `/saved/` 或 `/saved/{book_id}/` |

## 书籍上下文确定

调用工具前确定目标书籍：
- **如果当前对话已有 `current_book`**，直接用其 `book_id` 作为目录名。
- **如果 `current_book` 不存在**，先调 `resolve_weread_book` 获取 `book_id`。
- **如果不确定是哪本书**，调 `ask_clarification`。

## 保存分析

当用户要求将当前分析存档时：
1. 回顾对话中的分析内容，整理为 Markdown
2. 文档开头包含基本元信息（书名、日期、分析主题）
3. 调用 `write_file` 写入 `/saved/{book_id}/{日期} - {主题}.md`

**内容格式**：
```markdown
# {书名} - {分析主题}

> 分析日期: {YYYY-MM-DD}

## 核心内容
...
```

## 读回存档

用户说"读一下上次的分析"时：
1. 如果不确定文件路径，先用 `ls /saved/` 或 `ls /saved/{book_id}/` 查看
2. 用 `read_file` 读取内容
3. 基于内容回答用户

## 调用后行为

保存后简短回复保存路径即可（如"已保存到 /saved/{book_id}/2025-05-09 - 主题分析.md"）。不要描述你刚保存的内容。

## 边界

- **仅在用户明确要求存档时使用**，不要主动建议。
- 此功能是本地文件存储，不是长期记忆。不对接 FTS 或向量索引。
- 保存内容可被用户编辑，下次读回的是最新版本。
- **不要用 `present_weread_book_document` 或 `ensure_weread_book_document` 来完成存档**——划线/想法笔记已由它们自动持久化，本 skill 只管理 Agent 生成的分析。
