"""Server-side WeRead QR login orchestration using Playwright."""

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

from tenacity import AsyncRetrying, retry_if_result, stop_after_delay, wait_fixed

try:
    from playwright.async_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright
except ImportError:  # pragma: no cover - optional dependency during local tests
    Browser = BrowserContext = Page = None
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger
from app.models.weread_binding import WeReadBindingSource
from app.models.weread_login_session import (
    WeReadLoginSession,
    WeReadQrLoginSessionStatus,
)
from app.features.weread.services.weread import weread_service
from app.features.weread.services.auth import WeReadReauthRequiredError, weread_auth_service
from app.features.weread.stores.login_session_store import WeReadLoginSessionStore, weread_login_session_store

logger = get_logger(__name__)


class WeReadQrLoginUnavailableError(RuntimeError):
    """Raised when QR login support is unavailable on the server."""


class WeReadBrowserService:
    """Coordinate temporary browser sessions for WeRead QR login."""

    _BROWSER_LAUNCH_TIMEOUT_MS = 15_000
    _PAGE_READY_TIMEOUT_MS = 15_000
    _LOGIN_ENTRY_SELECTORS = (
        "text=登录",
        "text=微信登录",
        "text=扫码登录",
        "button:has-text('登录')",
        "a:has-text('登录')",
        "[role='button']:has-text('登录')",
    )
    _QR_SELECTORS = (
        "img[alt*='二维码']",
        "img[alt*='QR']",
        "[class*='qr'] img",
        "[class*='QRCode'] img",
        "canvas",
    )

    def __init__(self, store: WeReadLoginSessionStore = weread_login_session_store) -> None:
        self.store = store
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    async def start_qr_login_session(self, user_id: int) -> WeReadLoginSession:
        """Start a new QR login session for the user."""
        if async_playwright is None:
            raise WeReadQrLoginUnavailableError("Playwright is not installed on this server")

        cancelled_sessions = await self.store.cancel_active_sessions(user_id)
        for login_session in cancelled_sessions:
            task = self._active_tasks.pop(login_session.id, None)
            if task is not None:
                task.cancel()

        login_session_id = str(uuid.uuid4())
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.WEREAD_QR_LOGIN_TIMEOUT_SECONDS)
        login_session = await self.store.create_login_session(
            session_id=login_session_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        task = asyncio.create_task(self._run_login_flow(login_session.id, user_id), name=f"weread_qr_{login_session.id}")
        self._active_tasks[login_session.id] = task
        logger.info("weread_qr_login_started", login_session_id=login_session.id, user_id=user_id)
        return login_session

    async def get_qr_login_session(self, *, user_id: int, session_id: str) -> WeReadLoginSession | None:
        """Fetch a QR login session scoped to the user."""
        login_session = await self.store.get_user_login_session(user_id=user_id, session_id=session_id)
        if login_session is None:
            return None

        if self._is_session_expired(login_session):
            task = self._active_tasks.pop(session_id, None)
            if task is not None:
                task.cancel()
            expired_at = datetime.now(UTC)
            updated_session = await self.store.update_login_session(
                session_id=session_id,
                status=WeReadQrLoginSessionStatus.EXPIRED.value,
                last_error="WeRead QR login timed out before the QR flow completed.",
                completed_at=expired_at,
            )
            if updated_session is not None:
                logger.info(
                    "weread_qr_login_session_marked_expired",
                    login_session_id=session_id,
                    user_id=user_id,
                )
                return updated_session

        return login_session

    async def cancel_qr_login_session(self, *, user_id: int, session_id: str) -> WeReadLoginSession | None:
        """Cancel a QR login session if it is still active."""
        login_session = await self.store.get_user_login_session(user_id=user_id, session_id=session_id)
        if login_session is None:
            return None

        task = self._active_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()

        if login_session.status in {
            WeReadQrLoginSessionStatus.SUCCESS.value,
            WeReadQrLoginSessionStatus.EXPIRED.value,
            WeReadQrLoginSessionStatus.FAILED.value,
            WeReadQrLoginSessionStatus.CANCELLED.value,
        }:
            return login_session

        cancelled_at = datetime.now(UTC)
        return await self.store.update_login_session(
            session_id=session_id,
            status=WeReadQrLoginSessionStatus.CANCELLED.value,
            last_error=None,
            completed_at=cancelled_at,
        )

    async def _run_login_flow(self, session_id: str, user_id: int) -> None:
        browser: Browser | None = None
        context: BrowserContext | None = None
        page: Page | None = None
        try:
            async with async_playwright() as playwright:
                logger.info("weread_qr_login_browser_launching", login_session_id=session_id, user_id=user_id)
                launch_kwargs = {
                    "headless": settings.WEREAD_QR_HEADLESS,
                    "args": ["--disable-dev-shm-usage"],
                    "timeout": self._BROWSER_LAUNCH_TIMEOUT_MS,
                }
                chromium_executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
                if chromium_executable_path:
                    launch_kwargs["executable_path"] = chromium_executable_path
                browser = await playwright.chromium.launch(**launch_kwargs)
                logger.info("weread_qr_login_browser_launched", login_session_id=session_id, user_id=user_id)
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(self._PAGE_READY_TIMEOUT_MS)
                logger.info(
                    "weread_qr_login_page_loading",
                    login_session_id=session_id,
                    user_id=user_id,
                    login_url=settings.WEREAD_QR_LOGIN_URL,
                )
                await page.goto(
                    settings.WEREAD_QR_LOGIN_URL,
                    wait_until="domcontentloaded",
                    timeout=self._PAGE_READY_TIMEOUT_MS,
                )
                logger.info("weread_qr_login_page_loaded", login_session_id=session_id, user_id=user_id)
                await page.wait_for_timeout(1200)
                logger.info("weread_qr_login_waiting_for_qr", login_session_id=session_id, user_id=user_id)
                await self._ensure_qr_visible(page)
                logger.info("weread_qr_login_qr_visible", login_session_id=session_id, user_id=user_id)

                qr_image_base64 = await self._capture_qr_image_base64(page)
                await self.store.update_login_session(
                    session_id=session_id,
                    status=WeReadQrLoginSessionStatus.QR_READY.value,
                    qr_image_base64=qr_image_base64,
                    last_error=None,
                    completed_at=None,
                )

                cookie = await self._wait_for_authenticated_cookie(context)
                if cookie is None:
                    expired_at = datetime.now(UTC)
                    await self.store.update_login_session(
                        session_id=session_id,
                        status=WeReadQrLoginSessionStatus.EXPIRED.value,
                        last_error="WeRead QR login timed out before a valid cookie was captured.",
                        completed_at=expired_at,
                    )
                    logger.info("weread_qr_login_expired", login_session_id=session_id, user_id=user_id)
                    return

                await weread_auth_service.bind_cookie(
                    user_id=user_id,
                    cookie=cookie,
                    source=WeReadBindingSource.QR,
                )
                try:
                    await weread_service.sync_bookshelf_snapshot(user_id)
                except WeReadReauthRequiredError as sync_exc:
                    failed_at = datetime.now(UTC)
                    await weread_auth_service.mark_reauth_required(user_id, str(sync_exc))
                    await self.store.update_login_session(
                        session_id=session_id,
                        status=WeReadQrLoginSessionStatus.FAILED.value,
                        last_error="WeRead cookie captured but was rejected during validation. Please retry QR login.",
                        completed_at=failed_at,
                    )
                    logger.warning(
                        "weread_qr_login_validation_failed",
                        login_session_id=session_id,
                        user_id=user_id,
                        error=str(sync_exc),
                    )
                    return
                except Exception as sync_exc:
                    logger.exception(
                        "weread_bookshelf_initial_sync_failed_after_qr_login",
                        login_session_id=session_id,
                        user_id=user_id,
                        error=str(sync_exc),
                    )
                    failed_at = datetime.now(UTC)
                    await self.store.update_login_session(
                        session_id=session_id,
                        status=WeReadQrLoginSessionStatus.FAILED.value,
                        last_error="WeRead validation failed after QR login. Please retry.",
                        completed_at=failed_at,
                    )
                    return
                completed_at = datetime.now(UTC)
                await self.store.update_login_session(
                    session_id=session_id,
                    status=WeReadQrLoginSessionStatus.SUCCESS.value,
                    last_error=None,
                    completed_at=completed_at,
                )
                logger.info("weread_qr_login_succeeded", login_session_id=session_id, user_id=user_id)
        except PlaywrightTimeoutError as exc:
            failed_at = datetime.now(UTC)
            await self.store.update_login_session(
                session_id=session_id,
                status=WeReadQrLoginSessionStatus.FAILED.value,
                last_error="Timed out while opening WeRead login page or waiting for the QR code.",
                completed_at=failed_at,
            )
            logger.exception(
                "weread_qr_login_timeout",
                login_session_id=session_id,
                user_id=user_id,
                error=str(exc),
            )
        except asyncio.CancelledError:
            logger.info("weread_qr_login_cancelled", login_session_id=session_id, user_id=user_id)
            raise
        except Exception as exc:
            failed_at = datetime.now(UTC)
            await self.store.update_login_session(
                session_id=session_id,
                status=WeReadQrLoginSessionStatus.FAILED.value,
                last_error=str(exc),
                completed_at=failed_at,
            )
            logger.exception("weread_qr_login_failed", login_session_id=session_id, user_id=user_id, error=str(exc))
        finally:
            self._active_tasks.pop(session_id, None)
            if page is not None:
                await page.close()
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()

    async def _capture_qr_image_base64(self, page: Page) -> str:
        """Capture the current QR image as base64 PNG."""
        for selector in self._QR_SELECTORS:
            handle = await page.query_selector(selector)
            if handle is None:
                continue
            image_bytes = await handle.screenshot(type="png")
            return base64.b64encode(image_bytes).decode("utf-8")

        image_bytes = await page.screenshot(type="png", full_page=True)
        return base64.b64encode(image_bytes).decode("utf-8")

    async def _ensure_qr_visible(self, page: Page) -> None:
        """Ensure the QR login dialog is visible before screenshotting."""
        if await self._has_qr_visible(page):
            return

        for selector in self._LOGIN_ENTRY_SELECTORS:
            locator = page.locator(selector).first
            try:
                if await locator.count() == 0 or not await locator.is_visible():
                    continue
                await locator.click()
                await page.wait_for_timeout(800)
                if await self._has_qr_visible(page):
                    return
            except Exception:
                continue

        for selector in self._QR_SELECTORS:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                return
            except Exception:
                continue

        raise RuntimeError("WeRead QR code did not appear after opening the login page.")

    async def _has_qr_visible(self, page: Page) -> bool:
        """Check whether any QR element is currently visible on the page."""
        for selector in self._QR_SELECTORS:
            handle = await page.query_selector(selector)
            if handle is not None:
                return True
        return False

    async def _wait_for_authenticated_cookie(self, context: BrowserContext) -> str | None:
        """Wait until WeRead cookies appear or timeout is reached."""
        async for attempt in AsyncRetrying(
            stop=stop_after_delay(settings.WEREAD_QR_LOGIN_TIMEOUT_SECONDS),
            wait=wait_fixed(settings.WEREAD_QR_POLL_INTERVAL_SECONDS),
            retry=retry_if_result(lambda result: result is None),
            reraise=False,
        ):
            with attempt:
                cookie = await self._extract_authenticated_cookie(context)
            if cookie is not None:
                return cookie
        return None

    async def _extract_authenticated_cookie(self, context: BrowserContext) -> str | None:
        """Extract a serialized WeRead cookie header from the browser context."""
        # NOTE: WeRead auth cookies may be set across multiple related domains.
        # Query both all-context cookies and per-origin cookies to avoid missing partitioned entries.
        cookie_candidates = await context.cookies()
        per_origin_urls = [
            settings.WEREAD_QR_LOGIN_URL,
            "https://weread.qq.com/",
            "https://weread.qq.com/web/login",
            "https://qq.com/",
            "https://wx.qq.com/",
            "https://wechat.com/",
        ]
        for url in per_origin_urls:
            try:
                cookie_candidates.extend(await context.cookies(url))
            except Exception:
                continue

        # Deduplicate cookie rows by (name, domain, path).
        deduped: dict[tuple[str, str, str], dict] = {}
        for cookie in cookie_candidates:
            key = (
                str(cookie.get("name", "")),
                str(cookie.get("domain", "")),
                str(cookie.get("path", "")),
            )
            deduped[key] = cookie
        cookies = list(deduped.values())

        allowed_domain_suffixes = (
            "weread.qq.com",
            ".weread.qq.com",
            "qq.com",
            ".qq.com",
        )
        allowed_names = {
            # weread-specific
            "wr_skey",
            "wr_vid",
            "wr_gid",
            # common wechat/qq auth cookies sometimes required by weread web APIs
            "skey",
            "lskey",
            "uin",
            "luin",
            "key",
        }

        relevant_cookies = []
        for cookie in cookies:
            domain = str(cookie.get("domain", "") or "")
            name = str(cookie.get("name", "") or "")
            if not name or "value" not in cookie:
                continue

            is_allowed_domain = any(domain == suffix or domain.endswith(suffix) for suffix in allowed_domain_suffixes)
            if is_allowed_domain or name.startswith("wr_") or name in allowed_names:
                relevant_cookies.append(cookie)

        if not relevant_cookies:
            return None

        # Guard: don't treat pre-login tracking cookies as "authenticated".
        # We need at least one auth-bearing cookie; otherwise WeRead API will reject requests.
        cookie_names = {str(cookie.get("name", "")) for cookie in relevant_cookies}
        required_auth_cookie_names = {
            "wr_skey",
            "wr_vid",
            "wr_uin",
            "skey",
            "lskey",
            "uin",
            "luin",
        }
        if not (cookie_names & required_auth_cookie_names):
            logger.info(
                "weread_qr_login_cookie_incomplete_waiting",
                cookie_count=len(relevant_cookies),
                cookie_names=sorted(name for name in cookie_names if name)[:30],
            )
            return None

        # Prefer deterministic ordering to keep encrypted payload stable across runs.
        relevant_cookies.sort(key=lambda item: (str(item.get("domain", "")), str(item.get("name", ""))))
        cookie_header = "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in relevant_cookies)

        logger.info(
            "weread_qr_login_cookie_captured",
            cookie_count=len(relevant_cookies),
            cookie_names=[str(cookie.get("name", "")) for cookie in relevant_cookies][:30],
        )
        return cookie_header

    async def wait_for_qr_ready(
        self,
        user_id: int,
        session_id: str,
        *,
        poll_interval: float = 0.5,
        timeout: float = 15.0,
    ) -> WeReadLoginSession | None:
        """Poll the login session until QR code is ready or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            session = await self.store.get_user_login_session(user_id=user_id, session_id=session_id)
            if session is None:
                return None
            if session.status == WeReadQrLoginSessionStatus.QR_READY.value:
                return session
            if session.status in {
                WeReadQrLoginSessionStatus.FAILED.value,
                WeReadQrLoginSessionStatus.EXPIRED.value,
                WeReadQrLoginSessionStatus.CANCELLED.value,
            }:
                return session
            await asyncio.sleep(poll_interval)
        return await self.store.get_user_login_session(user_id=user_id, session_id=session_id)

    def _is_session_expired(self, login_session: WeReadLoginSession) -> bool:
        """Return whether an active login session is past its expiry."""
        if login_session.status not in {
            WeReadQrLoginSessionStatus.PENDING.value,
            WeReadQrLoginSessionStatus.QR_READY.value,
        }:
            return False
        expires_at = login_session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC)


weread_browser_service = WeReadBrowserService()
