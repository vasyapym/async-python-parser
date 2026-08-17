from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .domain import ListingRef, RunError, RunSummary
from .ports import AttachmentStorePort, ListingRepository, SourceAdapter


@dataclass
class _ProcessOutcome:
    processed: int = 0
    inserted: int = 0
    updated: int = 0
    attachments_downloaded: int = 0
    latest_published_at: Optional[datetime] = None
    errors: List[RunError] = field(default_factory=list)


class ScrapeRunner:
    """Deep orchestration module; source and storage details stay behind ports."""

    def __init__(
        self,
        source: SourceAdapter,
        repository: ListingRepository,
        attachment_store: AttachmentStorePort,
        *,
        concurrency: int = 5,
    ) -> None:
        self.source = source
        self.repository = repository
        self.attachment_store = attachment_store
        self.concurrency = max(1, concurrency)

    async def run(
        self,
        *,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
        full: bool = False,
    ) -> RunSummary:
        summary = RunSummary(source=self.source.name)
        effective_since = since
        if effective_since is None and not full:
            effective_since = await self.repository.get_checkpoint(self.source.name)

        references: List[ListingRef] = []
        seen = set()
        try:
            async for reference in self.source.discover(
                since=effective_since,
                limit=limit,
                full=full,
            ):
                if reference.external_id in seen:
                    continue
                seen.add(reference.external_id)
                references.append(reference)
        except Exception as error:
            summary.errors.append(RunError(None, "discover", str(error)))
            return summary

        summary.discovered = len(references)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(reference: ListingRef) -> _ProcessOutcome:
            async with semaphore:
                return await self._process_one(reference)

        outcomes = await asyncio.gather(*(process(reference) for reference in references))
        latest_published_at: Optional[datetime] = None
        for outcome in outcomes:
            summary.processed += outcome.processed
            summary.inserted += outcome.inserted
            summary.updated += outcome.updated
            summary.attachments_downloaded += outcome.attachments_downloaded
            summary.errors.extend(outcome.errors)
            if outcome.latest_published_at is not None:
                if latest_published_at is None or outcome.latest_published_at > latest_published_at:
                    latest_published_at = outcome.latest_published_at

        if not summary.errors and latest_published_at is not None:
            await self.repository.save_checkpoint(self.source.name, latest_published_at)
        return summary

    async def _process_one(self, reference: ListingRef) -> _ProcessOutcome:
        outcome = _ProcessOutcome()
        try:
            listing = await self.source.fetch_detail(reference)
        except Exception as error:
            outcome.errors.append(RunError(reference.external_id, "detail", str(error)))
            return outcome

        try:
            listing_id, inserted = await self.repository.upsert_listing(listing)
        except Exception as error:
            outcome.errors.append(RunError(listing.external_id, "database", str(error)))
            return outcome

        outcome.processed = 1
        if inserted:
            outcome.inserted = 1
        else:
            outcome.updated = 1
        outcome.latest_published_at = listing.published_at

        attachment_urls = set()
        for attachment in listing.attachments:
            if attachment.url in attachment_urls:
                continue
            attachment_urls.add(attachment.url)
            try:
                downloaded = await self.attachment_store.save(
                    listing.source,
                    listing.external_id,
                    attachment,
                )
                await self.repository.save_attachment(listing_id, downloaded)
                outcome.attachments_downloaded += 1
            except Exception as error:
                message = str(error)
                try:
                    await self.repository.record_attachment_failure(
                        listing_id,
                        attachment,
                        message,
                    )
                except Exception as database_error:
                    message += "; could not record failure: " + str(database_error)
                outcome.errors.append(RunError(listing.external_id, "attachment", message))
        return outcome
