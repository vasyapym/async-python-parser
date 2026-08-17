from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


class ListingKind(str, Enum):
    """The two source domains supported by the CLI."""

    PURCHASE = "purchase"
    PRODUCT = "product"


@dataclass(frozen=True)
class Attachment:
    """A remote file linked from a listing detail page."""

    url: str
    filename: str
    content_type: Optional[str] = None
    source_id: Optional[str] = None


@dataclass(frozen=True)
class ListingRef:
    """The small record discovered on a source index before detail fetching."""

    external_id: str
    url: str
    published_at: Optional[datetime] = None
    title_hint: Optional[str] = None


@dataclass(frozen=True)
class Listing:
    """Normalized data persisted for either a purchase or a product."""

    source: str
    kind: ListingKind
    external_id: str
    title: str
    url: str
    number: Optional[str] = None
    description: Optional[str] = None
    customer_name: Optional[str] = None
    status: Optional[str] = None
    published_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    raw_payload: Mapping[str, Any] = field(default_factory=dict)
    attachments: Tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class DownloadedAttachment:
    """The result of an atomic attachment download."""

    source_url: str
    filename: str
    content_type: Optional[str]
    size: int
    sha256: str
    local_path: str
    status: str = "downloaded"
    error: Optional[str] = None


@dataclass(frozen=True)
class RunError:
    """A non-fatal error associated with one source record or attachment."""

    external_id: Optional[str]
    stage: str
    message: str


@dataclass
class RunSummary:
    """Observable outcome of one source pass."""

    source: str
    discovered: int = 0
    processed: int = 0
    inserted: int = 0
    updated: int = 0
    attachments_downloaded: int = 0
    errors: List[RunError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "discovered": self.discovered,
            "processed": self.processed,
            "inserted": self.inserted,
            "updated": self.updated,
            "attachments_downloaded": self.attachments_downloaded,
            "errors": [
                {
                    "external_id": error.external_id,
                    "stage": error.stage,
                    "message": error.message,
                }
                for error in self.errors
            ],
        }
