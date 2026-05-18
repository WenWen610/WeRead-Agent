from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import time
from typing import Any, Awaitable, Callable

import httpx
from dotenv import load_dotenv

load_dotenv()

WEREAD_URL = "https://weread.qq.com/"
WEREAD_NOTEBOOKS_URL = "https://weread.qq.com/api/user/notebook"
WEREAD_BOOK_INFO_URL = "https://weread.qq.com/api/book/info"
WEREAD_BOOKMARKLIST_URL = "https://weread.qq.com/web/book/bookmarklist"
WEREAD_CHAPTER_INFO_URL = "https://weread.qq.com/web/book/chapterInfos"
WEREAD_REVIEW_LIST_URL = "https://weread.qq.com/web/review/list"
WEREAD_READ_INFO_URL = "https://weread.qq.com/web/book/getProgress"
WEREAD_SHELF_SYNC_URL = "https://weread.qq.com/web/shelf/sync"
WEREAD_BEST_REVIEW_URL = "https://weread.qq.com/web/review/list/best"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class WeReadApi:
    def __init__(self, cookie: str | None = None) -> None:
        self.cookie = ""
        self.client: httpx.AsyncClient | None = None
        self.initialized = False
        self.direct_cookie = cookie.strip() if isinstance(cookie, str) else ""
        self.command_args = self._parse_command_args()

    def _parse_command_args(self) -> dict[str, Any]:
        args = sys.argv
        if "--args" not in args:
            return {}

        idx = args.index("--args")
        if idx + 1 >= len(args):
            return {}

        try:
            parsed = json.loads(args[idx + 1])
        except json.JSONDecodeError:
            return {}

        if not isinstance(parsed, dict):
            return {}
        return parsed

    async def _try_get_cloud_cookie(self, url: str, cookie_id: str, password: str) -> str | None:
        if url.endswith("/"):
            url = url[:-1]

        req_url = f"{url}/get/{cookie_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(req_url, json={"password": password})
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        cookie_data = payload.get("cookie_data") if isinstance(payload, dict) else None
        if not isinstance(cookie_data, dict):
            return None

        explicit = self._extract_cookies_from_domain(cookie_data, "weread.qq.com")
        if explicit:
            return explicit

        weread = cookie_data.get("weread")
        if isinstance(weread, list):
            valid = []
            for cookie in weread:
                if not isinstance(cookie, dict):
                    continue
                domain = str(cookie.get("domain", ""))
                if domain in (".weread.qq.com", "weread.qq.com"):
                    name = cookie.get("name")
                    value = cookie.get("value")
                    if name and value:
                        valid.append(f"{name}={value}")
            if valid:
                return "; ".join(valid)

        for _, cookies in cookie_data.items():
            if not isinstance(cookies, list):
                continue
            valid = []
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue
                domain = str(cookie.get("domain", ""))
                if domain in (".weread.qq.com", "weread.qq.com"):
                    name = cookie.get("name")
                    value = cookie.get("value")
                    if name and value:
                        valid.append(f"{name}={value}")
            if valid:
                return "; ".join(valid)

        return None

    def _extract_cookies_from_domain(self, cookie_data: dict[str, Any], domain: str) -> str | None:
        cookies = cookie_data.get(domain)
        if not isinstance(cookies, list):
            return None

        items: list[str] = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value:
                items.append(f"{name}={value}")

        return "; ".join(items) if items else None

    async def _get_cookie(self) -> str:
        if self.direct_cookie:
            return self.direct_cookie

        direct_cookie = self.command_args.get("WEREAD_COOKIE")
        if isinstance(direct_cookie, str) and direct_cookie.strip():
            return direct_cookie

        cc_url = self.command_args.get("CC_URL")
        cc_id = self.command_args.get("CC_ID")
        cc_password = self.command_args.get("CC_PASSWORD")
        if all(isinstance(v, str) and v.strip() for v in (cc_url, cc_id, cc_password)):
            cloud_cookie = await self._try_get_cloud_cookie(cc_url, cc_id, cc_password)
            if cloud_cookie:
                return cloud_cookie

        env_url = os.getenv("CC_URL")
        env_id = os.getenv("CC_ID")
        env_password = os.getenv("CC_PASSWORD")
        if env_url and env_id and env_password:
            cloud_cookie = await self._try_get_cloud_cookie(env_url, env_id, env_password)
            if cloud_cookie:
                return cloud_cookie

        env_cookie = os.getenv("WEREAD_COOKIE")
        if not env_cookie or not env_cookie.strip():
            raise RuntimeError("No cookie found. Configure WEREAD_COOKIE or Cookie Cloud.")
        return env_cookie

    def _handle_errcode(self, errcode: int) -> None:
        if errcode in (-2012, -2010):
            raise RuntimeError("WeRead cookie expired. Refresh the cookie and retry.")

    async def _retry(
        self,
        func: Callable[[], Awaitable[Any]],
        max_attempts: int = 3,
        wait_ms: int = 5000,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await func()
            except Exception as exc:  # noqa: PERF203
                last_exc = exc
                if attempt == max_attempts:
                    raise
                random_wait = wait_ms + random.randint(0, 3000)
                await asyncio.sleep(random_wait / 1000)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("All retry attempts failed.")

    async def _ensure_initialized(self) -> None:
        if self.initialized:
            return

        self.cookie = await self._get_cookie()
        self.client = httpx.AsyncClient(
            headers={
                "Cookie": self.cookie,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
            },
            timeout=httpx.Timeout(60.0),
        )
        self.initialized = True

    def _standard_headers(self) -> dict[str, str]:
        return {
            "Cookie": self.cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            "Connection": "keep-alive",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-ch-ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
        }

    async def _visit_homepage(self) -> None:
        await self._ensure_initialized()
        assert self.client is not None
        try:
            await self.client.get(
                WEREAD_URL,
                headers=self._standard_headers(),
                timeout=httpx.Timeout(30.0),
            )
        except Exception:
            return

    async def _make_api_request(
        self,
        url: str,
        method: str = "get",
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> Any:
        await self._ensure_initialized()
        assert self.client is not None

        req_params = dict(params or {})
        if method.lower() == "get":
            req_params["_"] = int(time.time() * 1000)

        try:
            if method.lower() == "get":
                response = await self.client.get(url, params=req_params)
            else:
                if isinstance(data, (dict, list)):
                    response = await self.client.post(url, params=req_params, json=data)
                else:
                    response = await self.client.post(url, params=req_params, data=data)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"API request failed ({url}): {exc}") from exc

        if isinstance(payload, dict):
            errcode = payload.get("errcode")
            if errcode is None:
                errcode = payload.get("errCode")

            if errcode is not None and _to_int(errcode, 0) != 0:
                code = _to_int(errcode, 0)
                self._handle_errcode(code)
                errmsg = payload.get("errmsg") or payload.get("errMsg") or "Unknown error"
                raise RuntimeError(f"API returned error: {errmsg} (code: {code})")

        return payload

    # async def get_bookshelf(self) -> Any:
    #     async def call() -> Any:
    #         return await self._make_api_request(WEREAD_NOTEBOOKS_URL, "get")

    #     return await self._retry(call)

    async def get_entire_shelf(self) -> Any:
        async def call() -> Any:
            return await self._make_api_request(WEREAD_SHELF_SYNC_URL, "get")

        return await self._retry(call)

    async def get_notebook_list(self) -> list[dict[str, Any]]:
        async def call() -> list[dict[str, Any]]:
            data = await self._make_api_request(WEREAD_NOTEBOOKS_URL, "get")
            if isinstance(data, dict) and isinstance(data.get("books"), list):
                return [b for b in data["books"] if isinstance(b, dict)]
            return []

        return await self._retry(call)

    async def ping_session(self) -> None:
        """Single lightweight GET to verify the cookie (no multi-retry)."""
        await self._make_api_request(WEREAD_NOTEBOOKS_URL, "get")

    async def get_book_info(self, book_id: str) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            data = await self._make_api_request(WEREAD_BOOK_INFO_URL, "get", {"bookId": book_id})
            return data if isinstance(data, dict) else {}

        return await self._retry(call)

    async def get_bookmark_list(self, book_id: str) -> list[dict[str, Any]]:
        async def call() -> list[dict[str, Any]]:
            data = await self._make_api_request(WEREAD_BOOKMARKLIST_URL, "get", {"bookId": book_id})
            updated = data.get("updated", []) if isinstance(data, dict) else []
            if not isinstance(updated, list):
                return []
            return [
                mark for mark in updated if isinstance(mark, dict) and mark.get("markText") and mark.get("chapterUid")
            ]

        return await self._retry(call)

    async def get_read_info(self, book_id: str) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            data = await self._make_api_request(WEREAD_READ_INFO_URL, "get", {"bookId": book_id})
            return data if isinstance(data, dict) else {}

        return await self._retry(call)

    async def get_review_list(self, book_id: str) -> list[dict[str, Any]]:
        async def call() -> list[dict[str, Any]]:
            data = await self._make_api_request(
                WEREAD_REVIEW_LIST_URL,
                "get",
                {
                    "bookId": book_id,
                    "listType": 4,
                    "maxIdx": 0,
                    "count": 0,
                    "listMode": 2,
                    "syncKey": 0,
                },
            )
            raw_reviews = data.get("reviews", []) if isinstance(data, dict) else []
            if not isinstance(raw_reviews, list):
                return []

            reviews: list[dict[str, Any]] = []
            for item in raw_reviews:
                if not isinstance(item, dict):
                    continue
                review = item.get("review")
                if not isinstance(review, dict):
                    continue
                if review.get("type") == 4:
                    review = {"chapterUid": 1000000, **review}
                reviews.append(review)
            return reviews

        return await self._retry(call)

    async def get_best_reviews(
        self,
        book_id: str,
        count: int = 10,
        max_idx: int = 0,
        synckey: int = 0,
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            data = await self._make_api_request(
                WEREAD_BEST_REVIEW_URL,
                "get",
                {
                    "bookId": book_id,
                    "synckey": synckey,
                    "maxIdx": max_idx,
                    "count": count,
                },
            )
            return data if isinstance(data, dict) else {}

        return await self._retry(call)

    async def get_chapter_info(self, book_id: str) -> dict[str, dict[str, Any]]:
        async def call() -> dict[str, dict[str, Any]]:
            await self._visit_homepage()
            await self.get_notebook_list()
            await asyncio.sleep((1000 + random.randint(0, 2000)) / 1000)

            wr_vid_match = re.search(r"wr_vid=([^;]+)", self.cookie)
            wr_skey_match = re.search(r"wr_skey=([^;]+)", self.cookie)
            wr_vid = wr_vid_match.group(1) if wr_vid_match else ""
            wr_skey = wr_skey_match.group(1) if wr_skey_match else ""
            _ = (wr_vid, wr_skey)

            headers = {
                "Cookie": self.cookie,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://weread.qq.com",
                "Referer": f"https://weread.qq.com/web/reader/{book_id}",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            params = {"_": int(time.time() * 1000)}

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.post(
                    WEREAD_CHAPTER_INFO_URL,
                    params=params,
                    headers=headers,
                    json={"bookIds": [book_id]},
                )
            response.raise_for_status()
            data = response.json()

            update: list[dict[str, Any]] | None = None
            if (
                isinstance(data, dict)
                and isinstance(data.get("data"), list)
                and len(data["data"]) == 1
                and isinstance(data["data"][0], dict)
                and isinstance(data["data"][0].get("updated"), list)
            ):
                update = [i for i in data["data"][0]["updated"] if isinstance(i, dict)]
            elif isinstance(data, dict) and isinstance(data.get("updated"), list):
                update = [i for i in data["updated"] if isinstance(i, dict)]
            elif (
                isinstance(data, list)
                and data
                and isinstance(data[0], dict)
                and isinstance(data[0].get("updated"), list)
            ):
                update = [i for i in data[0]["updated"] if isinstance(i, dict)]
            elif isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("chapterUid"):
                update = [i for i in data if isinstance(i, dict)]

            if update is not None:
                update.append(
                    {
                        "chapterUid": 1000000,
                        "chapterIdx": 1000000,
                        "updateTime": 1683825006,
                        "readAhead": 0,
                        "title": "\u70b9\u8bc4",
                        "level": 1,
                    }
                )
                result: dict[str, dict[str, Any]] = {}
                for chapter in update:
                    uid = chapter.get("chapterUid")
                    if uid is None:
                        continue
                    result[str(uid)] = chapter
                return result

            if isinstance(data, dict):
                if data.get("errCode") is not None:
                    code = _to_int(data.get("errCode"), 0)
                    self._handle_errcode(code)
                    err_msg = data.get("errMsg") or "Unknown error"
                    raise RuntimeError(f"API returned error: {err_msg} (code: {code})")
                if data.get("errcode") is not None:
                    code = _to_int(data.get("errcode"), 0)
                    self._handle_errcode(code)
                    err_msg = data.get("errmsg") or "Unknown error"
                    raise RuntimeError(f"API returned error: {err_msg} (code: {code})")

            raise RuntimeError("Failed to get chapter info, unexpected response format.")

        return await self._retry(call)

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
            self.initialized = False
