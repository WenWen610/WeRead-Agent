"""Embedding service for local note retrieval."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

# ── NO_PROXY IPv6 workaround ──────────────────────────────────────────────────
for _key in ("NO_PROXY", "no_proxy"):
    _val = os.environ.get(_key, "")
    if _val:
        os.environ[_key] = ",".join(v for v in _val.split(",") if not v.strip().startswith("["))

from openai import (
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)
retry_logger = logging.getLogger(__name__)
_OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai", "openai_compatible", "docker_model_runner"})


class NoteEmbeddingService:
    """Generate embeddings for local note chunks and queries."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    @staticmethod
    def is_enabled() -> bool:
        """Return whether note embeddings are enabled."""
        return settings.NOTE_EMBEDDING_ENABLED

    @staticmethod
    def get_model_name() -> str:
        """Return the configured note embedding model name."""
        return settings.NOTE_EMBEDDING_MODEL

    @staticmethod
    def get_dimensions() -> int:
        """Return the configured note embedding dimensions."""
        return settings.NOTE_EMBEDDING_DIMENSIONS

    def _get_client(self) -> AsyncOpenAI | None:
        if not self.is_enabled():
            return None

        provider = settings.NOTE_EMBEDDING_PROVIDER
        if provider not in _OPENAI_COMPATIBLE_PROVIDERS:
            logger.warning("note_embedding_provider_unsupported", provider=provider)
            return None

        if not settings.NOTE_EMBEDDING_API_KEY:
            logger.warning("note_embedding_api_key_missing")
            return None

        if self._client is None:
            client_kwargs: dict[str, str] = {"api_key": settings.NOTE_EMBEDDING_API_KEY}
            if settings.NOTE_EMBEDDING_BASE_URL:
                client_kwargs["base_url"] = settings.NOTE_EMBEDDING_BASE_URL
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError, OpenAIError)),
        before_sleep=before_sleep_log(retry_logger, logging.WARNING),
        reraise=True,
    )
    async def _create_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        client = self._get_client()
        if client is None:
            return []

        kwargs: dict[str, object] = {
            "model": self.get_model_name(),
            "input": list(texts),
        }
        dimensions = self.get_dimensions()
        if dimensions > 0:
            kwargs["dimensions"] = dimensions

        response = await client.embeddings.create(**kwargs)
        return [list(item.embedding) for item in response.data]

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts, or return an empty list when disabled."""
        if not texts:
            return []

        if not self.is_enabled():
            return []

        try:
            embeddings = await self._create_embeddings(texts)
        except Exception:
            logger.exception(
                "note_embedding_generation_failed",
                model=self.get_model_name(),
                text_count=len(texts),
            )
            return []

        if len(embeddings) != len(texts):
            logger.warning(
                "note_embedding_count_mismatch",
                expected_count=len(texts),
                actual_count=len(embeddings),
            )
            return []

        logger.info(
            "note_embeddings_generated",
            model=self.get_model_name(),
            dimensions=self.get_dimensions(),
            text_count=len(texts),
        )
        return embeddings


note_embedding_service = NoteEmbeddingService()
