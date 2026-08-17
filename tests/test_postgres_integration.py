import os
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from scraper.domain import DownloadedAttachment, Listing, ListingKind
from scraper.repository import PostgresRepository

try:
    import asyncpg  # noqa: F401

    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(
    HAS_ASYNCPG and TEST_DATABASE_URL,
    "set TEST_DATABASE_URL and install asyncpg for PostgreSQL integration tests",
)
class PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_upsert_checkpoint_and_attachment_record(self):
        source = "test-review-" + uuid.uuid4().hex
        repository = PostgresRepository(TEST_DATABASE_URL, max_size=2)
        connection = None
        try:
            await repository.initialize()
            listing = Listing(
                source=source,
                kind=ListingKind.PURCHASE,
                external_id="purchase-1",
                number="purchase-1",
                title="Integration fixture",
                url="https://local.test/purchase-1",
                published_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
                price=Decimal("12.50"),
                raw_payload={"fixture": True},
            )
            listing_id, inserted = await repository.upsert_listing(listing)
            repeated_id, repeated_inserted = await repository.upsert_listing(listing)
            self.assertTrue(inserted)
            self.assertFalse(repeated_inserted)
            self.assertEqual(listing_id, repeated_id)

            checkpoint = listing.published_at
            await repository.save_checkpoint(source, checkpoint)
            self.assertEqual(await repository.get_checkpoint(source), checkpoint)
            await repository.save_attachment(
                listing_id,
                DownloadedAttachment(
                    source_url="https://local.test/file.pdf",
                    filename="file.pdf",
                    content_type="application/pdf",
                    size=4,
                    sha256="abcd",
                    local_path="data/file.pdf",
                ),
            )

            connection = await asyncpg.connect(TEST_DATABASE_URL)
            listing_count = await connection.fetchval(
                "SELECT COUNT(*) FROM listings WHERE source = $1", source
            )
            attachment_count = await connection.fetchval(
                """
                SELECT COUNT(*) FROM attachments a
                JOIN listings l ON l.id = a.listing_id
                WHERE l.source = $1
                """,
                source,
            )
            self.assertEqual(listing_count, 1)
            self.assertEqual(attachment_count, 1)
        finally:
            await repository.close()
            if connection is None:
                connection = await asyncpg.connect(TEST_DATABASE_URL)
            await connection.execute(
                "DELETE FROM attachments "
                "WHERE listing_id IN (SELECT id FROM listings WHERE source = $1)",
                source,
            )
            await connection.execute("DELETE FROM listings WHERE source = $1", source)
            await connection.execute("DELETE FROM scraper_checkpoints WHERE source = $1", source)
            await connection.close()


if __name__ == "__main__":
    unittest.main()
