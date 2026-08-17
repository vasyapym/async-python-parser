from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from .domain import Attachment, DownloadedAttachment
from .parsing import filename_from_url, safe_component, safe_filename
from .ports import AttachmentStorePort, Fetcher


class AttachmentDownloadError(RuntimeError):
    """A file could not be safely downloaded or finalized."""


class LocalAttachmentStore(AttachmentStorePort):
    """Filesystem adapter that never exposes a source URL as a writable path."""

    def __init__(
        self,
        fetcher: Fetcher,
        root: Path,
        *,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.fetcher = fetcher
        self.root = root
        self.max_bytes = max(1, max_bytes)

    async def save(
        self,
        source: str,
        external_id: str,
        attachment: Attachment,
    ) -> DownloadedAttachment:
        filename = safe_filename(attachment.filename or filename_from_url(attachment.url))
        source_dir = self.root / safe_component(source) / safe_component(external_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        url_token = hashlib.sha1(attachment.url.encode("utf-8")).hexdigest()[:12]
        destination = source_dir / (url_token + "-" + filename)

        if destination.exists():
            size, checksum = await asyncio.to_thread(self._file_digest, destination)
            return DownloadedAttachment(
                source_url=attachment.url,
                filename=filename,
                content_type=attachment.content_type,
                size=size,
                sha256=checksum,
                local_path=str(destination),
            )

        temporary = source_dir / ("." + destination.name + "." + uuid.uuid4().hex + ".part")
        try:
            response = await self.fetcher.download(
                attachment.url,
                temporary,
                max_bytes=self.max_bytes,
            )
            size, checksum = await asyncio.to_thread(self._file_digest, temporary)
            if size != response.size:
                raise AttachmentDownloadError(
                    "download size changed while finalizing " + attachment.url
                )
            os.replace(str(temporary), str(destination))
            return DownloadedAttachment(
                source_url=attachment.url,
                filename=filename,
                content_type=attachment.content_type or response.content_type,
                size=size,
                sha256=checksum,
                local_path=str(destination),
            )
        except Exception as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(error, AttachmentDownloadError):
                raise
            raise AttachmentDownloadError(str(error)) from error

    @staticmethod
    def _file_digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()
