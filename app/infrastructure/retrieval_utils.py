"""Shared hybrid retrieval utility functions."""

import math


def bm25_rank_to_score(rank: float) -> float:
    """Map SQLite BM25 rank (lower is better) to a positive score."""
    return 1.0 / (1.0 + max(0.0, abs(rank)))


def hybrid_candidate_limit(top_k: int, multiplier: int) -> int:
    """Return the candidate pool size for hybrid search fusion."""
    return min(top_k * multiplier, 200)


def normalize_hybrid_weights(text_weight: float, vector_weight: float) -> tuple[float, float]:
    """Normalize text and vector weights so they sum to 1.0."""
    text = max(0.0, text_weight)
    vector = max(0.0, vector_weight)
    total = text + vector
    if total <= 0:
        return 0.5, 0.5
    return text / total, vector / total


def vector_distance_to_score(distance: float) -> float:
    """Map vector L2 distance to a similarity score in [0, 1]."""
    return max(0.0, 1.0 / (1.0 + max(0.0, distance)))
