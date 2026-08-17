import unittest
from datetime import datetime, timezone
from typing import Dict, List, Optional

from scraper.domain import (
    Attachment,
    DownloadedAttachment,
    Listing,
    ListingKind,
    ListingRef,
)
from scraper.runner import ScrapeRunner

STAMP = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


class FakeSource:
    name = "fake"
    kind = ListingKind.PURCHASE.value

    def __init__(self, fail_detail: bool = False, with_attachment: bool = False):
        self.fail_detail = fail_detail
        self.with_attachment = with_attachment
        self.references = [
            ListingRef("a", "https://example.test/a", published_at=STAMP),
            ListingRef("a", "https://example.test/a", published_at=STAMP),
            ListingRef("b", "https://example.test/b", published_at=STAMP),
        ]

    async def discover(self, *, since, limit, full):
        del since, limit, full
        for reference in self.references:
            yield reference

    async def fetch_detail(self, reference):
        if self.fail_detail and reference.external_id == "b":
            raise RuntimeError("detail unavailable")
        return Listing(
            source=self.name,
            kind=ListingKind.PURCHASE,
            external_id=reference.external_id,
            number=reference.external_id,
            title="Listing " + reference.external_id,
            url=reference.url,
            published_at=STAMP,
            attachments=(
                (Attachment("https://example.test/a.pdf", "a.pdf"),)
                if self.with_attachment and reference.external_id == "a"
                else ()
            ),
        )


class FakeRepository:
    def __init__(self):
        self.checkpoint: Optional[datetime] = None
        self.records: Dict[str, int] = {}
        self.attachment_failures: List[str] = []

    async def initialize(self):
        return None

    async def get_checkpoint(self, source):
        del source
        return self.checkpoint

    async def save_checkpoint(self, source, value):
        del source
        self.checkpoint = value

    async def upsert_listing(self, listing):
        inserted = listing.external_id not in self.records
        if inserted:
            self.records[listing.external_id] = len(self.records) + 1
        return self.records[listing.external_id], inserted

    async def save_attachment(self, listing_id, attachment):
        del listing_id, attachment

    async def record_attachment_failure(self, listing_id, attachment, error):
        del listing_id, attachment
        self.attachment_failures.append(error)

    async def close(self):
        return None


class FakeAttachmentStore:
    def __init__(self, fail=False):
        self.fail = fail

    async def save(self, source, external_id, attachment):
        del source, external_id
        if self.fail:
            raise RuntimeError("download failed")
        return DownloadedAttachment(
            source_url=attachment.url,
            filename=attachment.filename,
            content_type="application/pdf",
            size=3,
            sha256="abc",
            local_path="/tmp/a.pdf",
        )


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_and_saves_checkpoint(self):
        repository = FakeRepository()
        runner = ScrapeRunner(FakeSource(), repository, FakeAttachmentStore(), concurrency=2)

        summary = await runner.run()

        self.assertTrue(summary.ok)
        self.assertEqual(summary.discovered, 2)
        self.assertEqual(summary.processed, 2)
        self.assertEqual(summary.inserted, 2)
        self.assertEqual(repository.checkpoint, STAMP)

    async def test_partial_detail_failure_does_not_advance_checkpoint(self):
        repository = FakeRepository()
        runner = ScrapeRunner(
            FakeSource(fail_detail=True),
            repository,
            FakeAttachmentStore(),
            concurrency=2,
        )

        summary = await runner.run()

        self.assertFalse(summary.ok)
        self.assertEqual(summary.processed, 1)
        self.assertIsNone(repository.checkpoint)
        self.assertEqual(summary.errors[0].stage, "detail")

    async def test_attachment_failure_is_recorded_and_other_records_continue(self):
        repository = FakeRepository()
        runner = ScrapeRunner(
            FakeSource(with_attachment=True),
            repository,
            FakeAttachmentStore(fail=True),
            concurrency=2,
        )

        summary = await runner.run()

        self.assertFalse(summary.ok)
        self.assertEqual(summary.processed, 2)
        self.assertEqual(len(repository.attachment_failures), 1)
        self.assertIsNone(repository.checkpoint)


if __name__ == "__main__":
    unittest.main()
