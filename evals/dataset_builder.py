#!/usr/bin/env python3
"""Interactive LangSmith dataset builder for the Deep Agent.

Usage:
    uv run python -m evals.dataset_builder
    uv run python -m evals.dataset_builder --dataset my-dataset

Supports:
    - WeRead tool-calling cases (with mock HTTP + auth)
    - Pre-populated memory workspace for memory injection cases
    - Auto-memory & dream consolidation evaluation cases
    - Multi-turn cases with shared thread (thread_group)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

# ── NO_PROXY IPv6 fix ────────────────────────────────────────────────────────
for _key in ("NO_PROXY", "no_proxy"):
    _val = os.environ.get(_key, "")
    if _val:
        os.environ[_key] = ",".join(v for v in _val.split(",") if not v.strip().startswith("["))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.runtimes.deep_agent.client import DeepAgentClient
from app.runtimes.deep_agent.memory_subagents import make_auto_memory_agent, make_dream_agent
from tests.factories.agent import make_test_agent_client


# ═══════════════════════════════════════════════════════════════════════════════
# Mock setup
# ═══════════════════════════════════════════════════════════════════════════════


def _setup_mock_weread() -> None:
    from tests.mocks.weread_transport import make_weread_mocked_client

    import app.features.weread.api_client as weread_api

    async def _patched(self):
        if self.initialized:
            return
        self.cookie = "mock-cookie"
        self.client = make_weread_mocked_client()
        self.initialized = True

    weread_api.WeReadApi._ensure_initialized = _patched

    async def _patched_chapter_info(self, book_id: str) -> dict:
        _ = (self, book_id)
        return {}

    weread_api.WeReadApi.get_chapter_info = _patched_chapter_info

    from app.features.weread.services.auth import WeReadAuthService
    from app.models.weread_binding import WeReadBinding, WeReadBindingSource, WeReadBindingStatus

    async def _fake_auth(self, user_id: int) -> str:
        _ = (self, user_id)
        return "fake-cookie"

    async def _fake_get_binding(self, user_id: int) -> WeReadBinding | None:
        _ = (self, user_id)
        return WeReadBinding(
            user_id=user_id,
            encrypted_cookie="fake-encrypted",
            source=WeReadBindingSource.QR,
            status=WeReadBindingStatus.ACTIVE,
        )

    WeReadAuthService.require_active_cookie = _fake_auth
    WeReadAuthService.get_binding = _fake_get_binding

    async def _noop_mark_expired(self, user_id: int, reason: str = "") -> None:
        pass

    async def _noop_mark_valid(self, user_id: int) -> None:
        pass

    WeReadAuthService.mark_expired = _noop_mark_expired
    WeReadAuthService.mark_cookie_valid = _noop_mark_valid

    import app.features.weread.services.browser_login as browser_mod
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    async def _fake_start_qr(self, user_id: int):
        fake = type("FakeSession", (), {
            "id": str(_uuid.uuid4()),
            "user_id": user_id,
            "status": "qr_ready",
            "qr_image_base64": "fake-base64-qr",
            "expires_at": _dt.now(_tz.utc),
            "created_at": _dt.now(_tz.utc),
        })()
        return fake

    async def _fake_wait_qr(self, user_id: int, session_id: str):
        return await _fake_start_qr(self, user_id)

    browser_mod.weread_browser_service.start_qr_login_session = _fake_start_qr
    browser_mod.weread_browser_service.wait_for_qr_ready = _fake_wait_qr


# ═══════════════════════════════════════════════════════════════════════════════
# Memory workspace setup
# ═══════════════════════════════════════════════════════════════════════════════

_PREPOPULATED_MEMORY_MD = """# MEMORY

## Reader Profile
- 示例用户档案

## Reading Preferences
- 偏好结构化分析

## Active Reading Threads
- 暂无活跃阅读线程

## Knowledge Map
- 暂无知识图谱

## Output Preferences
- 使用中文回答，先结论后细节
"""

_PREPOPULATED_DAILY_NOTE = """## Reading Activity
- 今天查询了读书笔记

