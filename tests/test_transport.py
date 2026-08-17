import asyncio
import tempfile
import unittest
from pathlib import Path

from scraper.ports import DownloadResponse
from scraper.transport import (
    BlockedFetchError,
    HostRateLimiter,
    HttpxFetcher,
    PermanentFetchError,
    RetryPolicy,
)

try:
    import httpx  # noqa: F401

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class TransportUnitTests(unittest.TestCase):
    def test_status_classification_surfaces_blocks(self):
        with self.assertRaises(BlockedFetchError):
            HttpxFetcher._raise_for_status(403, "https://example.test")
        with self.assertRaises(PermanentFetchError):
            HttpxFetcher._raise_for_status(404, "https://example.test")


@unittest.skipUnless(HAS_HTTPX, "install httpx to run the local transport smoke test")
class LocalTransportSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_and_stream_download_use_local_server_only(self):
        async def handler(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                body = b"local transport fixture"
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    + b"Content-Type: text/plain\r\n"
                    + ("Content-Length: %d\r\n" % len(body)).encode("ascii")
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            fetcher = HttpxFetcher(
                user_agent="test",
                concurrency=2,
                rate_limiter=HostRateLimiter(min_delay=0, jitter=0),
                retry_policy=RetryPolicy(attempts=1),
            )
            async with fetcher:
                response = await fetcher.fetch("http://127.0.0.1:%d/page" % port)
                self.assertEqual(response.content, b"local transport fixture")
                with tempfile.TemporaryDirectory() as temporary:
                    destination = Path(temporary) / "attachment.bin"
                    result = await fetcher.download(
                        "http://127.0.0.1:%d/attachment" % port,
                        destination,
                        max_bytes=1024,
                    )
                    self.assertIsInstance(result, DownloadResponse)
                    self.assertEqual(destination.read_bytes(), b"local transport fixture")
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
