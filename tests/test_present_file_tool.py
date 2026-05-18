import importlib.util
import sys
from pathlib import Path


def _load_present_file_tool_module():
    sys.modules.pop("app.runtimes.shared_tools.present_file_tool", None)

    module_name = "app.runtimes.shared_tools.present_file_tool"
    module_path = (
        Path(__file__).resolve().parents[1] / "app" / "runtimes" / "shared_tools" / "present_file_tool.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_build_present_files_update_normalizes_artifacts() -> None:
    present_file_tool_module = _load_present_file_tool_module()

    update = present_file_tool_module.build_present_files_update(
        [
            {
                "artifact_id": "weread:123:book-1:marks",
                "kind": "weread_markdown",
                "doc_id": "weread:123:book-1:marks",
                "book_id": "book-1",
                "book_title": "Sample Book",
                "source_type": "marks",
                "name": "marks.md",
                "generated_at": "2026-03-17T12:00:00Z",
            }
        ]
    )

    assert update == {
        "artifacts": [
            {
                "artifact_id": "weread:123:book-1:marks",
                "kind": "weread_markdown",
                "doc_id": "weread:123:book-1:marks",
                "book_id": "book-1",
                "book_title": "Sample Book",
                "source_type": "marks",
                "name": "marks.md",
                "generated_at": "2026-03-17T12:00:00Z",
            }
        ]
    }


def test_present_files_tool_returns_command_with_artifact_update() -> None:
    present_file_tool_module = _load_present_file_tool_module()

    artifact = present_file_tool_module.PresentableArtifact(
        artifact_id="weread:123:book-1:marks",
        kind="weread_markdown",
        doc_id="weread:123:book-1:marks",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        name="marks.md",
        generated_at="2026-03-17T12:00:00Z",
    )

    command = present_file_tool_module.present_files_tool.func(
        artifacts=[artifact],
        tool_call_id="tool-call-1",
        runtime=None,
    )

    assert command.update["artifacts"] == [
        {
            "artifact_id": "weread:123:book-1:marks",
            "kind": "weread_markdown",
            "doc_id": "weread:123:book-1:marks",
            "book_id": "book-1",
            "book_title": "Sample Book",
            "source_type": "marks",
            "name": "marks.md",
            "generated_at": "2026-03-17T12:00:00Z",
        }
    ]
    assert command.update["messages"][0].tool_call_id == "tool-call-1"
