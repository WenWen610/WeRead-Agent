"""Skill path helpers for Deep Agent subagents."""

from pathlib import Path

from app.infrastructure.config import settings


def _skill_path(*parts: str) -> str:
    return str((Path(settings.SKILLS_DIR) / "deep" / Path(*parts)).resolve())


def main_agent_skills(*, web_search_enabled: bool = False) -> list[str]:
    """Return skill directories for the main Deep Agent."""
    skills = [
        _skill_path("weread_light_tasks"),
        _skill_path("artifact_presentation"),
        _skill_path("connect_weread"),
        _skill_path("markdown_save"),
    ]
    if web_search_enabled:
        skills.append(_skill_path("web_search"))
    return skills


def weread_notes_analysis_skills() -> list[str]:
    """Return skill directories for the WeRead notes analyst subagent."""
    return [
        _skill_path("weread_notes_analysis"),
        _skill_path("weread_source_citation"),
    ]



