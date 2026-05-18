"""Application configuration management.

This module handles environment-specific configuration loading, parsing, and management
for the application. It includes environment detection, .env file loading, and
configuration value parsing.
"""

import json
import os
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
)

from dotenv import load_dotenv


# Define environment types
class Environment(str, Enum):
    """Application environment types.

    Defines the possible environments the application can run in:
    development, staging, production, and test.
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


# Determine environment
def get_environment() -> Environment:
    """Get the current environment.

    Returns:
        Environment: The current environment (development, staging, production, or test)
    """
    match os.getenv("APP_ENV", "development").lower():
        case "production" | "prod":
            return Environment.PRODUCTION
        case "staging" | "stage":
            return Environment.STAGING
        case "test":
            return Environment.TEST
        case _:
            return Environment.DEVELOPMENT


# Load appropriate .env file based on environment
def load_env_file():
    """Load environment-specific .env file."""
    env = get_environment()
    print(f"Loading environment: {env}")
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    # Define env files in priority order
    env_files = [
        os.path.join(base_dir, f".env.{env.value}.local"),
        os.path.join(base_dir, f".env.{env.value}"),
        os.path.join(base_dir, ".env.local"),
        os.path.join(base_dir, ".env"),
    ]

    # Load the first env file that exists
    for env_file in env_files:
        if os.path.isfile(env_file):
            load_dotenv(dotenv_path=env_file)
            print(f"Loaded environment from {env_file}")
            return env_file

    # Fall back to default if no env file found
    return None


ENV_FILE = load_env_file()


# Parse list values from environment variables
def parse_list_from_env(env_key, default=None):
    """Parse a comma-separated list from an environment variable."""
    value = os.getenv(env_key)
    if not value:
        return default or []

    # Remove quotes if they exist
    value = value.strip("\"'")
    # Handle single value case
    if "," not in value:
        return [value]
    # Split comma-separated values
    return [item.strip() for item in value.split(",") if item.strip()]


# Parse dict of lists from environment variables with prefix
def parse_dict_of_lists_from_env(prefix, default_dict=None):
    """Parse dictionary of lists from environment variables with a common prefix."""
    result = default_dict or {}

    # Look for all env vars with the given prefix
    for key, value in os.environ.items():
        if key.startswith(prefix):
            endpoint = key[len(prefix) :].lower()  # Extract endpoint name
            # Parse the values for this endpoint
            if value:
                value = value.strip("\"'")
                if "," in value:
                    result[endpoint] = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    result[endpoint] = [value]

    return result


class Settings:
    """Application settings without using pydantic."""

    def __init__(self):
        """Initialize application settings from environment variables.

        Loads and sets all configuration values from environment variables,
        with appropriate defaults for each setting. Also applies
        environment-specific overrides based on the current environment.
        """
        # Set the environment
        self.ENVIRONMENT = get_environment()
        # Application Settings
        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "WeRead Agent")
        self.VERSION = os.getenv("VERSION", "1.0.0")
        self.DESCRIPTION = os.getenv(
            "DESCRIPTION", "A local-first AI reading assistant for WeRead notes and Markdown memory"
        )
        self.API_V1_STR = os.getenv("API_V1_STR", "/api/v1")
        self.DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "t", "yes")

        # CORS Settings
        self.ALLOWED_ORIGINS = parse_list_from_env("ALLOWED_ORIGINS", ["*"])

        # LLM configuration
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        self.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", os.getenv("OPENAI_API_BASE", ""))
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
        self.DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
        self.DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "gpt-5-mini")
        self.DEFAULT_LLM_TEMPERATURE = float(os.getenv("DEFAULT_LLM_TEMPERATURE", "0.2"))
        self.REPLY_MAX_TOKENS = int(os.getenv("REPLY_MAX_TOKENS", os.getenv("MAX_TOKENS", "2000")))
        # Backward-compatible alias for existing call sites that still read MAX_TOKENS.
        self.MAX_TOKENS = self.REPLY_MAX_TOKENS
        self.REPLY_RESERVE_TOKENS = int(os.getenv("REPLY_RESERVE_TOKENS", str(self.REPLY_MAX_TOKENS)))
        # Compact LLM output cap: 4096 tokens allows ~5000 chars of Chinese summary.
        # Must be larger than SUMMARY_HARD_CHAR_LIMIT / ~2.5 chars-per-token.
        default_compact_max_tokens = 4096
        self.COMPACT_MAX_TOKENS = int(os.getenv("COMPACT_MAX_TOKENS", str(default_compact_max_tokens)))
        self.COMPACT_RESERVE_TOKENS = int(os.getenv("COMPACT_RESERVE_TOKENS", str(self.COMPACT_MAX_TOKENS)))
        self.MAX_LLM_CALL_RETRIES = int(os.getenv("MAX_LLM_CALL_RETRIES", "3"))
        self.DEEP_AGENT_WEB_SEARCH_ENABLED = os.getenv("DEEP_AGENT_WEB_SEARCH_ENABLED", "false").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.DEEP_AGENT_RECURSION_LIMIT = int(os.getenv("DEEP_AGENT_RECURSION_LIMIT", "9999"))

        # Long-term memory configuration
        default_memory_provider = "deepseek" if self.LLM_PROVIDER == "deepseek" else "openai"
        raw_memory_provider = os.getenv("LONG_TERM_MEMORY_LLM_PROVIDER", "auto").lower()
        self.LONG_TERM_MEMORY_LLM_PROVIDER = (
            default_memory_provider if raw_memory_provider in {"", "auto"} else raw_memory_provider
        )
        default_memory_model = "deepseek-chat" if self.LONG_TERM_MEMORY_LLM_PROVIDER == "deepseek" else "gpt-5-nano"
        self.LONG_TERM_MEMORY_MODEL = os.getenv("LONG_TERM_MEMORY_MODEL", default_memory_model)
        self.LONG_TERM_MEMORY_EMBEDDER_MODEL = os.getenv("LONG_TERM_MEMORY_EMBEDDER_MODEL", "text-embedding-3-small")
        self.LONG_TERM_MEMORY_COLLECTION_NAME = os.getenv("LONG_TERM_MEMORY_COLLECTION_NAME", "longterm_memory")
        self.LONG_TERM_MEMORY_LLM_API_KEY = os.getenv(
            "LONG_TERM_MEMORY_LLM_API_KEY",
            self.DEEPSEEK_API_KEY if self.LONG_TERM_MEMORY_LLM_PROVIDER == "deepseek" else self.OPENAI_API_KEY,
        )
        self.LONG_TERM_MEMORY_LLM_BASE_URL = os.getenv(
            "LONG_TERM_MEMORY_LLM_BASE_URL",
            self.DEEPSEEK_API_BASE if self.LONG_TERM_MEMORY_LLM_PROVIDER == "deepseek" else self.OPENAI_BASE_URL,
        )

        self.LONG_TERM_MEMORY_EMBEDDER_PROVIDER = os.getenv("LONG_TERM_MEMORY_EMBEDDER_PROVIDER", "openai").lower()
        self.LONG_TERM_MEMORY_EMBEDDER_API_KEY = os.getenv("LONG_TERM_MEMORY_EMBEDDER_API_KEY", self.OPENAI_API_KEY)
        self.LONG_TERM_MEMORY_EMBEDDER_BASE_URL = os.getenv(
            "LONG_TERM_MEMORY_EMBEDDER_BASE_URL", self.OPENAI_BASE_URL
        )
        self.NOTE_EMBEDDING_ENABLED = os.getenv("NOTE_EMBEDDING_ENABLED", "false").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.NOTE_EMBEDDING_PROVIDER = os.getenv(
            "NOTE_EMBEDDING_PROVIDER",
            self.LONG_TERM_MEMORY_EMBEDDER_PROVIDER,
        ).lower()
        self.NOTE_EMBEDDING_MODEL = os.getenv(
            "NOTE_EMBEDDING_MODEL",
            self.LONG_TERM_MEMORY_EMBEDDER_MODEL,
        )
        self.NOTE_EMBEDDING_API_KEY = os.getenv(
            "NOTE_EMBEDDING_API_KEY",
            self.LONG_TERM_MEMORY_EMBEDDER_API_KEY,
        )
        self.NOTE_EMBEDDING_BASE_URL = os.getenv(
            "NOTE_EMBEDDING_BASE_URL",
            self.LONG_TERM_MEMORY_EMBEDDER_BASE_URL,
        )
        self.NOTE_EMBEDDING_DIMENSIONS = int(os.getenv("NOTE_EMBEDDING_DIMENSIONS", "1536"))
        self.NOTE_VECTOR_WEIGHT = float(os.getenv("NOTE_VECTOR_WEIGHT", "0.7"))
        self.NOTE_TEXT_WEIGHT = float(os.getenv("NOTE_TEXT_WEIGHT", "0.3"))
        self.NOTE_CANDIDATE_MULTIPLIER = int(os.getenv("NOTE_CANDIDATE_MULTIPLIER", "4"))
        self.LONG_TERM_MEMORY_SEARCH_TEXT_WEIGHT = float(
            os.getenv("LONG_TERM_MEMORY_SEARCH_TEXT_WEIGHT", str(self.NOTE_TEXT_WEIGHT))
        )
        self.LONG_TERM_MEMORY_SEARCH_VECTOR_WEIGHT = float(
            os.getenv("LONG_TERM_MEMORY_SEARCH_VECTOR_WEIGHT", str(self.NOTE_VECTOR_WEIGHT))
        )
        self.LONG_TERM_MEMORY_SEARCH_CANDIDATE_MULTIPLIER = int(
            os.getenv("LONG_TERM_MEMORY_SEARCH_CANDIDATE_MULTIPLIER", str(self.NOTE_CANDIDATE_MULTIPLIER))
        )
        self.LONG_TERM_MEMORY_SEARCH_MIN_SCORE = float(
            os.getenv("LONG_TERM_MEMORY_SEARCH_MIN_SCORE", "0.25")
        )
        self.LONG_TERM_MEMORY_SEARCH_TEMPORAL_DECAY_ENABLED = os.getenv(
            "LONG_TERM_MEMORY_SEARCH_TEMPORAL_DECAY_ENABLED", "false"
        ).lower() in ("true", "1", "t", "yes")
        self.LONG_TERM_MEMORY_SEARCH_TEMPORAL_HALF_LIFE_DAYS = float(
            os.getenv("LONG_TERM_MEMORY_SEARCH_TEMPORAL_HALF_LIFE_DAYS", "30")
        )
        self.LONG_TERM_MEMORY_SEARCH_TEMPORAL_DECAY_EXEMPT_KINDS = frozenset(
            parse_list_from_env(
                "LONG_TERM_MEMORY_SEARCH_TEMPORAL_DECAY_EXEMPT_KINDS",
                ["stable_user_preference"],
            )
        )
        self.LONG_TERM_MEMORY_SEARCH_MMR_ENABLED = os.getenv(
            "LONG_TERM_MEMORY_SEARCH_MMR_ENABLED", "false"
        ).lower() in ("true", "1", "t", "yes")
        self.LONG_TERM_MEMORY_SEARCH_MMR_LAMBDA = float(
            os.getenv("LONG_TERM_MEMORY_SEARCH_MMR_LAMBDA", "0.7")
        )
        self.LONG_TERM_MEMORY_SEARCH_MMR_POOL_MULTIPLIER = int(
            os.getenv("LONG_TERM_MEMORY_SEARCH_MMR_POOL_MULTIPLIER", "4")
        )
        self.LONG_TERM_MEMORY_SEARCH_LOG_HYBRID_BREAKDOWN = os.getenv(
            "LONG_TERM_MEMORY_SEARCH_LOG_HYBRID_BREAKDOWN", "false"
        ).lower() in ("true", "1", "t", "yes")
        self.WEREAD_INDEX_DEBOUNCE_MS = int(os.getenv("WEREAD_INDEX_DEBOUNCE_MS", "3000"))
        self.WEREAD_INDEX_QUERY_WAIT_MS = int(os.getenv("WEREAD_INDEX_QUERY_WAIT_MS", "500"))
        self.LONG_TERM_MEMORY_UPDATE_DEBOUNCE_MS = int(os.getenv("LONG_TERM_MEMORY_UPDATE_DEBOUNCE_MS", "3000"))
        self.LONG_TERM_MEMORY_MID_TERM_LIMIT = int(os.getenv("LONG_TERM_MEMORY_MID_TERM_LIMIT", "2"))
        self.LONG_TERM_MEMORY_LONG_TERM_LIMIT = int(os.getenv("LONG_TERM_MEMORY_LONG_TERM_LIMIT", "3"))
        self.LONG_TERM_MEMORY_MAX_INJECTION_CHARS = int(os.getenv("LONG_TERM_MEMORY_MAX_INJECTION_CHARS", "1200"))
        self.LONG_TERM_MEMORY_TOOL_MAX_CHARS = int(os.getenv("LONG_TERM_MEMORY_TOOL_MAX_CHARS", "6000"))
        self.LONG_TERM_MEMORY_AUTO_INJECT_MIN_QUERY_CHARS = int(
            os.getenv("LONG_TERM_MEMORY_AUTO_INJECT_MIN_QUERY_CHARS", "3")
        )
        self.LONG_TERM_MEMORY_AUTO_INJECT_MAX_CHARS = int(
            os.getenv("LONG_TERM_MEMORY_AUTO_INJECT_MAX_CHARS", "800")
        )
        self.LONG_TERM_MEMORY_AUTO_INJECT_MID_TERM_LIMIT = int(
            os.getenv("LONG_TERM_MEMORY_AUTO_INJECT_MID_TERM_LIMIT", "0")
        )
        self.LONG_TERM_MEMORY_AUTO_INJECT_LONG_TERM_LIMIT = int(
            os.getenv("LONG_TERM_MEMORY_AUTO_INJECT_LONG_TERM_LIMIT", "2")
        )
        self.LONG_TERM_MEMORY_AUTO_INJECT_SKIP_FILLERS = os.getenv(
            "LONG_TERM_MEMORY_AUTO_INJECT_SKIP_FILLERS", "true"
        ).lower() in ("true", "1", "t", "yes")
        self.LONG_TERM_MEMORY_MIN_ESTIMATED_TOKENS_FOR_EXTRACTION = int(
            os.getenv("LONG_TERM_MEMORY_MIN_ESTIMATED_TOKENS_FOR_EXTRACTION", "3000")
        )
        self.LONG_TERM_MEMORY_MIN_USER_TURNS_FOR_EXTRACTION = int(
            os.getenv("LONG_TERM_MEMORY_MIN_USER_TURNS_FOR_EXTRACTION", "4")
        )
        # Fallback threshold when the token estimate is small or unavailable.
        self.LONG_TERM_MEMORY_MIN_NEW_MESSAGES_FOR_EXTRACTION = int(
            os.getenv("LONG_TERM_MEMORY_MIN_NEW_MESSAGES_FOR_EXTRACTION", "12")
        )
        self.LONG_TERM_MEMORY_UPDATE_WAIT_BEFORE_COMPACTION_MS = int(
            os.getenv("LONG_TERM_MEMORY_UPDATE_WAIT_BEFORE_COMPACTION_MS", "1000")
        )
        self.STABLE_PROFILE_MAX_CHARS = int(os.getenv("STABLE_PROFILE_MAX_CHARS", "400"))
        # JWT Configuration
        self.JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
        self.JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "30"))
        self.WEREAD_CREDENTIAL_SECRET = os.getenv("WEREAD_CREDENTIAL_SECRET", self.JWT_SECRET_KEY)
        self.WEREAD_QR_LOGIN_URL = os.getenv("WEREAD_QR_LOGIN_URL", "https://weread.qq.com/")
        self.WEREAD_QR_LOGIN_TIMEOUT_SECONDS = int(os.getenv("WEREAD_QR_LOGIN_TIMEOUT_SECONDS", "180"))
        self.WEREAD_QR_POLL_INTERVAL_SECONDS = float(os.getenv("WEREAD_QR_POLL_INTERVAL_SECONDS", "2"))
        self.WEREAD_QR_HEADLESS = os.getenv("WEREAD_QR_HEADLESS", "true").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.WEREAD_BINDING_AUTO_VALIDATE_AFTER_HOURS = float(
            os.getenv("WEREAD_BINDING_AUTO_VALIDATE_AFTER_HOURS", "6"),
        )
        self.WEREAD_BINDING_VALIDATE_NETWORK_BACKOFF_SECONDS = int(
            os.getenv("WEREAD_BINDING_VALIDATE_NETWORK_BACKOFF_SECONDS", "3600"),
        )
        self.LOCAL_KNOWLEDGE_DIR = Path(
            os.getenv(
                "LOCAL_KNOWLEDGE_DIR",
                str(Path(__file__).resolve().parents[2] / "data" / "local_knowledge"),
            )
        )
        self.LOCAL_KNOWLEDGE_DB_PATH = self.LOCAL_KNOWLEDGE_DIR / os.getenv(
            "LOCAL_KNOWLEDGE_DB_NAME",
            "local_knowledge.db",
        )
        _checkpoint_sqlite_override = os.getenv("LANGGRAPH_CHECKPOINT_SQLITE_PATH", "").strip()
        if _checkpoint_sqlite_override:
            self.LANGGRAPH_CHECKPOINT_SQLITE_PATH = Path(
                _checkpoint_sqlite_override,
            ).expanduser()
        else:
            self.LANGGRAPH_CHECKPOINT_SQLITE_PATH = self.LOCAL_KNOWLEDGE_DIR / os.getenv(
                "LANGGRAPH_CHECKPOINT_DB_NAME",
                "langgraph_checkpoints.db",
            )
        _deep_agent_store_override = os.getenv("DEEP_AGENT_STORE_SQLITE_PATH", "").strip()
        if _deep_agent_store_override:
            self.DEEP_AGENT_STORE_SQLITE_PATH = Path(_deep_agent_store_override).expanduser()
        else:
            self.DEEP_AGENT_STORE_SQLITE_PATH = self.LOCAL_KNOWLEDGE_DIR / os.getenv(
                "DEEP_AGENT_STORE_DB_NAME",
                "deep_agent_store.db",
            )
        # Markdown-backed long-term memory for Deep Agent. Markdown files are the
        # source of truth; SQLite is only an index cache.
        self.MARKDOWN_MEMORY_ENABLED = os.getenv("MARKDOWN_MEMORY_ENABLED", "true").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.MARKDOWN_MEMORY_ROOT = Path(
            os.getenv(
                "MARKDOWN_MEMORY_ROOT",
                str(self.LOCAL_KNOWLEDGE_DIR / "memory_workspaces"),
            )
        ).expanduser()
        self.MARKDOWN_MEMORY_INDEX_DB_PATH = Path(
            os.getenv(
                "MARKDOWN_MEMORY_INDEX_DB_PATH",
                str(self.LOCAL_KNOWLEDGE_DIR / "markdown_memory_index.db"),
            )
        ).expanduser()
        self.MARKDOWN_MEMORY_TASK_DB_PATH = Path(
            os.getenv(
                "MARKDOWN_MEMORY_TASK_DB_PATH",
                str(self.LOCAL_KNOWLEDGE_DIR / "markdown_memory_tasks.db"),
            )
        ).expanduser()
        self.MARKDOWN_MEMORY_AUTO_INTERVAL = int(os.getenv("MARKDOWN_MEMORY_AUTO_INTERVAL", "5"))
        self.MARKDOWN_MEMORY_DREAM_CRON = os.getenv("MARKDOWN_MEMORY_DREAM_CRON", "0 3 * * *")
        self.MARKDOWN_MEMORY_REBUILD_INDEX_ON_START = os.getenv(
            "MARKDOWN_MEMORY_REBUILD_INDEX_ON_START",
            "false",
        ).lower() in ("true", "1", "t", "yes")
        self.MARKDOWN_MEMORY_SEARCH_MAX_RESULTS = int(os.getenv("MARKDOWN_MEMORY_SEARCH_MAX_RESULTS", "6"))
        self.MARKDOWN_MEMORY_SEARCH_MIN_SCORE = float(os.getenv("MARKDOWN_MEMORY_SEARCH_MIN_SCORE", "0.25"))
        self.MARKDOWN_MEMORY_VECTOR_ENABLED = os.getenv("MARKDOWN_MEMORY_VECTOR_ENABLED", "true").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.MARKDOWN_MEMORY_FTS_ENABLED = os.getenv("MARKDOWN_MEMORY_FTS_ENABLED", "true").lower() in (
            "true",
            "1",
            "t",
            "yes",
        )
        self.MARKDOWN_MEMORY_TEXT_WEIGHT = float(os.getenv("MARKDOWN_MEMORY_TEXT_WEIGHT", "0.35"))
        self.MARKDOWN_MEMORY_VECTOR_WEIGHT = float(os.getenv("MARKDOWN_MEMORY_VECTOR_WEIGHT", "0.65"))
        self.MARKDOWN_MEMORY_MAX_INJECT_CHARS = int(os.getenv("MARKDOWN_MEMORY_MAX_INJECT_CHARS", "2500"))
        self.MARKDOWN_MEMORY_CHUNK_CHARS = int(os.getenv("MARKDOWN_MEMORY_CHUNK_CHARS", "1200"))
        self.MARKDOWN_MEMORY_CHUNK_OVERLAP = int(os.getenv("MARKDOWN_MEMORY_CHUNK_OVERLAP", "120"))
        self.MARKDOWN_MEMORY_DREAM_LOOKBACK_DAYS = int(os.getenv("MARKDOWN_MEMORY_DREAM_LOOKBACK_DAYS", "14"))

        # ── WeChat Channel ──
        self.WEIXIN_BOT_TOKEN = os.getenv("WEIXIN_BOT_TOKEN", "")
        self.WEIXIN_BASE_URL = os.getenv("WEIXIN_BASE_URL", "https://ilinkai.weixin.qq.com")
        self.WEIXIN_STATE_DIR = os.getenv("WEIXIN_STATE_DIR", "")

        # ── QQ Bot Channel ──
        self.QQ_ENABLED = os.getenv("QQ_ENABLED", "false").lower() in ("true", "1", "t", "yes")
        self.QQ_APP_ID = os.getenv("QQ_APP_ID", "")
        self.QQ_SECRET = os.getenv("QQ_SECRET", "")

        # ── Saved Content ──
        _saved_content_override = os.getenv("SAVED_CONTENT_DIR", "").strip()
        if _saved_content_override:
            self.SAVED_CONTENT_DIR = Path(_saved_content_override).expanduser()
        else:
            self.SAVED_CONTENT_DIR = self.LOCAL_KNOWLEDGE_DIR / "saved_content"

        # SQLModel app DB
        explicit_database_url = os.getenv("DATABASE_URL", "").strip()
        if explicit_database_url:
            self.DATABASE_URL = explicit_database_url
        else:
            _app_sqlite_override = os.getenv("APP_DATABASE_SQLITE_PATH", "").strip()
            if _app_sqlite_override:
                app_db_path = Path(_app_sqlite_override).expanduser()
            else:
                app_db_path = self.LOCAL_KNOWLEDGE_DIR / os.getenv(
                    "APP_DATABASE_NAME",
                    "app.db",
                )
            app_db_path.parent.mkdir(parents=True, exist_ok=True)
            self.DATABASE_URL = f"sqlite:///{app_db_path.resolve().as_posix()}"
        self.SKILLS_DIR = Path(
            os.getenv(
                "SKILLS_DIR",
                str(Path(__file__).resolve().parents[2] / "skills"),
            )
        )
        self.SKILLS_STATE_PATH = Path(
            os.getenv(
                "SKILLS_STATE_PATH",
                str(Path(__file__).resolve().parents[2] / "skills_state.json"),
            )
        )

        # Logging Configuration
        self.LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" or "console"

        # Postgres Configuration
        self.POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
        self.POSTGRES_DB = os.getenv("POSTGRES_DB", "food_order_db")
        self.POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
        self.POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
        self.POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
        self.POSTGRES_MAX_OVERFLOW = int(os.getenv("POSTGRES_MAX_OVERFLOW", "10"))

        # Rate Limiting Configuration
        self.RATE_LIMIT_DEFAULT = parse_list_from_env("RATE_LIMIT_DEFAULT", ["200 per day", "50 per hour"])

        # Rate limit endpoints defaults
        default_endpoints = {
            "chat": ["30 per minute"],
            "chat_stream": ["20 per minute"],
            "messages": ["50 per minute"],
            "register": ["10 per hour"],
            "login": ["20 per minute"],
            "root": ["10 per minute"],
            "health": ["20 per minute"],
            "weread_validate": ["30 per hour"],
        }

        # Update rate limit endpoints from environment variables
        self.RATE_LIMIT_ENDPOINTS = default_endpoints.copy()
        for endpoint in default_endpoints:
            env_key = f"RATE_LIMIT_{endpoint.upper()}"
            value = parse_list_from_env(env_key)
            if value:
                self.RATE_LIMIT_ENDPOINTS[endpoint] = value

        # Evaluation Configuration
        self.EVALUATION_LLM = os.getenv("EVALUATION_LLM", "gpt-5")
        self.EVALUATION_BASE_URL = os.getenv("EVALUATION_BASE_URL", "https://api.openai.com/v1")
        self.EVALUATION_API_KEY = os.getenv("EVALUATION_API_KEY", self.OPENAI_API_KEY)
        self.EVALUATION_SLEEP_TIME = int(os.getenv("EVALUATION_SLEEP_TIME", "10"))

        # Apply environment-specific settings
        self.apply_environment_settings()

    def apply_environment_settings(self):
        """Apply environment-specific settings based on the current environment."""
        env_settings = {
            Environment.DEVELOPMENT: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "200 per hour"],
            },
            Environment.STAGING: {
                "DEBUG": False,
                "LOG_LEVEL": "INFO",
                "RATE_LIMIT_DEFAULT": ["500 per day", "100 per hour"],
            },
            Environment.PRODUCTION: {
                "DEBUG": False,
                "LOG_LEVEL": "WARNING",
                "RATE_LIMIT_DEFAULT": ["200 per day", "50 per hour"],
            },
            Environment.TEST: {
                "DEBUG": True,
                "LOG_LEVEL": "DEBUG",
                "LOG_FORMAT": "console",
                "RATE_LIMIT_DEFAULT": ["1000 per day", "1000 per hour"],  # Relaxed for testing
            },
        }

        # Get settings for current environment
        current_env_settings = env_settings.get(self.ENVIRONMENT, {})

        # Apply settings if not explicitly set in environment variables
        for key, value in current_env_settings.items():
            env_var_name = key.upper()
            # Only override if environment variable wasn't explicitly set
            if env_var_name not in os.environ:
                setattr(self, key, value)


# Create settings instance
settings = Settings()
