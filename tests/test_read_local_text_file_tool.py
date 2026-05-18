from pathlib import Path

from app.infrastructure.config import settings
from app.runtimes.shared_tools.read_local_text_file_tool import read_local_text_file_tool


def test_read_local_text_file_reads_allowed_skill_file(tmp_path: Path, monkeypatch) -> None:
    skills_dir = tmp_path / "skills"
    local_knowledge_dir = tmp_path / "local_knowledge"
    skills_dir.mkdir(parents=True)
    local_knowledge_dir.mkdir(parents=True)
    target = skills_dir / "public" / "weread" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    monkeypatch.setattr(settings, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(settings, "LOCAL_KNOWLEDGE_DIR", local_knowledge_dir)

    result = read_local_text_file_tool.func(
        description="read weread skill",
        path=str(target.resolve()),
        start_line=2,
        end_line=3,
    )

    assert result == "line2\nline3"


def test_read_local_text_file_rejects_outside_allowed_roots(tmp_path: Path, monkeypatch) -> None:
    skills_dir = tmp_path / "skills"
    local_knowledge_dir = tmp_path / "local_knowledge"
    outside = tmp_path / "other" / "notes.txt"
    skills_dir.mkdir(parents=True)
    local_knowledge_dir.mkdir(parents=True)
    outside.parent.mkdir(parents=True)
    outside.write_text("forbidden", encoding="utf-8")

    monkeypatch.setattr(settings, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(settings, "LOCAL_KNOWLEDGE_DIR", local_knowledge_dir)

    result = read_local_text_file_tool.func(
        description="should fail",
        path=str(outside.resolve()),
    )

    assert result == "Error: path is outside the allowed local roots."
