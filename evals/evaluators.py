"""LangSmith evaluator functions for the Deep Agent.

Each evaluator receives a ``run`` (the agent's output) and an ``example``
(the dataset row with ``inputs`` and optional ``outputs``).  It returns a
dict with at least ``{"key": str, "score": float}``.

Evaluators:
    1.  hallucination      —  LLM-as-judge, scale 0-1 (lower = less hallucination)
    2.  helpfulness        —  LLM-as-judge, scale 0-1 (higher = more helpful)
    3.  relevancy          —  LLM-as-judge, scale 0-1 (higher = more relevant)
    4.  conciseness        —  LLM-as-judge, scale 0-1 (higher = more concise)
    5.  toxicity           —  LLM-as-judge, scale 0-1 (lower = less toxic)
    6.  tool_calling_accuracy — custom, checks expected vs actual tool names
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from evals.schemas import ScoreSchema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

METRICS_DIR = Path(__file__).resolve().parent / "metrics" / "prompts"


def _load_prompt(name: str) -> str:
    path = METRICS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Metric prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def _get_eval_llm():
    """Return a ChatOpenAI instance for running LLM-as-judge evaluations."""
    import importlib

    llm_module = importlib.import_module("app.infrastructure.llm")
    return llm_module.llm_service.get_llm()


def _ask_judge(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Send a prompt to the judge LLM and parse score + reasoning."""
    llm = _get_eval_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    text = response.content if hasattr(response, "content") else str(response)
    if isinstance(text, list):
        text = "".join(str(part) for part in text)

    score_match = re.search(r"\*\*Score\*\*:\s*([\d.]+)", text)
    score = float(score_match.group(1)) if score_match else 0.5

    reasoning_match = re.search(r"\*\*Reasoning\*\*:\s*(.+)", text, re.DOTALL)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else text[:500]

    return {"score": score, "reasoning": reasoning}


def _extract_agent_answer(run: dict) -> str:
    """Best-effort extraction of the agent's final text answer from a run."""
    outputs = run.outputs if hasattr(run, "outputs") else run.get("outputs", {})
    if isinstance(outputs, dict):
        for key in ("output", "content", "answer", "text"):
            val = outputs.get(key)
            if isinstance(val, str) and val.strip():
                return val
    if isinstance(outputs, str):
        return outputs
    return json.dumps(outputs, ensure_ascii=False)