## Key Insights
- 暂无关键洞察
"""


async def _setup_memory_workspace() -> str:
    """Create a temporary memory workspace with pre-populated files and index.

    Returns the temp directory path (caller must clean up).
    """
    import shutil

    from app.features.markdown_memory.workspace import MarkdownMemoryWorkspace
    from app.features.markdown_memory.manager import markdown_memory_manager
    from app.features.markdown_memory.indexer import MarkdownMemoryIndexer

    temp_dir = tempfile.mkdtemp(prefix="md_memory_")
    workspace = MarkdownMemoryWorkspace(root=Path(temp_dir))
    user_dir = workspace.ensure_user_workspace(42)

    # Write MEMORY.md
    (user_dir / "MEMORY.md").write_text(_PREPOPULATED_MEMORY_MD, encoding="utf-8")

    # Write today's daily note
    memory_dir = user_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    (memory_dir / f"{today}.md").write_text(_PREPOPULATED_DAILY_NOTE, encoding="utf-8")

    # Patch manager workspace and rebuild index
    markdown_memory_manager.workspace = workspace
    indexer = MarkdownMemoryIndexer(workspace=workspace, db_path=user_dir / ".index" / "memory.db")
    await indexer.rebuild_user(42)

    print(f"  Memory workspace: {temp_dir}")
    return temp_dir


def _cleanup_memory_workspace(temp_dir: str) -> None:
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent case runners
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_tool_names(messages: list) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            n = tc.get("name", "")
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names


async def _run_chat_case(
    client: DeepAgentClient,
    case: dict,
    thread_id: str,
) -> dict[str, Any]:
    """Run a WeRead chat case and return structured result."""
    question = case["inputs"].get("question", "")
    result = await client.chat(question, thread_id, user_id=42)

    config = RunnableConfig(configurable={"thread_id": thread_id})
    agent = await client._ensure_agent(config)
    state = await agent.aget_state(config)
    messages = (state.values or {}) if state else {}
    messages = messages.get("messages", [])
    tool_names = _extract_tool_names(messages)

    answer = result.data.get("content", "") if result.type == "answer" else str(result.data)
    return {
        "question": question,
        "type": result.type,
        "answer": answer,
        "tool_names": tool_names,
    }


async def _run_auto_memory_case(case: dict) -> dict[str, Any]:
    """Run auto_memory_agent on a conversation transcript."""
    transcript = case["inputs"].get("transcript", [])
    lines = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in transcript]
    conversation = "\n".join(lines)

    agent = make_auto_memory_agent(user_id=42)
    result = await agent.ainvoke({
        "messages": [HumanMessage(content=(
            "Analyze this conversation and write durable memory to today's daily note.\n\n"
            f"{conversation}"
        ))],
    }, config=RunnableConfig(configurable={"user_id": 42}))
    messages = result.get("messages", [])
    last_msg = messages[-1] if messages else None
    content = getattr(last_msg, "content", str(last_msg)) if last_msg else ""
    return {"extracted_content": str(content), "question": transcript[0]["content"][:80] if transcript else ""}


async def _run_dream_case(case: dict) -> dict[str, Any]:
    """Run dream_agent to consolidate MEMORY.md from daily notes."""
    inputs = case["inputs"]
    current_memory = inputs.get("current_memory", "")
    daily_notes = inputs.get("daily_notes", [])

    # Build prompt
    prompt_parts = ["### Current MEMORY.md\n", current_memory]
    if daily_notes:
        prompt_parts.append("\n### Recent Daily Notes\n")
        for i, note in enumerate(daily_notes, 1):
            prompt_parts.append(f"--- Note {i} ---\n{note}")

    agent = make_dream_agent(user_id=42)
    result = await agent.ainvoke({
        "messages": [HumanMessage(content="\n".join(prompt_parts))],
    }, config=RunnableConfig(configurable={"user_id": 42}))
    messages = result.get("messages", [])
    last_msg = messages[-1] if messages else None
    content = getattr(last_msg, "content", str(last_msg)) if last_msg else ""
    return {"consolidated_memory": str(content), "question": f"Dream consolidation ({len(daily_notes)} daily notes)"}


# ═══════════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_result(result: dict, case: dict) -> list[str]:
    issues: list[str] = []
    expected_tools = case["outputs"].get("expected_tools", [])
    actual_tools = result["tool_names"]

    # Clarification blocked by middleware → skip tool matching.
    if result["type"] == "clarification":
        return issues

    # Capability required when tools expected → mock failure.
    if result["type"] == "capability_required" and expected_tools:
        issues.append(f"Got capability_required but expected tools {expected_tools}. Mock may be broken.")
        return issues

    if expected_tools:
        matched = [t for t in expected_tools if t in actual_tools]
        missing = [t for t in expected_tools if t not in actual_tools]
        extra = [t for t in actual_tools if t not in expected_tools and t != "ask_clarification"]

        if not missing and not extra:
            pass
        elif matched:
            issues.append(
                f"⚠ Partial match. Expected {expected_tools}, matched {matched}. "
                f"Missing: {missing or 'none'}. Extra: {extra or 'none'}."
            )
        else:
            issues.append(
                f"❌ Complete mismatch! Expected {expected_tools}, got {actual_tools}."
            )

    return issues


MAX_CONSECUTIVE_FAILURES = 3


def _is_memory_case(case: dict) -> bool:
    """Check if this is an auto-memory or dream case (not a chat case)."""
    return "transcript" in case["inputs"] or "current_memory" in case["inputs"]


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


async def _main(cases: list[dict], dataset_name: str, *, resume: bool = False) -> None:
    _setup_mock_weread()

    conn_cp = await aiosqlite.connect(":memory:")
    checkpointer = AsyncSqliteSaver(conn_cp)
    await checkpointer.setup()

    conn_st = await aiosqlite.connect(":memory:")
    store = AsyncSqliteStore(conn_st)
    await store.setup()

    client = make_test_agent_client(checkpointer=checkpointer, store=store)

    approved: list[dict] = []
    consecutive_failures = 0
    thread_groups: dict[str, str] = {}
    temp_dir: str | None = None

    def _approve(case_index: int, data: dict) -> None:
        """Append approved case with index and save progress."""
        data["_case_index"] = case_index
        approved.append(data)
        with open(progress_file, "w") as f:
            json.dump({"approved": approved}, f, ensure_ascii=False, indent=2)

    # Resume support: read previously approved case indices
    progress_file = f"{dataset_name}_progress.json"
    skipped_indices: set[int] = set()
    if resume:
        try:
            with open(progress_file) as f:
                prev = json.load(f)
            approved = prev.get("approved", [])
            skipped_indices = {a["_case_index"] for a in approved if "_case_index" in a}
            print(f"  Resuming: {len(skipped_indices)}/{len(cases)} cases already approved, {len(cases) - len(skipped_indices)} remaining")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    print(f"\n{'='*60}")
    print(f"  Dataset Builder — {len(cases)} cases")
    print(f"  Max consecutive failures before abort: {MAX_CONSECUTIVE_FAILURES}")
    if resume:
        print(f"  Resume mode: skipping {len(skipped_indices)} already-approved cases")
    print(f"{'='*60}\n")

    for i, case in enumerate(cases, 1):
        # Skip already-approved cases in resume mode
        if i in skipped_indices:
            print(f"[{i}/{len(cases)}] ⏭  Skipping (already approved)")
            continue
        # Lazy memory workspace: only set up when a memory injection case needs it.
        # A9 has memory_section=None → runs WITHOUT workspace (hallucination eval).
        ms = case.get("memory_section", "N/A")
        if ms is not None and ms != "N/A" and temp_dir is None:
            temp_dir = await _setup_memory_workspace()
        # --- Auto-memory cases ---
        if "transcript" in case["inputs"]:
            transcript_preview = case["inputs"]["transcript"][0]["content"][:60] if case["inputs"]["transcript"] else ""
            print(f"[{i}/{len(cases)}] auto-memory: {transcript_preview}...")
            try:
                result = await _run_auto_memory_case(case)
            except Exception as exc:
                print(f"  ✗ Runtime error: {exc}\n")
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n❌ ABORTING: {consecutive_failures} consecutive failures.")
                    break
                continue
            print(f"  Content: {result['extracted_content'][:200]}...")
            print(f"  ⚠ auto-memory cases are LLM-as-judge evaluated (not tool-matched here)")
            _approve(i, {
                "inputs": case["inputs"],
                "outputs": {
                    "expected_tools": [],
                    "reference_answer": result["extracted_content"][:500],
                    "actual_tools": [],
                    "response_type": "auto_memory_output",
                },
            })
            consecutive_failures = 0
            print()
            continue

        # --- Dream cases ---
        if "current_memory" in case["inputs"]:
            print(f"[{i}/{len(cases)}] dream consolidation ({len(case['inputs'].get('daily_notes', []))} daily notes)")
            try:
                result = await _run_dream_case(case)
            except Exception as exc:
                print(f"  ✗ Runtime error: {exc}\n")
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n❌ ABORTING: {consecutive_failures} consecutive failures.")
                    break
                continue
            print(f"  Output: {result['consolidated_memory'][:200]}...")
            print(f"  ⚠ dream cases are LLM-as-judge evaluated (not tool-matched here)")
            _approve(i, {
                "inputs": case["inputs"],
                "outputs": {
                    "expected_tools": [],
                    "reference_answer": result["consolidated_memory"][:1000],
                    "actual_tools": [],
                    "response_type": "dream_output",
                },
            })
            consecutive_failures = 0
            print()
            continue

        # --- Chat cases (WeRead + memory injection) ---
        question = case["inputs"].get("question", "")
        group = case.get("thread_group")

        if group:
            if group not in thread_groups:
                thread_groups[group] = f"eval-grp-{group[:20]}"
            thread_id = thread_groups[group]
        else:
            thread_id = f"eval-{i}"

        print(f"[{i}/{len(cases)}] Q: {question}" + (f" (group: {group})" if group else ""))

        if not question.strip():
            print("  ⏭  Skipping (empty question)\n")
            continue

        try:
            result = await _run_chat_case(client, case, thread_id=thread_id)
        except Exception as exc:
            print(f"  ✗ Runtime error: {exc}\n")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n❌ ABORTING: {consecutive_failures} consecutive failures.")
                break
            continue

        print(f"  Type: {result['type']}")
        print(f"  Tools: {result['tool_names'] or 'none'}")
        print(f"  Answer: {result['answer'][:200]}{'...' if len(result['answer']) > 200 else ''}")

        issues = _validate_result(result, case)
        expected_tools = case["outputs"].get("expected_tools", [])
        expected_subagent = case["outputs"].get("expected", {}).get("subagent")

        if issues:
            is_hard_failure = any("❌" in issue for issue in issues)
            for issue in issues:
                print(f"  {issue}")
            if is_hard_failure:
                consecutive_failures += 1
                print(f"  ⚠  Consecutive hard failures: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n❌ ABORTING: Too many consecutive failures.")
                    break
            else:
                consecutive_failures = 0
                _approve(i, {
                    "inputs": case["inputs"],
                    "outputs": {
                        **case["outputs"],
                        "reference_answer": result["answer"][:500],
                        "actual_tools": result["tool_names"],
                        "response_type": result["type"],
                        "_warning": issues,
                    },
                })
        else:
            consecutive_failures = 0
            if expected_subagent:
                print(f"  Subagent: {expected_subagent} ✓")
            elif expected_tools:
                print(f"  Expected tools: {expected_tools} ✓")
            else:
                print(f"  —")
            _approve(i, {
                "inputs": case["inputs"],
                "outputs": {
                    **case["outputs"],
                    "reference_answer": result["answer"][:500],
                    "actual_tools": result["tool_names"],
                    "response_type": result["type"],
                },
            })

        print()

    # Upload to LangSmith
    if approved:
        try:
            from langsmith import Client
            ls_client = Client()

            dataset = ls_client.create_dataset(
                dataset_name=dataset_name,
                description=f"Auto-generated dataset with {len(approved)}/{len(cases)} approved cases for Deep Agent evaluation.",
            )

            examples = []
            for item in approved:
                inp = {**item["inputs"]}
                out = item["outputs"]
                if "reference_answer" in out:
                    inp["_reference_answer"] = out["reference_answer"]
                if "actual_tools" in out:
                    inp["_actual_tools"] = out["actual_tools"]
                if "response_type" in out:
                    inp["_response_type"] = out["response_type"]
                examples.append({"inputs": inp, "outputs": out})
            ls_client.create_examples(
                inputs=[ex["inputs"] for ex in examples],
                outputs=[ex["outputs"] for ex in examples],
                dataset_id=dataset.id,
            )
            print(f"✓ Uploaded {len(approved)}/{len(cases)} examples to dataset '{dataset_name}' (id={dataset.id})")
        except Exception as exc:
            print(f"✗ Failed to upload to LangSmith: {exc}")
            import json as _json
            path = f"{dataset_name}_dataset.json"
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(approved, f, ensure_ascii=False, indent=2)
            print(f"✓ Saved {len(approved)} cases to {path}")
    else:
        print("⚠  No cases approved — nothing uploaded to LangSmith.")

    await conn_cp.close()
    await conn_st.close()
    if temp_dir is not None:
        _cleanup_memory_workspace(temp_dir)


def _load_cases_from_module(path: str) -> list[dict]:
    import importlib.util
    spec = importlib.util.spec_from_file_location("custom_cases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "ALL_CASES", getattr(module, "CASES", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a LangSmith evaluation dataset")
    parser.add_argument("--cases", help="Path to a Python module with ALL_CASES list")
    parser.add_argument("--dataset", default="deep-agent-eval", help="LangSmith dataset name")
    parser.add_argument("--resume", action="store_true", help="Skip already-approved cases from a previous run")
    args = parser.parse_args()

    if args.cases:
        cases = _load_cases_from_module(args.cases)
    else:
        try:
            from evals.dataset_cases import ALL_CASES
            cases = ALL_CASES
        except ImportError:
            print(
                "ERROR: No case definitions found.\n"
                "  Create evals/dataset_cases.py with an ALL_CASES list,\n"
                "  or pass --cases /path/to/your/cases.py",
                file=sys.stderr,
            )
            sys.exit(1)

    if not cases:
        print("No cases found.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_main(cases, args.dataset, resume=args.resume))


if __name__ == "__main__":
    main()
