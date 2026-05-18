"""Token counting helpers with DeepSeek-aware local estimation."""

from __future__ import annotations

import json
import math
import re
from typing import Any

_CJK_RE = re.compile(r"[\u2E80-\u9FFF\uA000-\uA4FF\uAC00-\uD7AF\uF900-\uFAFF\U00020000-\U0002FA1F]")

_ASCII_TOKEN_RATIO = 0.3
_CJK_TOKEN_RATIO = 0.6
_OTHER_TOKEN_RATIO = 0.45
_GENERIC_CHARS_PER_TOKEN = 4


def is_deepseek_model_name(model_name: str | None) -> bool:
    """Return whether the model name belongs to a DeepSeek family."""
    lowered = (model_name or "").strip().lower()
    return lowered.startswith("deepseek-") or lowered.startswith("deepseek/")


def _rough_deepseek_token_count(text: str) -> int:
    if not text:
        return 0

    total = 0.0
    for ch in text:
        if ord(ch) < 128:
            total += _ASCII_TOKEN_RATIO
            continue
        if _CJK_RE.match(ch):
            total += _CJK_TOKEN_RATIO
            continue
        total += _OTHER_TOKEN_RATIO
    return max(1, math.ceil(total))


def _rough_generic_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _GENERIC_CHARS_PER_TOKEN))


def estimate_text_tokens(text: str, *, model_name: str | None = None) -> int:
    """Estimate tokens for raw text.

    DeepSeek models use a language-aware rough estimate aligned to the provider's
    documented English/Chinese ratios. Other models use a generic character-based
    fallback.
    """
    if not text:
        return 0

    if is_deepseek_model_name(model_name):
        return _rough_deepseek_token_count(text)

    return _rough_generic_token_count(text)


def estimate_payload_tokens(payload: Any, *, model_name: str | None = None) -> int:
    """Estimate tokens for a structured payload after JSON serialization."""
    return estimate_text_tokens(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        model_name=model_name,
    )
