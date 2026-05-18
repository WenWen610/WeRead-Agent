"""Lightweight ReAct subagent factory for background memory tasks.

These subagents are NOT registered with the main deepagent framework.
They are standalone LangGraph agents created on-the-fly for isolated
background memory operations (auto-memory, dream consolidation).
"""

from __future__ import annotations

from pydantic import BaseModel
from langchain.agents import create_agent

from app.features.markdown_memory.tools import (
    append_memory_daily_note,
    read_memory_note,
    search_user_memory,
    write_memory_note,
)
from app.infrastructure.llm import llm_service


class MemoryAgentContext(BaseModel):
    """Context passed to memory subagent tools via runtime.context."""
    user_id: int


def make_auto_memory_agent(user_id: int | None = None):
    """Create a lightweight ReAct agent for auto-memory extraction.

    The agent reads today's daily note and appends new durable memory.
    It has no access to MEMORY.md (reserved for dream consolidation).
    """
    return create_agent(
        model=llm_service.get_llm(),
        tools=[read_memory_note, append_memory_daily_note],
        system_prompt=(
            "You are the auto-memory writer, a background agent that extracts "
            "durable reading memory from conversations.\n\n"
            "Instructions:\n"
            "1. Read today's daily note (memory/YYYY-MM-DD.md) if it exists.\n"
            "2. Analyze the conversation transcript provided below.\n"
            "3. Write new durable memory to today's note using append_memory_daily_note.\n"
            "4. If content already exists, skip it (no duplicates).\n"
            "5. Focus on: reading activity, key insights, user preferences, progress.\n"
            "6. Ignore: transient chatter, tool outputs, unsupported guesses.\n\n"
            "Rules:\n"
            "- ONLY write to memory/YYYY-MM-DD.md.\n"
            "- Do NOT read or modify MEMORY.md.\n"
            "- Do not fabricate information.\n"
            "- If there is nothing durable to store, do not call append_memory_daily_note and reply exactly [SILENT].\n"
            "- Reply with a one-line summary of what was stored."
        ),
        context_schema=MemoryAgentContext,
    )


def make_dream_agent(user_id: int | None = None):
    """Create a lightweight ReAct agent for dream consolidation.

    The agent reads MEMORY.md and recent daily notes, then produces
    a consolidated MEMORY.md.
    """
    return create_agent(
        model=llm_service.get_llm(),
        tools=[read_memory_note, write_memory_note, search_user_memory],
        system_prompt=(
            "You are the dream consolidator, a background agent that optimizes "
            "long-term memory by consolidating daily notes into MEMORY.md.\n\n"
            "Instructions:\n"
            "1. Read MEMORY.md using read_memory_note.\n"
            "2. Read recent daily notes from memory/ directory.\n"
            "3. Consolidate: merge new durable info, deduplicate, replace outdated entries.\n"
            "4. Write the consolidated MEMORY.md using write_memory_note.\n"
            "5. Preserve the exact top-level section structure.\n"
            "6. Prune transient or no-longer-relevant entries.\n\n"
            "Rules:\n"
            "- ONLY modify MEMORY.md (not daily notes).\n"
            "- Backup of MEMORY.md is handled externally before you start.\n"
            "- Preserve all ## top-level section headers.\n"
            "- Do not fabricate information.\n"
            "- Reply with a summary of what was added, changed, or removed."
        ),
        context_schema=MemoryAgentContext,
    )
