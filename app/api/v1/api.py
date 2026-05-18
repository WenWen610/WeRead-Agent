"""API v1 router configuration.

This module sets up the main API router and includes all sub-routers for different
endpoints like authentication and chatbot functionality.
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.channels import router as channels_router
from app.api.v1.chatbot import router as chatbot_router
from app.api.v1.memory import router as memory_router
from app.api.v1.saved_content import router as saved_content_router
from app.api.v1.weread import router as weread_router
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

api_router = APIRouter()

# Include routers
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(chatbot_router, prefix="/chatbot", tags=["chatbot"])
api_router.include_router(memory_router, prefix="/memory", tags=["memory"])
api_router.include_router(weread_router, prefix="/weread", tags=["weread"])
api_router.include_router(saved_content_router, prefix="/saved-content", tags=["saved-content"])
api_router.include_router(channels_router, prefix="/channels", tags=["channels"])


@api_router.get("/health")
async def health_check():
    """Health check endpoint.

    Returns:
        dict: Health status information.
    """
    logger.info("health_check_called")
    return {"status": "healthy", "version": "1.0.0"}
