---
name: weread-notes-analysis
description: 用于分析单本微信读书的用户划线、想法和章节笔记，生成中文解读、主题归纳、用户关注点和复习建议。Use this when analyzing one WeRead notes document.
---

# 微信读书划线/想法分析

## 使用场景

当用户要求“解读我在这本书里的划线”“分析我的想法”“总结这本书的笔记”时使用本 skill。

## 工作流

1. 如果目标书籍不明确，先用 `resolve_weread_book` 判断是否唯一。
2. 如果返回多个候选或未找到，不要继续读取；返回 `clarification_required`、问题和候选项给主 agent。
3. 使用 `ensure_weread_book_document` 确保本地 markdown 文档存在。
4. 使用 `read_weread_document_outline` 获取文档章节结构。
5. 根据章节逐段调用 `read_weread_document_section`，不要要求主 agent 一次性提供全文。
6. 区分用户划线的原文和用户自己的想法/点评。
7. 将每个章节压缩为要点，再综合全书范围内的主题和关注点。
8. 返回给主 agent 的结果要简洁，包含：核心主题、用户关注点、代表性片段、可继续追问的问题。

## 规则

- 不要修改微信读书 markdown。
- 不要写长期 memory。
- 不要把所有原文搬回主 agent，上下文很长时只返回必要证据。
- 如果章节很多，优先覆盖有想法/点评密度高的章节。
- 你不直接向用户澄清；需要澄清时返回结构化澄清需求给主 agent。
