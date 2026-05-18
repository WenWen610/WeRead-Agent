---
name: weread-light-tasks
description: 主 agent 用于处理轻量微信读书 metadata 查询。Use for lightweight WeRead metadata tasks in the main agent.
---

# 微信读书轻量任务

## 意图 → 工具映射

| 用户意图 | 使用的工具 | source_type |
|---|---|---|
| 书架整体情况、统计概览 | `get_weread_bookshelf_snapshot` | — |
| 书架里有没有 XX 类书 | `search_weread_bookshelf`（按分类搜索是搜索，不是概览） | — |
| 书架里 XX 作者的书 | `search_weread_bookshelf` | — |
| 书架里按关键词找书 | `search_weread_bookshelf` | — |
| 单本书阅读进度 | `resolve_weread_book` → `get_weread_book_reading_status` | — |
| 单本书笔记/划线/想法数量 | `resolve_weread_book` → `get_weread_book_note_counts` | — |
| 用户说"笔记"→ 检索 | `resolve_weread_book` → `search_weread_notes` | `both` |
| 用户明确说"划线"→ 检索 | `resolve_weread_book` → `search_weread_notes` | `marks` |
| 用户明确说"想法/点评/我说了什么"→ 检索 | `resolve_weread_book` → `search_weread_notes` | `reviews` |
| 展示笔记文档 | `resolve_weread_book` → `present_weread_book_document` | `both` |
| 展示划线文档 | `resolve_weread_book` → `present_weread_book_document` | `marks` |
| 展示想法文档 | `resolve_weread_book` → `present_weread_book_document` | `reviews` |

## `source_type` 参数规则

`search_weread_notes` 和 `present_weread_book_document` 的 `source_type`：
- 用户笼统说"笔记"或未明确指定 → `both`
- 用户明确说"划线" → `marks`
- 用户明确说"想法/点评/思考/我说了什么/我的看法" → `reviews`

## 需要 `resolve_weread_book` 做前置的工具

以下工具需要 `book_id` 参数，必须先调用 `resolve_weread_book` 定位目标书：
- `get_weread_book_reading_status`
- `get_weread_book_note_counts`
- `search_weread_notes`
- `present_weread_book_document`

## 澄清

- 当 `resolve_weread_book` 返回多个候选书时，调用 `ask_clarification` 让用户选择，**不要猜测**。
- 当用户提问信息不足（如只说"帮我查一下"而未指明具体需求），调用 `ask_clarification`。

## 上下文复用

- 如果前面对话中 `get_weread_bookshelf_snapshot` 已返回各书的进度数据，可不重复调用 `get_weread_book_reading_status`，直接基于上下文回答。

## 边界

以下场景必须委派 subagent（weread_notes_analyst），主 agent 绝不自己处理：
- 用户要求**全量分析**某本书的所有划线、全部想法/点评
- 用户要求**全面总结、整体解读**划线内容（而非检索特定片段）
→ 委派给 `weread_notes_analyst`

以下场景主 agent 自行处理，无需委派：
- 检索笔记内容、查找/对比具体笔记 → `search_weread_notes`（根据用户措辞选择 source_type，见上表）
- 检索划线内容 → `search_weread_notes`（source_type=`marks`）
- 检索想法/点评内容 → `search_weread_notes`（source_type=`reviews`）
