#!/usr/bin/env python3
"""LangSmith evaluation runner for the Deep Agent.

Replaces the disabled ``evals.evaluator.Evaluator``.

Usage:
    uv run python -m evals.langsmith_eval --dataset deep-agent-eval
    uv run python -m evals.langsmith_eval --dataset deep-agent-eval --metrics hallucination,relevancy
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv(".env.development")

# ── NO_PROXY IPv6 fix (same as conftest.py) ───────────────────────────────────
for _key in ("NO_PROXY", "no_proxy"):
    _val = os.environ.get(_key, "")
    if _val:
        os.environ[_key] = ",".join(v for v in _val.split(",") if not v.strip().startswith("["))

from langsmith import aevaluate


# ── Agent target ──────────────────────────────────────────────────────────────


async def _run_agent_example(inputs: dict) -> dict:
    """Return the builder-captured answer for evaluator scoring.

    Does NOT re-run the agent — evaluators score the same answer
    that was validated by the dataset builder.
    """
    return {
        "output": inputs.get("_reference_answer", ""),
        "tool_names": inputs.get("_actual_tools", []),
        "response_type": inputs.get("_response_type", ""),
    }


# ── Evaluator loading ─────────────────────────────────────────────────────────


def _load_evaluators(metric_names: list[str] | None) -> list:
    from evals.evaluators import ALL_EVALUATORS, DEFAULT_EVALUATORS

    if not metric_names:
        return list(DEFAULT_EVALUATORS)

    result = []
    for name in metric_names:
        name = name.strip()
        if name in ALL_EVALUATORS:
            result.append(ALL_EVALUATORS[name])
        else:
            print(f"Warning: unknown metric '{name}', skipping. Available: {list(ALL_EVALUATORS)}")
    return result or list(DEFAULT_EVALUATORS)


# ── Main ──────────────────────────────────────────────────────────────────────


async def _main(args: argparse.Namespace) -> None:
    metric_names = [m.strip() for m in args.metrics.split(",")] if args.metrics else None
    evaluators = _load_evaluators(metric_names)
    print(f"Evaluators: {[e.__name__ for e in evaluators]}")
    print(f"Dataset: {args.dataset}")

    try:
        results = await aevaluate(
            _run_agent_example,
            data=args.dataset,
            evaluators=evaluators,
            max_concurrency=args.max_concurrency,
            experiment_prefix=args.experiment_prefix,
        )
        print(f"\nEvaluation complete. Results: {results}")
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
            print(f"\nHint: Dataset '{args.dataset}' does not exist in LangSmith.", file=sys.stderr)
            print("Run the dataset builder first:", file=sys.stderr)
            print("  uv run python -m evals.dataset_builder --dataset deep-agent-eval", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LangSmith evaluation on Deep Agent dataset")
    parser.add_argument("--dataset", default="deep-agent-eval", help="LangSmith dataset name")
    parser.add_argument(
        "--metrics",
        help="Comma-separated metric names. Available: hallucination, helpfulness, relevancy, conciseness, toxicity, tool_calling_accuracy",
    )
    parser.add_argument("--max-concurrency", type=int, default=1, help="Max concurrent evaluations")
    parser.add_argument("--experiment-prefix", default="deep-agent-eval", help="LangSmith experiment prefix")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
