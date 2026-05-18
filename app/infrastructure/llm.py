"""LLM service for managing LLM calls with retries and fallback mechanisms."""

import logging
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIError,
    APITimeoutError,
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

from app.infrastructure.config import (
    Environment,
    settings,
)
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)
retry_logger = logging.getLogger(__name__)

SUPPORTED_LLM_PROVIDERS = {"openai", "deepseek"}


def _resolve_provider() -> str:
    """Resolve and validate configured LLM provider."""
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        logger.warning(
            "invalid_llm_provider_falling_back_to_openai",
            configured_provider=provider,
            supported_providers=sorted(SUPPORTED_LLM_PROVIDERS),
        )
        return "openai"
    return provider


def _provider_client_kwargs(provider: str) -> Dict[str, Any]:
    """Build client kwargs for ChatOpenAI based on provider."""
    if provider == "deepseek":
        api_key = settings.DEEPSEEK_API_KEY
        base_url = settings.DEEPSEEK_API_BASE
    else:
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_BASE_URL

    if not api_key:
        logger.warning("llm_api_key_missing", provider=provider)

    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    return client_kwargs


def _provider_model_definitions(provider: str) -> List[Dict[str, Any]]:
    """Return model definitions for a provider."""
    if provider == "deepseek":
        return [
            {
                "name": "deepseek-chat",
                "kwargs": {
                    "temperature": settings.DEFAULT_LLM_TEMPERATURE,
                    "max_tokens": settings.REPLY_MAX_TOKENS,
                    "top_p": 0.9 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
                },
            },
            {
                "name": "deepseek-reasoner",
                "kwargs": {
                    "max_tokens": settings.REPLY_MAX_TOKENS,
                },
            },
        ]

    return [
        {
            "name": "gpt-5-mini",
            "kwargs": {
                "max_tokens": settings.REPLY_MAX_TOKENS,
                "reasoning": {"effort": "low"},
            },
        },
        {
            "name": "gpt-5",
            "kwargs": {
                "max_tokens": settings.REPLY_MAX_TOKENS,
                "reasoning": {"effort": "medium"},
            },
        },
        {
            "name": "gpt-5-nano",
            "kwargs": {
                "max_tokens": settings.REPLY_MAX_TOKENS,
                "reasoning": {"effort": "minimal"},
            },
        },
        {
            "name": "gpt-4o",
            "kwargs": {
                "temperature": settings.DEFAULT_LLM_TEMPERATURE,
                "max_tokens": settings.REPLY_MAX_TOKENS,
                "top_p": 0.95 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
                "presence_penalty": 0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
                "frequency_penalty": 0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
            },
        },
        {
            "name": "gpt-4o-mini",
            "kwargs": {
                "temperature": settings.DEFAULT_LLM_TEMPERATURE,
                "max_tokens": settings.REPLY_MAX_TOKENS,
                "top_p": 0.9 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
            },
        },
    ]


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances."""

    LLMS: List[Dict[str, Any]] = []
    PROVIDER: str = "openai"

    @classmethod
    def refresh(cls) -> None:
        """Rebuild model registry from current settings."""
        provider = _resolve_provider()
        client_kwargs = _provider_client_kwargs(provider)
        model_definitions = _provider_model_definitions(provider)

        cls.LLMS = [
            {"name": entry["name"], "llm": ChatOpenAI(model=entry["name"], **client_kwargs, **entry["kwargs"])}
            for entry in model_definitions
        ]
        cls.PROVIDER = provider

        logger.info(
            "llm_registry_refreshed",
            provider=provider,
            model_count=len(cls.LLMS),
            model_names=[entry["name"] for entry in cls.LLMS],
        )

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides."""
        if not cls.LLMS:
            cls.refresh()

        model_entry = next((entry for entry in cls.LLMS if entry["name"] == model_name), None)
        if not model_entry:
            available_models = [entry["name"] for entry in cls.LLMS]
            raise ValueError(
                f"model '{model_name}' not found in registry. available models: {', '.join(available_models)}"
            )

        if kwargs:
            logger.debug("creating_llm_with_custom_args", model_name=model_name, custom_args=list(kwargs.keys()))
            return ChatOpenAI(model=model_name, **_provider_client_kwargs(cls.PROVIDER), **kwargs)

        logger.debug("using_default_llm_instance", model_name=model_name)
        return model_entry["llm"]

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Get all registered LLM names in order."""
        if not cls.LLMS:
            cls.refresh()
        return [entry["name"] for entry in cls.LLMS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """Get model entry at specific index."""
        if not cls.LLMS:
            cls.refresh()
        if not cls.LLMS:
            raise ValueError("llm registry is empty")
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0]


class LLMService:
    """Service for managing LLM calls with retries and circular fallback."""

    def __init__(self):
        """Initialize the LLM service."""
        self._llm: Optional[BaseChatModel] = None
        self._current_model_index: int = 0

        LLMRegistry.refresh()
        all_names = LLMRegistry.get_all_names()
        if not all_names:
            raise RuntimeError("llm registry initialization failed")

        try:
            self._current_model_index = all_names.index(settings.DEFAULT_LLM_MODEL)
            self._llm = LLMRegistry.get(settings.DEFAULT_LLM_MODEL)
            logger.info(
                "llm_service_initialized",
                default_model=settings.DEFAULT_LLM_MODEL,
                model_index=self._current_model_index,
                total_models=len(all_names),
                provider=LLMRegistry.PROVIDER,
                environment=settings.ENVIRONMENT.value,
            )
        except (ValueError, Exception) as e:
            self._current_model_index = 0
            self._llm = LLMRegistry.LLMS[0]["llm"]
            logger.warning(
                "default_model_not_found_using_first",
                requested=settings.DEFAULT_LLM_MODEL,
                using=all_names[0],
                provider=LLMRegistry.PROVIDER,
                error=str(e),
            )

    def _get_next_model_index(self) -> int:
        """Get the next model index in circular fashion."""
        total_models = len(LLMRegistry.LLMS)
        if total_models == 0:
            raise RuntimeError("no models available in llm registry")
        return (self._current_model_index + 1) % total_models

    def _switch_to_next_model(self) -> bool:
        """Switch to the next model in the registry (circular)."""
        try:
            next_index = self._get_next_model_index()
            next_model_entry = LLMRegistry.get_model_at_index(next_index)

            logger.warning(
                "switching_to_next_model",
                from_index=self._current_model_index,
                to_index=next_index,
                to_model=next_model_entry["name"],
            )

            self._current_model_index = next_index
            self._llm = next_model_entry["llm"]
            logger.info("model_switched", new_model=next_model_entry["name"], new_index=next_index)
            return True
        except Exception as e:
            logger.error("model_switch_failed", error=str(e))
            return False

    @retry(
        stop=stop_after_attempt(settings.MAX_LLM_CALL_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
        before_sleep=before_sleep_log(retry_logger, logging.WARNING),
        reraise=True,
    )
    async def _call_llm_with_retry(
        self,
        messages: List[BaseMessage],
        llm: Any | None = None,
    ) -> BaseMessage:
        """Call the LLM with automatic retry logic."""
        llm_to_use = llm or self._llm
        if not llm_to_use:
            raise RuntimeError("llm not initialized")

        try:
            response = await llm_to_use.ainvoke(messages)
            logger.debug("llm_call_successful", message_count=len(messages))
            return response
        except (RateLimitError, APITimeoutError, APIError) as e:
            logger.warning(
                "llm_call_failed_retrying",
                error_type=type(e).__name__,
                error=str(e),
                exc_info=True,
            )
            raise
        except OpenAIError as e:
            logger.error(
                "llm_call_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    async def call(
        self,
        messages: List[BaseMessage],
        model_name: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        tool_choice: Optional[Any] = None,
        **model_kwargs,
    ) -> BaseMessage:
        """Call the LLM with the specified messages and circular fallback."""
        if model_name:
            try:
                self._llm = LLMRegistry.get(model_name, **model_kwargs)
                all_names = LLMRegistry.get_all_names()
                try:
                    self._current_model_index = all_names.index(model_name)
                except ValueError:
                    pass
                logger.info("using_requested_model", model_name=model_name, has_custom_kwargs=bool(model_kwargs))
            except ValueError as e:
                logger.error("requested_model_not_found", model_name=model_name, error=str(e))
                raise

        total_models = len(LLMRegistry.LLMS)
        if total_models == 0:
            raise RuntimeError("no models available in llm registry")

        models_tried = 0
        starting_index = self._current_model_index
        last_error = None

        while models_tried < total_models:
            try:
                llm_for_call = self._llm
                if not llm_for_call:
                    raise RuntimeError("llm not initialized")

                if tools:
                    if tool_choice is not None:
                        llm_for_call = llm_for_call.bind_tools(tools, tool_choice=tool_choice)
                    else:
                        llm_for_call = llm_for_call.bind_tools(tools)
                    logger.debug(
                        "tools_bound_for_llm_call",
                        tool_count=len(tools),
                        tool_choice=str(tool_choice) if tool_choice is not None else "auto",
                    )

                response = await self._call_llm_with_retry(messages, llm=llm_for_call)
                return response
            except OpenAIError as e:
                last_error = e
                models_tried += 1

                current_model_name = LLMRegistry.LLMS[self._current_model_index]["name"]
                logger.error(
                    "llm_call_failed_after_retries",
                    model=current_model_name,
                    models_tried=models_tried,
                    total_models=total_models,
                    error=str(e),
                )

                if models_tried >= total_models:
                    logger.error(
                        "all_models_failed",
                        models_tried=models_tried,
                        starting_model=LLMRegistry.LLMS[starting_index]["name"],
                    )
                    break

                if not self._switch_to_next_model():
                    logger.error("failed_to_switch_to_next_model")
                    break

        raise RuntimeError(
            f"failed to get response from llm after trying {models_tried} models. last error: {str(last_error)}"
        )

    def get_llm(self) -> Optional[BaseChatModel]:
        """Get the current LLM instance."""
        return self._llm

    def get_reply_max_output_tokens(self) -> int:
        """Return the real max output token cap used by normal chat requests."""
        return settings.REPLY_MAX_TOKENS

    def get_reply_reserve_tokens(self) -> int:
        """Return budget headroom reserved for normal chat replies."""
        return min(settings.REPLY_RESERVE_TOKENS, self.get_reply_max_output_tokens())

    def get_compact_max_output_tokens(self) -> int:
        """Return the real max output token cap that future compact calls should use."""
        return settings.COMPACT_MAX_TOKENS

    def get_compact_reserve_tokens(self) -> int:
        """Return budget headroom reserved for compact summary generation."""
        return min(settings.COMPACT_RESERVE_TOKENS, self.get_compact_max_output_tokens())

    def get_compact_llm(self, model_name: Optional[str] = None, **overrides) -> BaseChatModel:
        """Return an LLM configured for compact requests with a smaller real output cap."""
        target_model_name = model_name or getattr(self._llm, "model_name", settings.DEFAULT_LLM_MODEL)
        return LLMRegistry.get(
            str(target_model_name),
            max_tokens=self.get_compact_max_output_tokens(),
            **overrides,
        )

    def bind_tools(self, tools: List) -> "LLMService":
        """Bind tools to the current LLM."""
        if self._llm:
            self._llm = self._llm.bind_tools(tools)
            logger.debug("tools_bound_to_llm", tool_count=len(tools))
        return self


# Create global LLM service instance
llm_service = LLMService()
