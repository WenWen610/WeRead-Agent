"""Subagent definitions for the Deep Agent reading assistant."""

from typing import Any

from app.runtimes.deep_agent.prompts import (
    WEREAD_NOTES_ANALYST_PROMPT,
)
from app.runtimes.deep_agent.skills import (
    weread_notes_analysis_skills,
)
from app.runtimes.deep_agent.tools import build_weread_analysis_tools


def build_deep_agent_subagents() -> list[dict[str, Any]]:
    """Return custom subagents with isolated skill sets."""
    return [
        {
            "name": "weread_notes_analyst",
            "description": (
                "Use for one-book broad or full-document WeRead note analysis, such as all highlights, "
                "all thoughts, overall concerns, or large-section synthesis."
            ),
            "system_prompt": WEREAD_NOTES_ANALYST_PROMPT,
            "tools": build_weread_analysis_tools(),
            "skills": weread_notes_analysis_skills(),
        },
    ]
