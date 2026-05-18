"""This file contains the main application entry point."""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import (
    Any,
    AsyncIterator,
    Dict,
)

from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.api import api_router
from app.api.v1.channels import set_channel_manager
from app.channels.bus import MessageBus
from app.channels.manager import ChannelManager
from app.channels.agent import ChannelAgentBridge
from app.infrastructure.config import settings
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger, setup_logging
from app.infrastructure.metrics import setup_metrics
from app.infrastructure.middleware import (
    LoggingContextMiddleware,
    MetricsMiddleware,
)
from app.infrastructure.database import database_service
from app.features.markdown_memory.manager import markdown_memory_manager
from app.features.markdown_memory.scheduler import memory_scheduler

logger = get_logger(__name__)


def _get_environment_value() -> str:
    if hasattr(settings.ENVIRONMENT, "value"):
        return settings.ENVIRONMENT.value
    return str(settings.ENVIRONMENT)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Handle application startup and shutdown events."""
    logger.info(
        "application_startup",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        api_prefix=settings.API_V1_STR,
    )
    if settings.MARKDOWN_MEMORY_ENABLED:
        await markdown_memory_manager.start()
        await markdown_memory_manager.start_task_worker()
        await memory_scheduler.start()
        logger.info("markdown_memory_manager_started")

    settings.SAVED_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("saved_content_dir_ready", path=str(settings.SAVED_CONTENT_DIR))

    # ── Channel system ──
    bus = app.state.channel_bus = MessageBus()
    ch_mgr = app.state.channel_manager = ChannelManager(bus)
    set_channel_manager(ch_mgr)

    # Add WeChat channel (always available via API; QR login grants token)
    ch_mgr.add(
        "weixin",
        token=settings.WEIXIN_BOT_TOKEN,
        base_url=settings.WEIXIN_BASE_URL,
        state_dir=settings.WEIXIN_STATE_DIR,
    )

    # Add QQ channel from config (WebSocket managed by botpy, non-blocking)
    if settings.QQ_ENABLED:
        ch_mgr.add("qq", app_id=settings.QQ_APP_ID, secret=settings.QQ_SECRET)
        logger.info("qq_channel_registered")

    # Start all channels (non-blocking, each runs in its own task)
    active_channels = [c for c in ch_mgr.get_channels().values() if c.has_token]
    if active_channels:
        await ch_mgr.start_all()

    # Always start the agent bridge (handles late-joining channels after QR login)
    from app.runtimes.deep_agent.client import DeepAgentClient
    agent_client = DeepAgentClient()
    bridge = app.state.channel_bridge = ChannelAgentBridge(bus, ch_mgr, agent_client)
    await bridge.start()
    from app.api.v1.channels import set_channel_bridge
    set_channel_bridge(bridge)

    if active_channels:
        logger.info("channel_system_started")
    else:
        logger.info("channel_system_idle", message="Bridge ready, waiting for channel login.")

    yield

    # ── Channel shutdown ──
    if hasattr(app.state, "channel_bridge"):
        await app.state.channel_bridge.stop()
    if hasattr(app.state, "channel_manager"):
        await app.state.channel_manager.stop_all()
    logger.info("channel_system_stopped")

    await memory_scheduler.stop()
    await markdown_memory_manager.close()
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    setup_logging(
        debug=settings.DEBUG,
        environment=_get_environment_value(),
        service=settings.PROJECT_NAME,
        log_format=settings.LOG_FORMAT,
        log_dir=settings.LOG_DIR,
        log_level=settings.LOG_LEVEL,
    )

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        lifespan=lifespan,
    )

    # Set up Prometheus metrics
    setup_metrics(app)

    # Add logging context middleware (must be added before other middleware to capture context)
    app.add_middleware(LoggingContextMiddleware)

    # Add custom metrics middleware
    app.add_middleware(MetricsMiddleware)

    # Set up CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router
    app.include_router(api_router, prefix=settings.API_V1_STR)

    # Set up rate limiter exception handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle validation errors from request data."""
        logger.warning(
            "validation_error",
            client_host=request.client.host if request.client else "unknown",
            path=request.url.path,
            errors=str(exc.errors()),
        )

        formatted_errors = []
        for error in exc.errors():
            loc = " -> ".join(str(loc_part) for loc_part in error["loc"] if loc_part != "body")
            formatted_errors.append({"field": loc, "message": error["msg"]})

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation error", "errors": formatted_errors},
        )

    @app.get("/")
    @limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
    async def root(request: Request) -> Dict[str, Any]:
        """Root endpoint returning basic API information."""
        logger.info("root_endpoint_called")
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "healthy",
            "environment": settings.ENVIRONMENT.value,
            "swagger_url": "/docs",
            "redoc_url": "/redoc",
        }

    @app.get("/health")
    @limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
    async def health_check(request: Request) -> Dict[str, Any]:
        """Health check endpoint with environment-specific information."""
        logger.info("health_check_called")

        # Check database connectivity
        db_healthy = await database_service.health_check()

        response = {
            "status": "healthy" if db_healthy else "degraded",
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT.value,
            "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
            "timestamp": datetime.now().isoformat(),
        }

        # If DB is unhealthy, set the appropriate status code
        status_code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

        return JSONResponse(content=response, status_code=status_code)

    return app


app = create_app()
