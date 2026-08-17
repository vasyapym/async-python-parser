from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar
from urllib.parse import urlsplit

from .ports import DownloadResponse, FetchedResponse


class FetchError(RuntimeError):
    """Base class for transport failures."""


class BlockedFetchError(FetchError):
    """The origin explicitly rejected automation or rate exceeded."""


class PermanentFetchError(FetchError):
    """A non-retryable HTTP or input error."""


class RetryExhaustedError(FetchError):
    """A transient failure remained after the configured retry budget."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 12.0
    jitter: float = 0.35


class HostRateLimiter:
    """Serialize requests per host and reserve a quiet interval between them."""

    def __init__(self, min_delay: float = 1.5, jitter: float = 0.75) -> None:
        self.min_delay = max(0.0, min_delay)
        self.jitter = max(0.0, jitter)
        self._next_allowed: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def wait(self, url: str) -> None:
        host = urlsplit(url).netloc.lower() or "default"
        async with self._guard:
            lock = self._locks.get(host)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[host] = lock

        async with lock:
            now = time.monotonic()
            scheduled = max(now, self._next_allowed.get(host, now))
            delay = scheduled - now
            self._next_allowed[host] = scheduled + self.min_delay + random.uniform(0, self.jitter)
        if delay > 0:
            await asyncio.sleep(delay)


T = TypeVar("T")


class HttpxFetcher:
    """Async HTTP transport with bounded concurrency, retries, and streaming downloads."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 30.0,
        concurrency: int = 5,
        rate_limiter: Optional[HostRateLimiter] = None,
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._rate_limiter = rate_limiter or HostRateLimiter()
        self._retry_policy = retry_policy
        self._client: Any = None

    async def __aenter__(self) -> "HttpxFetcher":
        if self._client is None:
            try:
                import httpx
            except ImportError as error:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "httpx is required; install dependencies with pip install -e ."
                ) from error
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "User-Agent": self.user_agent,
                    "Accept-Language": "ru,en;q=0.8",
                },
            )
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str) -> FetchedResponse:
        return await self._retry(lambda: self._fetch_once(url), "GET " + url)

    async def _fetch_once(self, url: str) -> FetchedResponse:
        if self._client is None:
            raise RuntimeError("Fetcher must be used inside an async context manager")
        async with self._semaphore:
            await self._rate_limiter.wait(url)
            try:
                response = await self._client.get(url)
            except Exception as error:
                raise FetchError("request failed: " + str(error)) from error
            self._raise_for_status(response.status_code, url)
            return FetchedResponse(
                status_code=response.status_code,
                url=str(response.url),
                content=response.content,
                headers=dict(response.headers),
            )

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> DownloadResponse:
        return await self._retry(
            lambda: self._download_once(url, destination, max_bytes=max_bytes),
            "download " + url,
        )

    async def _download_once(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> DownloadResponse:
        if self._client is None:
            raise RuntimeError("Fetcher must be used inside an async context manager")
        async with self._semaphore:
            await self._rate_limiter.wait(url)
            try:
                async with self._client.stream("GET", url) as response:
                    self._raise_for_status(response.status_code, url)
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        raise PermanentFetchError(
                            "attachment exceeds the configured size limit: " + url
                        )
                    written = 0
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            written += len(chunk)
                            if written > max_bytes:
                                raise PermanentFetchError(
                                    "attachment exceeds the configured size limit: " + url
                                )
                            output.write(chunk)
                    return DownloadResponse(
                        content_type=response.headers.get("content-type"),
                        size=written,
                    )
            except PermanentFetchError:
                raise
            except BlockedFetchError:
                raise
            except Exception as error:
                raise FetchError("download failed: " + str(error)) from error

    async def _retry(self, operation: Callable[[], Awaitable[T]], label: str) -> T:
        last_error: Optional[Exception] = None
        attempts = max(1, self._retry_policy.attempts)
        for attempt in range(attempts):
            try:
                return await operation()
            except (BlockedFetchError, PermanentFetchError):
                raise
            except Exception as error:
                last_error = error
                if attempt + 1 >= attempts:
                    break
                delay = min(
                    self._retry_policy.max_delay,
                    self._retry_policy.base_delay * (2**attempt),
                )
                delay += random.uniform(0, self._retry_policy.jitter)
                await asyncio.sleep(delay)
        raise RetryExhaustedError(
            "{} failed after {} attempts: {}".format(label, attempts, last_error)
        )

    @staticmethod
    def _raise_for_status(status_code: int, url: str) -> None:
        if status_code in (401, 403, 429):
            raise BlockedFetchError(
                "origin rejected the request (HTTP {}): {}".format(status_code, url)
            )
        if status_code == 408 or status_code == 425 or status_code >= 500:
            raise FetchError("transient HTTP {}: {}".format(status_code, url))
        if status_code >= 400:
            raise PermanentFetchError("HTTP {}: {}".format(status_code, url))


class PlaywrightFetcher:
    """Browser transport used only when an HTTP response is blocked or JS is required."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 30.0,
        concurrency: int = 2,
        rate_limiter: Optional[HostRateLimiter] = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = int(timeout * 1000)
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._rate_limiter = rate_limiter or HostRateLimiter(min_delay=2.0, jitter=1.0)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def __aenter__(self) -> "PlaywrightFetcher":
        if self._context is not None:
            return self
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "Playwright is required for browser fallback; install dependencies and run "
                "playwright install chromium"
            ) from error
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=self.user_agent)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def fetch(self, url: str) -> FetchedResponse:
        if self._context is None:
            await self.__aenter__()
        async with self._semaphore:
            await self._rate_limiter.wait(url)
            page = await self._context.new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                status_code = response.status if response is not None else 200
                HttpxFetcher._raise_for_status(status_code, url)
                return FetchedResponse(
                    status_code=status_code,
                    url=page.url,
                    content=(await page.content()).encode("utf-8"),
                    headers={},
                )
            except (BlockedFetchError, PermanentFetchError, FetchError):
                raise
            except Exception as error:
                raise FetchError("browser request failed: " + str(error)) from error
            finally:
                await page.close()

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> DownloadResponse:
        if self._context is None:
            await self.__aenter__()
        async with self._semaphore:
            await self._rate_limiter.wait(url)
            try:
                response = await self._context.request.get(url, timeout=self.timeout)
                HttpxFetcher._raise_for_status(response.status, url)
                content = await response.body()
                if len(content) > max_bytes:
                    raise PermanentFetchError(
                        "attachment exceeds the configured size limit: " + url
                    )
                destination.write_bytes(content)
                return DownloadResponse(
                    content_type=response.headers.get("content-type"),
                    size=len(content),
                )
            except (BlockedFetchError, PermanentFetchError, FetchError):
                raise
            except Exception as error:
                raise FetchError("browser download failed: " + str(error)) from error


class FallbackFetcher:
    """Keep the cheap HTTP path as the default and open a browser only when needed."""

    def __init__(self, primary: HttpxFetcher, fallback: PlaywrightFetcher) -> None:
        self.primary = primary
        self.fallback = fallback
        self._fallback_started = False

    async def __aenter__(self) -> "FallbackFetcher":
        await self.primary.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.primary.aclose()
        if self._fallback_started:
            await self.fallback.aclose()

    async def _use_fallback(self) -> None:
        if not self._fallback_started:
            await self.fallback.__aenter__()
            self._fallback_started = True

    async def fetch(self, url: str) -> FetchedResponse:
        try:
            return await self.primary.fetch(url)
        except BlockedFetchError as first_error:
            try:
                await self._use_fallback()
                return await self.fallback.fetch(url)
            except Exception as fallback_error:
                raise BlockedFetchError(
                    "HTTP and browser transports were rejected for {}: {}; {}".format(
                        url, first_error, fallback_error
                    )
                ) from fallback_error

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> DownloadResponse:
        try:
            return await self.primary.download(url, destination, max_bytes=max_bytes)
        except BlockedFetchError:
            await self._use_fallback()
            return await self.fallback.download(url, destination, max_bytes=max_bytes)


def build_fetcher(
    mode: str,
    *,
    user_agent: str,
    timeout: float,
    concurrency: int,
    min_delay: float,
    jitter: float,
) -> Any:
    limiter = HostRateLimiter(min_delay=min_delay, jitter=jitter)
    http = HttpxFetcher(
        user_agent=user_agent,
        timeout=timeout,
        concurrency=concurrency,
        rate_limiter=limiter,
    )
    if mode == "httpx":
        return http
    browser = PlaywrightFetcher(
        user_agent=user_agent,
        timeout=timeout,
        concurrency=max(1, min(concurrency, 2)),
        rate_limiter=HostRateLimiter(min_delay=max(2.0, min_delay), jitter=max(1.0, jitter)),
    )
    if mode == "playwright":
        return browser
    if mode == "auto":
        return FallbackFetcher(http, browser)
    raise ValueError("unknown transport: " + mode)
