"""Sample evaluation cases for the WeRead Agent.

These are ANONYMIZED examples using public book 三体 and fictional books.
Replace with your own cases or create a separate module and pass via --cases.

Case categories:
  - DECISION: tool-calling accuracy (which tool should the agent call?)
  - RETRIEVAL: retrieval quality from known highlights
  - ANALYST: subagent delegation for full-book analysis
  - CURRENT_BOOK: context-carrying across turns
  - MEMORY_INJECTION: memory context retrieval
  - AUTO_MEMORY: background daily-note extraction
  - DREAM: memory consolidation
  - MULTI_TURN: conversation threading
  - STREAM: SSE streaming events
  - ROBUSTNESS: edge cases and error handling
"""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Decision cases — tool-calling accuracy
# ═══════════════════════════════════════════════════════════════════════════════

DECISION_CASES: list[dict] = [
    {
        "inputs": {"question": "我的书架有哪些书"},
        "outputs": {"expected_tools": ["get_weread_bookshelf_snapshot"]},
        "comment": "D1: shelf query → shelf snapshot",
    },
    {
        "inputs": {"question": "书架里有没有科幻类的书"},
        "outputs": {"expected_tools": ["search_weread_bookshelf"]},
        "comment": "D2: category search → bookshelf search",
    },
    {
        "inputs": {"question": "三体我读到哪了"},
        "outputs": {"expected_tools": [
            "resolve_weread_book", "get_weread_book_reading_status"
        ]},
        "comment": "D3: reading progress → resolve + status",
    },
    {
        "inputs": {"question": "三体有多少条划线"},
        "outputs": {"expected_tools": [
            "resolve_weread_book", "get_weread_book_note_counts"
        ]},
        "comment": "D4: note count query → resolve + counts",
    },
    {
        "inputs": {"question": "帮我展示三体的划线"},
        "outputs": {"expected_tools": [
            "resolve_weread_book", "search_weread_notes", "present_weread_book_document"
        ]},
        "comment": "D5: show highlights → resolve + search + present",
    },
    {
        "inputs": {"question": "一本完全不存在的书我读到哪了"},
        "outputs": {"expected_tools": ["resolve_weread_book"]},
        "comment": "D6: unknown book → resolve only, no crash",
    },
    {
        "inputs": {"question": "三体我读完了吗"},
        "outputs": {"expected_tools": [
            "resolve_weread_book", "get_weread_book_reading_status"
        ]},
        "comment": "D7: completion status → resolve + status",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Retrieval cases — search against known highlights
# ═══════════════════════════════════════════════════════════════════════════════

RETRIEVAL_CASES: list[dict] = [
    {
        "inputs": {"question": "三体里关于文明的那句划线'给岁月以文明，而不是给文明以岁月'，帮我找出原文，解读一下"},
        "outputs": {"expected_tools": ["resolve_weread_book", "search_weread_notes"]},
        "comment": "R1: specific highlight retrieval",
    },
    {
        "inputs": {"question": "三体里关于黑暗森林法则我划了'宇宙就是一座黑暗森林'和'弱小和无知不是生存的障碍'这两段。帮我对比梳理"},
        "outputs": {"expected_tools": ["resolve_weread_book", "search_weread_notes"]},
        "comment": "R2: multi-highlight comparison",
    },
    {
        "inputs": {"question": "三体里刘慈欣怎么描写人类文明的？我在划线里标记过相关段落吗"},
        "outputs": {"expected_tools": ["resolve_weread_book", "search_weread_notes"]},
        "comment": "R3: thematic search in highlights",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Analyst cases — subagent delegation for full-book analysis
# ═══════════════════════════════════════════════════════════════════════════════

ANALYST_CASES: list[dict] = [
    {
        "inputs": {"question": "帮我系统分析三体的所有划线笔记，按主题归纳我的关注点"},
        "outputs": {"expected": {
            "subagent": "weread-notes-analyst",
            "required_tools": [
                "resolve_weread_book",
                "ensure_weread_book_document",
                "read_weread_document_outline",
                "read_weread_document_section",
            ],
        }},
        "comment": "A1: full-book analysis → analyst subagent",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Current book cases — context-carrying across queries
# ═══════════════════════════════════════════════════════════════════════════════

CURRENT_BOOK_CASES: list[dict] = [
    {
        "inputs": {"question": "三体我开始读了吗"},
        "outputs": {"expected_tools": ["resolve_weread_book"]},
        "comment": "C1: reading initiation check",
    },
    {
        "inputs": {"question": "这本有多少条划线"},
        "outputs": {"expected_tools": [
            "get_weread_book_note_counts"
        ]},
        "comment": "C2: follow-up after resolve → should reuse current_book",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Memory injection cases — memory context retrieval
# ═══════════════════════════════════════════════════════════════════════════════

MEMORY_INJECTION_CASES: list[dict] = [
    {
        "inputs": {"question": "根据我的阅读背景和书架，帮我推荐一本值得深入分析的书"},
        "outputs": {"expected_tools": []},
        "memory_section": "reader_profile",
        "comment": "M1: recommendation based on memory profile",
    },
    {
        "inputs": {"question": "帮我查三体的笔记"},
        "outputs": {"expected_tools": []},
        "memory_section": "reading_threads",
        "comment": "M2: known book query → memory should inject context",
    },
    {
        "inputs": {"question": "还有什么值得继续探索的？"},
        "outputs": {"expected_tools": []},
        "memory_section": "open_questions",
        "comment": "M3: open-ended → memory should guide",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Auto-memory cases — background daily-note extraction
# ═══════════════════════════════════════════════════════════════════════════════

AUTO_MEMORY_CASES: list[dict] = [
    {
        "inputs": {
            "transcript": [
                {"role": "user", "content": "三体里关于黑暗森林法则的核心论述是什么？我划过相关的段落"},
                {"role": "assistant", "content": "已找到你在《三体》中关于黑暗森林法则的划线，核心论述是：宇宙就是一座黑暗森林，每个文明都是带枪的猎人..."},
            ]
        },
        "outputs": {
            "expected_keywords": ["三体", "黑暗森林", "文明"],
        },
        "comment": "B1: extract reading activity about 三体",
    },
    {
        "inputs": {
            "transcript": [
                {"role": "user", "content": "三体里那三句话：给岁月以文明、黑暗森林、弱小无知不是障碍，帮我整理一下它们之间的逻辑关系"},
                {"role": "assistant", "content": "这三句话分别对应三种宇宙观：文明意义、博弈策略、生存态度..."},
            ]
        },
        "outputs": {
            "expected_keywords": ["三体", "宇宙观", "三句话"],
        },
        "comment": "B2: synthesis discussion → extract",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Dream cases — memory consolidation
# ═══════════════════════════════════════════════════════════════════════════════

DREAM_CASES: list[dict] = [
    {
        "inputs": {
            "current_memory": "# MEMORY\n\n尚无长期记忆。\n",
            "daily_notes": "## 2026-01-01\n- 分析了《三体》的划线笔记\n- 关注点：黑暗森林法则、文明存亡\n",
        },
        "outputs": {
            "expected_keywords": ["三体", "黑暗森林", "科幻"],
        },
        "comment": "DR1: single-book consolidation",
    },
    {
        "inputs": {
            "current_memory": "# MEMORY\n\n- 已分析过《三体》划线\n",
            "daily_notes": "## 2026-01-02\n- 继续分析《三体》\n- 新关注：傲慢与生存的关系\n",
        },
        "outputs": {
            "expected_keywords": ["三体", "傲慢", "生存"],
        },
        "comment": "DR2: incremental consolidation",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Multi-turn cases — conversation threading
# ═══════════════════════════════════════════════════════════════════════════════

MULTI_TURN_CASES: list[dict] = [
    {
        "inputs": {"question": "帮我查一下三体"},
        "outputs": {"expected_tools": ["resolve_weread_book"]},
        "comment": "T1-turn1: resolve book",
        "thread_group": "threebody-deep",
    },
    {
        "inputs": {"question": "这本书里关于文明我划了什么段落"},
        "outputs": {"expected_tools": [
            "search_weread_notes"
        ]},
        "comment": "T1-turn2: follow-up → should reuse current_book context",
        "thread_group": "threebody-deep",
    },
    {
        "inputs": {"question": "那黑暗森林那句原文帮我展示一下"},
        "outputs": {"expected_tools": [
            "search_weread_notes", "present_weread_book_document"
        ]},
        "comment": "T1-turn3: progressive deep-dive on same book",
        "thread_group": "threebody-deep",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 9. Stream cases — SSE streaming events
# ═══════════════════════════════════════════════════════════════════════════════

STREAM_CASES: list[dict] = [
    {
        "inputs": {"question": "三体有多少条划线"},
        "outputs": {"expected_stream_types": ["chunk", "done"]},
        "comment": "S1: simple stream",
    },
    {
        "inputs": {"question": "帮我展示三体的划线"},
        "outputs": {"expected_stream_types": ["chunk", "tool_call", "artifact", "done"]},
        "comment": "S2: artifact-bearing stream",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 10. Robustness cases — edge cases and error handling
# ═══════════════════════════════════════════════════════════════════════════════

ROBUSTNESS_CASES: list[dict] = [
    {
        "inputs": {"question": ""},
        "outputs": {"expected_tools": []},
        "comment": "RB1: empty input → no crash",
    },
    {
        "inputs": {"question": "   "},
        "outputs": {"expected_tools": []},
        "comment": "RB2: whitespace only → no crash",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# All cases merged
# ═══════════════════════════════════════════════════════════════════════════════

ALL_CASES: list[dict] = (
    DECISION_CASES
    + RETRIEVAL_CASES
    + ANALYST_CASES
    + CURRENT_BOOK_CASES
    + MEMORY_INJECTION_CASES
    + AUTO_MEMORY_CASES
    + DREAM_CASES
    + MULTI_TURN_CASES
    + STREAM_CASES
    + ROBUSTNESS_CASES
)