def _extract_agent_input(example: dict) -> str:
    """Best-effort extraction of the user's question from a dataset example."""
    inputs = example.inputs if hasattr(example, "inputs") else example.get("inputs", {})
    if isinstance(inputs, dict):
        for key in ("question", "input", "message", "query"):
            val = inputs.get(key)
            if isinstance(val, str) and val.strip():
                return val
    if isinstance(inputs, str):
        return inputs
    return json.dumps(inputs, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLM-as-judge evaluators
# ---------------------------------------------------------------------------


def evaluate_hallucination(run: dict, example: dict) -> dict:
    """Score 0-1: 1 = severe hallucination, 0 = factual."""
    system_prompt = _load_prompt("hallucination")
    user_prompt = (
        f"### Input\n{_extract_agent_input(example)}\n\n"
        f"### Output\n{_extract_agent_answer(run)}"
    )
    result = _ask_judge(system_prompt, user_prompt)
    return {"key": "hallucination", "score": result["score"], "comment": result["reasoning"]}


def evaluate_helpfulness(run: dict, example: dict) -> dict:
    """Score 0-1: 1 = very helpful, 0 = not helpful."""
    system_prompt = _load_prompt("helpfulness")
    user_prompt = (
        f"### Input\n{_extract_agent_input(example)}\n\n"
        f"### Output\n{_extract_agent_answer(run)}"
    )
    result = _ask_judge(system_prompt, user_prompt)
    return {"key": "helpfulness", "score": result["score"], "comment": result["reasoning"]}


def evaluate_relevancy(run: dict, example: dict) -> dict:
    """Score 0-1: 1 = perfectly relevant, 0 = completely off-topic."""
    system_prompt = _load_prompt("relevancy")
    user_prompt = (
        f"### Input\n{_extract_agent_input(example)}\n\n"
        f"### Output\n{_extract_agent_answer(run)}"
    )
    result = _ask_judge(system_prompt, user_prompt)
    return {"key": "relevancy", "score": result["score"], "comment": result["reasoning"]}


def evaluate_conciseness(run: dict, example: dict) -> dict:
    """Score 0-1: 1 = very concise, 0 = overly verbose."""
    system_prompt = _load_prompt("conciseness")
    user_prompt = (
        f"### Input\n{_extract_agent_input(example)}\n\n"
        f"### Output\n{_extract_agent_answer(run)}"
    )
    result = _ask_judge(system_prompt, user_prompt)
    return {"key": "conciseness", "score": result["score"], "comment": result["reasoning"]}


def evaluate_toxicity(run: dict, example: dict) -> dict:
    """Score 0-1: 1 = highly toxic, 0 = completely safe."""
    system_prompt = _load_prompt("toxicity")
    user_prompt = (
        f"### Input\n{_extract_agent_input(example)}\n\n"
        f"### Output\n{_extract_agent_answer(run)}"
    )
    result = _ask_judge(system_prompt, user_prompt)
    return {"key": "toxicity", "score": result["score"], "comment": result["reasoning"]}


# ---------------------------------------------------------------------------
# Custom evaluators
# ---------------------------------------------------------------------------


def evaluate_tool_calling_accuracy(run: dict, example: dict) -> dict:
    """Compare expected tool sequence (from dataset) against actual tool calls.

    Dataset ``outputs.expected_tools`` should be a list of tool names, e.g.::

        ["resolve_weread_book", "get_weread_book_reading_status"]

    Score: fraction of expected tools that appeared in the actual call sequence.
    """
    outputs = example.outputs if hasattr(example, "outputs") else example.get("outputs", {})
    expected = outputs.get("expected_tools", [])
    if not isinstance(expected, list):
        expected = []

    # Actual tools from run — try multiple paths.
    run_outputs = run.outputs if hasattr(run, "outputs") else run.get("outputs", {})
    actual = run_outputs.get("tool_names", run_outputs.get("tools", []))

    if not isinstance(actual, list):
        return {"key": "tool_calling_accuracy", "score": 0.0, "comment": "No tool call data available"}

    if not expected:
        return {"key": "tool_calling_accuracy", "score": 1.0, "comment": "No expected tools defined"}

    matched = sum(1 for name in expected if name in actual)
    score = matched / len(expected) if expected else 1.0
    return {
        "key": "tool_calling_accuracy",
        "score": score,
        "comment": f"Expected: {expected}, Actual: {actual} → {matched}/{len(expected)}",
    }


# ---------------------------------------------------------------------------
# Custom evaluators — subagent trajectory
# ---------------------------------------------------------------------------


def _get_all_child_runs(run) -> list:
    """Recursively collect all child_runs from a LangSmith Run object."""
    results: list = []
    children = getattr(run, "child_runs", None)
    if not children:
        return results
    for child in children:
        results.append(child)
        results.extend(_get_all_child_runs(child))
    return results


def evaluate_subagent_trajectory(run, example) -> dict:
    """Walk LangSmith child_runs to verify subagent invocation and tool trajectory.

    Dataset ``outputs.expected`` should contain::

        {
            "subagent": "weread-retrieval-agent",
            "required_tools": ["resolve_weread_book", "search_weread_notes"],
        }
    """
    outputs = example.outputs if hasattr(example, "outputs") else example.get("outputs", {})
    expected = outputs.get("expected", {})
    if not isinstance(expected, dict):
        expected = {}
    subagent_name = expected.get("subagent")
    if not subagent_name:
        return {"key": "subagent_trajectory", "score": 1.0, "comment": "not a subagent case"}

    required_tools = expected.get("required_tools", [])
    if not isinstance(required_tools, list):
        required_tools = []

    children = _get_all_child_runs(run)

    used_subagents: set[str] = set()
    all_tool_names: list[str] = []

    for ch in children:
        extra = getattr(ch, "extra", None) or {}
        metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
        agent = metadata.get("lc_agent_name", "")
        if agent:
            used_subagents.add(agent)
        if getattr(ch, "run_type", "") == "tool":
            tool_name = getattr(ch, "name", "")
            if tool_name:
                all_tool_names.append(tool_name)

    subagent_ok = subagent_name in used_subagents
    matched = sum(1 for t in required_tools if t in all_tool_names)
    total = max(len(required_tools), 1)
    score = (int(subagent_ok) + matched / total) / 2

    return {
        "key": "subagent_trajectory",
        "score": round(score, 2),
        "comment": (
            f"subagent_found={subagent_ok}, "
            f"tools_matched={matched}/{total}, "
            f"used_subagents={sorted(used_subagents)}"
        ),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Default evaluators suitable for most reading-assistant evals.
DEFAULT_EVALUATORS = [
    evaluate_hallucination,
    evaluate_helpfulness,
    evaluate_relevancy,
    evaluate_conciseness,
    evaluate_tool_calling_accuracy,
    evaluate_subagent_trajectory,
]

ALL_EVALUATORS: dict[str, Any] = {
    "hallucination": evaluate_hallucination,
    "helpfulness": evaluate_helpfulness,
    "relevancy": evaluate_relevancy,
    "conciseness": evaluate_conciseness,
    "toxicity": evaluate_toxicity,
    "tool_calling_accuracy": evaluate_tool_calling_accuracy,
    "subagent_trajectory": evaluate_subagent_trajectory,
}
