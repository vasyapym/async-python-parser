from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Mapping, Optional, Protocol, Tuple

from .domain import (
    Attachment,
    DownloadedAttachment,
    Listing,
    ListingRef,
)


@dataclass(frozen=True)
class FetchedResponse:
    """A transport-neutral HTTP/browser response."""

    status_code: int
    url: str
    content: bytes
    headers: Mapping[str, str]


@dataclass(frozen=True)
class DownloadResponse:
    """Metadata returned after a transport streams a file to disk."""

    content_type: Optional[str]
    size: int


class Fetcher(Protocol):
    """The transport seam used by adapters and the attachment store."""

    async def fetch(self, url: str) -> FetchedResponse: ...

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> DownloadResponse: ...

    async def __aenter__(self) -> "Fetcher": ...

    async def __aexit__(self, exc_type, exc_value, traceback) -> None: ...


class SourceAdapter(Protocol):
    """A source-specific adapter with discovery and detail parsing behind one seam."""

    name: str
    kind: str

    async def discover(
        self,
        *,
        since: Optional[datetime],
        limit: Optional[int],
        full: bool,
    ) -> AsyncIterator[ListingRef]: ...

    async def fetch_detail(self, reference: ListingRef) -> Listing: ...


class ListingRepository(Protocol):
    """Persistence port shared by PostgreSQL and in-memory test adapters."""

    async def initialize(self) -> None: ...

    async def get_checkpoint(self, source: str) -> Optional[datetime]: ...

    async def save_checkpoint(self, source: str, value: datetime) -> None: ...

    async def upsert_listing(self, listing: Listing) -> Tuple[int, bool]:
        """
        Persist a normalized listing and return (database_id, inserted).
        """
        ...

    async def save_attachment(
        self,
        listing_id: int,
        attachment: DownloadedAttachment,
    ) -> None: ...

    async def record_attachment_failure(
        self,
        listing_id: int,
        attachment: Attachment,
        error: str,
    ) -> None: ...

    async def close(self) -> None: ...


class AttachmentStorePort(Protocol):
    """Storage port for safe, atomic local attachment writes."""

    async def save(
        self,
        source: str,
        external_id: str,
        attachment: Attachment,
    ) -> DownloadedAttachment: ...
