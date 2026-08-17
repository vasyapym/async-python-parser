from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Tuple

from .domain import Attachment, DownloadedAttachment, Listing

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS listings (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('purchase', 'product')),
        external_id TEXT NOT NULL,
        number TEXT,
        title TEXT NOT NULL,
        description TEXT,
        customer_name TEXT,
        status TEXT,
        published_at TIMESTAMPTZ,
        deadline_at TIMESTAMPTZ,
        price NUMERIC(18, 2),
        currency TEXT,
        url TEXT NOT NULL,
        raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (source, external_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS listings_source_published_idx
        ON listings (source, published_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id BIGSERIAL PRIMARY KEY,
        listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
        source_url TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_type TEXT,
        size BIGINT,
        sha256 TEXT,
        local_path TEXT,
        status TEXT NOT NULL,
        error TEXT,
        downloaded_at TIMESTAMPTZ,
        UNIQUE (listing_id, source_url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scraper_checkpoints (
        source TEXT PRIMARY KEY,
        cursor_at TIMESTAMPTZ NOT NULL,
        saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


class PostgresRepository:
    """Deep persistence module: callers only know listing and checkpoint operations."""

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        self.database_url = database_url
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Any = None

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as error:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "asyncpg is required; install dependencies with pip install -e ."
                ) from error
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=self.min_size,
                max_size=self.max_size,
            )
        return self._pool

    async def initialize(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            for statement in SCHEMA_STATEMENTS:
                await connection.execute(statement)

    async def get_checkpoint(self, source: str) -> Optional[datetime]:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT cursor_at FROM scraper_checkpoints WHERE source = $1",
                source,
            )
        return value

    async def save_checkpoint(self, source: str, value: datetime) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO scraper_checkpoints (source, cursor_at)
                VALUES ($1, $2)
                ON CONFLICT (source) DO UPDATE
                    SET cursor_at = EXCLUDED.cursor_at, saved_at = NOW()
                """,
                source,
                value,
            )

    async def upsert_listing(self, listing: Listing) -> Tuple[int, bool]:
        pool = await self._ensure_pool()
        payload = json.dumps(dict(listing.raw_payload), ensure_ascii=False, default=str)
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO listings (
                    source, kind, external_id, number, title, description,
                    customer_name, status, published_at, deadline_at, price,
                    currency, url, raw_payload
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb
                )
                ON CONFLICT (source, external_id) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    number = EXCLUDED.number,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    customer_name = EXCLUDED.customer_name,
                    status = EXCLUDED.status,
                    published_at = EXCLUDED.published_at,
                    deadline_at = EXCLUDED.deadline_at,
                    price = EXCLUDED.price,
                    currency = EXCLUDED.currency,
                    url = EXCLUDED.url,
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = NOW()
                RETURNING id, (xmax = 0) AS inserted
                """,
                listing.source,
                listing.kind.value,
                listing.external_id,
                listing.number,
                listing.title,
                listing.description,
                listing.customer_name,
                listing.status,
                listing.published_at,
                listing.deadline_at,
                listing.price,
                listing.currency,
                listing.url,
                payload,
            )
        if row is None:  # pragma: no cover - PostgreSQL RETURNING always returns a row
            raise RuntimeError("PostgreSQL did not return the listing id")
        return int(row["id"]), bool(row["inserted"])

    async def save_attachment(
        self,
        listing_id: int,
        attachment: DownloadedAttachment,
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO attachments (
                    listing_id, source_url, filename, content_type, size,
                    sha256, local_path, status, error, downloaded_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                ON CONFLICT (listing_id, source_url) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    content_type = EXCLUDED.content_type,
                    size = EXCLUDED.size,
                    sha256 = EXCLUDED.sha256,
                    local_path = EXCLUDED.local_path,
                    status = EXCLUDED.status,
                    error = EXCLUDED.error,
                    downloaded_at = NOW()
                """,
                listing_id,
                attachment.source_url,
                attachment.filename,
                attachment.content_type,
                attachment.size,
                attachment.sha256,
                attachment.local_path,
                attachment.status,
                attachment.error,
            )

    async def record_attachment_failure(
        self,
        listing_id: int,
        attachment: Attachment,
        error: str,
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO attachments (
                    listing_id, source_url, filename, content_type, status, error
                )
                VALUES ($1, $2, $3, $4, 'failed', $5)
                ON CONFLICT (listing_id, source_url) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    content_type = EXCLUDED.content_type,
                    status = 'failed',
                    error = EXCLUDED.error
                """,
                listing_id,
                attachment.url,
                attachment.filename,
                attachment.content_type,
                error[:2000],
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
