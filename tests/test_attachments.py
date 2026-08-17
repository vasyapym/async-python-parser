import tempfile
import unittest
from pathlib import Path

from scraper.attachments import LocalAttachmentStore
from scraper.domain import Attachment
from scraper.ports import DownloadResponse


class FakeFetcher:
    def __init__(self):
        self.calls = 0

    async def download(self, url, destination: Path, *, max_bytes: int):
        del url
        self.calls += 1
        content = b"hello scraper"
        self.assert_size(content, max_bytes)
        destination.write_bytes(content)
        return DownloadResponse(content_type="application/pdf", size=len(content))

    @staticmethod
    def assert_size(content, max_bytes):
        if len(content) > max_bytes:
            raise RuntimeError("too large")


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_is_atomic_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            fetcher = FakeFetcher()
            store = LocalAttachmentStore(fetcher, Path(temporary), max_bytes=1024)
            attachment = Attachment("https://example.test/../../secret.pdf", "../../secret.pdf")

            first = await store.save("example/source", "../listing", attachment)
            second = await store.save("example/source", "../listing", attachment)

            self.assertEqual(fetcher.calls, 1)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.size, len(b"hello scraper"))
            self.assertTrue(Path(first.local_path).exists())
            self.assertNotIn("..", Path(first.local_path).name)
            self.assertFalse(list(Path(temporary).rglob("*.part")))


if __name__ == "__main__":
    unittest.main()
