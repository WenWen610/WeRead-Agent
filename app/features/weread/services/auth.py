"""WeRead binding and encrypted credential management."""

import base64
import hashlib
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger
from app.models.weread_binding import (
    WeReadBinding,
    WeReadBindingSource,
    WeReadBindingStatus,
)
from app.features.weread.stores.binding_store import WeReadBindingStore, weread_binding_store

logger = get_logger(__name__)


class WeReadBindingError(RuntimeError):
    """Base error for WeRead binding issues."""


class WeReadBindingRequiredError(WeReadBindingError):
    """Raised when a user has not connected WeRead yet."""


class WeReadReauthRequiredError(WeReadBindingError):
    """Raised when a saved WeRead credential is no longer usable."""


class WeReadCredentialCipher:
    """Encrypt and decrypt sensitive WeRead cookies."""

    def __init__(self, secret: str | None = None):
        raw_secret = (secret or settings.WEREAD_CREDENTIAL_SECRET or "").strip()
        if not raw_secret:
            raise ValueError("WEREAD_CREDENTIAL_SECRET or JWT_SECRET_KEY must be configured")
        self._fernet = Fernet(self._build_fernet_key(raw_secret))

    @staticmethod
    def _build_fernet_key(raw_secret: str) -> bytes:
        try:
            decoded = base64.urlsafe_b64decode(raw_secret.encode("utf-8"))
            if len(decoded) == 32:
                return raw_secret.encode("utf-8")
        except Exception:
            pass

        digest = hashlib.sha256(raw_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt_cookie(self, cookie: str) -> str:
        """Encrypt a WeRead cookie before persistence."""
        normalized_cookie = cookie.strip()
        if not normalized_cookie:
            raise ValueError("WeRead cookie cannot be empty")
        return self._fernet.encrypt(normalized_cookie.encode("utf-8")).decode("utf-8")

    def decrypt_cookie(self, encrypted_cookie: str) -> str:
        """Decrypt a previously stored WeRead cookie."""
        try:
            decrypted = self._fernet.decrypt(encrypted_cookie.encode("utf-8"))
        except InvalidToken as exc:
            raise WeReadBindingError("Stored WeRead credential could not be decrypted") from exc
        return decrypted.decode("utf-8")


class WeReadAuthService:
    """Manage per-user WeRead bindings and credential lifecycle."""

    def __init__(
        self,
        db_service: WeReadBindingStore = weread_binding_store,
        cipher: WeReadCredentialCipher | None = None,
    ) -> None:
        self.db_service = db_service
        self._cipher = cipher

    @property
    def cipher(self) -> WeReadCredentialCipher:
        """Lazily initialize the cookie cipher when first needed."""
        if self._cipher is None:
            self._cipher = WeReadCredentialCipher()
        return self._cipher

    async def get_binding(self, user_id: int) -> WeReadBinding | None:
        """Return the current WeRead binding record for a user."""
        return await self.db_service.get_weread_binding(user_id)

    async def bind_cookie(
        self,
        user_id: int,
        cookie: str,
        source: WeReadBindingSource = WeReadBindingSource.MANUAL,
    ) -> WeReadBinding:
        """Persist a new cookie for the given user."""
        encrypted_cookie = self.cipher.encrypt_cookie(cookie)
        binding = await self.db_service.upsert_weread_binding(
            user_id=user_id,
            encrypted_cookie=encrypted_cookie,
            status=WeReadBindingStatus.ACTIVE.value,
            source=source.value,
            last_error=None,
            last_validated_at=datetime.now(UTC),
        )
        logger.info("weread_cookie_bound", user_id=user_id, source=source.value)
        return binding

    async def require_active_cookie(self, user_id: int) -> str:
        """Return a decrypted cookie when the binding is active."""
        binding = await self.get_binding(user_id)
        if binding is None:
            raise WeReadBindingRequiredError("WeRead account is not connected for this user")

        if binding.status != WeReadBindingStatus.ACTIVE.value:
            raise WeReadReauthRequiredError("WeRead authorization requires reauthentication")

        try:
            return self.cipher.decrypt_cookie(binding.encrypted_cookie)
        except WeReadBindingError as exc:
            await self.mark_reauth_required(user_id, "Stored WeRead credential could not be decrypted")
            raise WeReadReauthRequiredError("Stored WeRead credential is invalid. Please reconnect WeRead.") from exc

    async def mark_cookie_valid(self, user_id: int) -> WeReadBinding | None:
        """Refresh binding health after a successful upstream call."""
        return await self.db_service.update_weread_binding_status(
            user_id=user_id,
            status=WeReadBindingStatus.ACTIVE.value,
            last_error=None,
            last_validated_at=datetime.now(UTC),
        )

    async def mark_reauth_required(self, user_id: int, last_error: str | None = None) -> WeReadBinding | None:
        """Mark the binding as needing a new login."""
        binding = await self.db_service.update_weread_binding_status(
            user_id=user_id,
            status=WeReadBindingStatus.REAUTH_REQUIRED.value,
            last_error=last_error,
            last_validated_at=datetime.now(UTC),
        )
        logger.info("weread_binding_marked_reauth_required", user_id=user_id)
        return binding

    async def mark_expired(self, user_id: int, last_error: str | None = None) -> WeReadBinding | None:
        """Mark the binding as expired after upstream rejects the cookie."""
        binding = await self.db_service.update_weread_binding_status(
            user_id=user_id,
            status=WeReadBindingStatus.EXPIRED.value,
            last_error=last_error,
            last_validated_at=datetime.now(UTC),
        )
        logger.info("weread_binding_marked_expired", user_id=user_id)
        return binding

    async def clear_binding(self, user_id: int) -> bool:
        """Delete a user's saved WeRead binding."""
        deleted = await self.db_service.delete_weread_binding(user_id)
        if deleted:
            logger.info("weread_binding_cleared", user_id=user_id)
        return deleted


weread_auth_service = WeReadAuthService()
