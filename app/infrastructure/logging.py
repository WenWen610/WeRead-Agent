"""Structured logging configuration (structlog + stdlib logging).

Design goals:
- Keep request-scoped context via ContextVar (user_id/session_id/request_id).
- Produce console logs in development and JSON logs in non-console mode.
- Keep simple imports for context helpers: `bind_context`, `clear_context`.
- Avoid fragile `logging.basicConfig` behavior under uvicorn/gunicorn.
"""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, cast

import structlog

from app.infrastructure.config import settings


# Request-scoped context. default=None avoids mutable-default pitfalls.
_request_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("request_context", default=None)


# Internal flag to avoid duplicate setup.
_configured = False


StructLogger = structlog.stdlib.BoundLogger


def bind_context(**kwargs: Any) -> None:
    """Bind key/value pairs to the current request context."""
    current = _request_context.get() or {}
    _request_context.set({**current, **kwargs})


def clear_context() -> None:
    """Clear request context at request end."""
    _request_context.set(None)


def get_context() -> Dict[str, Any]:
    """Return current request context."""
    return _request_context.get() or {}


def _add_context_to_event_dict(_: Any, __: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_context()
    if ctx:
        event_dict.update(ctx)
    return event_dict


def _add_static_fields(environment: str, service: str):
    def processor(_: Any, __: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
        event_dict.setdefault("environment", environment)
        event_dict.setdefault("service", service)
        return event_dict

    return processor


def _parse_log_level(debug: bool, configured_level: str | None) -> int:
    """Prefer explicit LOG_LEVEL; fallback to DEBUG flag."""
    if configured_level:
        candidate = configured_level.strip().upper()
        if hasattr(logging, candidate):
            return int(getattr(logging, candidate))
        if candidate.isdigit():
            return int(candidate)
    return logging.DEBUG if debug else logging.INFO


def setup_logging(
    *,
    debug: bool | None = None,
    environment: str | None = None,
    service: str | None = None,
    log_format: str | None = None,
    log_to_file: bool | None = None,
    log_dir: str | Path | None = None,
    file_backup_days: int = 7,
    log_level: str | None = None,
    force: bool = False,
) -> None:
    """Configure stdlib logging and structlog.

    Args:
        force: If True, replace existing root handlers.
    """
    global _configured

    if _configured and not force:
        return

    debug = bool(settings.DEBUG) if debug is None else debug
    environment = (
        (settings.ENVIRONMENT.value if hasattr(settings.ENVIRONMENT, "value") else str(settings.ENVIRONMENT))
        if environment is None
        else environment
    )
    service = settings.PROJECT_NAME if service is None else service
    log_format = settings.LOG_FORMAT if log_format is None else log_format
    log_level = settings.LOG_LEVEL if log_level is None else log_level
    log_dir = settings.LOG_DIR if log_dir is None else log_dir

    if log_to_file is None:
        raw = os.getenv("LOG_TO_FILE", "false")
        log_to_file = raw.strip().lower() in {"1", "true", "yes", "on"}

    level = _parse_log_level(debug=debug, configured_level=log_level)

    root_logger = logging.getLogger()

    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    if root_logger.handlers:
        # Another subsystem already configured logging; avoid duplicate handlers.
        _configured = True
        return

    handlers: list[logging.Handler] = []

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    handlers.append(stream_handler)

    if log_to_file:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        file_path = log_dir_path / f"{service}-{environment}.log"
        file_handler = TimedRotatingFileHandler(
            filename=str(file_path),
            when="midnight",
            interval=1,
            backupCount=file_backup_days,
            encoding="utf-8",
            delay=True,
            utc=False,
        )
        file_handler.setLevel(level)
        handlers.append(file_handler)

    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        _add_context_to_event_dict,
        _add_static_fields(environment, service),
    ]

    renderer = structlog.dev.ConsoleRenderer() if log_format == "console" else structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger.setLevel(level)
    for handler in handlers:
        root_logger.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None) -> StructLogger:
    """Return a structlog logger with BoundLogger interface for IDE hints."""
    logger_obj = structlog.get_logger(name) if name else structlog.get_logger()
    return cast(StructLogger, logger_obj)


__all__ = ["setup_logging", "get_logger", "bind_context", "clear_context", "get_context"]
