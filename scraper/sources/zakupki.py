from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from ..domain import Attachment, Listing, ListingKind, ListingRef
from ..html import (
    content_type_for,
    first_attr,
    first_node,
    first_text,
    labeled_value,
    parse_html,
    text_of,
)
from ..parsing import (
    absolute_url,
    clean_text,
    filename_from_url,
    find_labeled_value,
    parse_datetime,
    parse_decimal,
    unique_strings,
)
from ..ports import Fetcher

_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".txt")
_DATE_PATTERN = re.compile(r"\b\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b")


class ZakupkiSource:
    """Adapter for the public procurement registry at zakupki.gov.ru."""

    name = "zakupki.gov.ru"
    kind = ListingKind.PURCHASE.value

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        list_url: str = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
        max_pages: int = 100,
    ) -> None:
        self.fetcher = fetcher
        self.list_url = list_url
        self.max_pages = max(1, max_pages)

    async def discover(
        self,
        *,
        since: Optional[datetime],
        limit: Optional[int],
        full: bool,
    ) -> AsyncIterator[ListingRef]:
        current_url: Optional[str] = self._start_url(since if not full else None)
        visited: Set[str] = set()
        emitted: Set[str] = set()
        pages = 0
        while current_url and current_url not in visited and pages < self.max_pages:
            visited.add(current_url)
            pages += 1
            response = await self.fetcher.fetch(current_url)
            soup = parse_html(response.content)
            references = list(self._parse_references(soup, current_url))
            if not references:
                return
            for reference in references:
                if reference.external_id in emitted:
                    continue
                if not full and since is not None and reference.published_at is not None:
                    if reference.published_at < since:
                        continue
                emitted.add(reference.external_id)
                yield reference
                if limit is not None and len(emitted) >= limit:
                    return
            if (
                not full
                and since is not None
                and all(
                    reference.published_at is not None and reference.published_at < since
                    for reference in references
                )
            ):
                return
            current_url = self._next_page(soup, current_url)

    async def fetch_detail(self, reference: ListingRef) -> Listing:
        response = await self.fetcher.fetch(reference.url)
        soup = parse_html(response.content)
        page_text = text_of(soup)
        number = (
            first_text(soup, (".registry-entry__header-mid__number", '[class*="notice-number"]'))
            or labeled_value(soup, ("Номер извещения", "Номер закупки"))
            or reference.external_id
        )
        number = clean_text(number).lstrip("№ ")
        title = (
            first_text(soup, (".registry-entry__header-mid__title", "h1", ".notice-title"))
            or reference.title_hint
            or "Закупка " + number
        )
        description = first_attr(soup, ('meta[name="description"]',), "content") or first_text(
            soup, (".common-text", ".notice-description", '[class*="description"]')
        )
        customer_labels = (
            "Заказчик",
            "Наименование заказчика",
            "Организация, осуществляющая закупку",
        )
        status_labels = ("Статус закупки", "Состояние")
        price_labels = ("Начальная цена", "НМЦК", "Начальная (максимальная) цена контракта")
        deadline_labels = ("Окончание подачи заявок", "Дата и время окончания подачи заявок")
        published_labels = ("Дата размещения", "Размещено", "Дата публикации")
        customer = labeled_value(soup, customer_labels) or find_labeled_value(
            page_text, customer_labels
        )
        status = labeled_value(soup, status_labels) or find_labeled_value(page_text, status_labels)
        price_text = labeled_value(soup, price_labels) or find_labeled_value(
            page_text, price_labels
        )
        deadline_text = labeled_value(soup, deadline_labels) or find_labeled_value(
            page_text, deadline_labels
        )
        published_text = labeled_value(soup, published_labels) or find_labeled_value(
            page_text, published_labels
        )
        price = parse_decimal(price_text)
        currency = "RUB" if price is not None or "руб" in page_text.lower() else None

        return Listing(
            source=self.name,
            kind=ListingKind.PURCHASE,
            external_id=number,
            number=number,
            title=title,
            description=description or None,
            customer_name=customer,
            status=status,
            published_at=self._parse_date(published_text, page_text),
            deadline_at=parse_datetime(deadline_text),
            price=price,
            currency=currency,
            url=reference.url,
            raw_payload={
                "source": self.name,
                "number": number,
                "title": title,
                "customer": customer,
                "status": status,
            },
            attachments=tuple(self._attachments(soup, reference.url)),
        )

    @classmethod
    def _parse_references(cls, soup: Any, base_url: str) -> Iterable[ListingRef]:
        entries = soup.select(".registry-entry__form, .registry-entry, .registry-entry__body")
        if not entries:
            entries = soup.select('a[href*="/epz/order/notice/"]')
        seen: Set[str] = set()
        for entry in entries:
            link = first_node(entry, ('a[href*="/epz/order/notice/"]',))
            if link is None and getattr(entry, "name", None) == "a":
                link = entry
            if link is None:
                continue
            url = absolute_url(base_url, link.get("href"))
            if not url:
                continue
            entry_text = text_of(entry)
            number = first_text(
                entry,
                (".registry-entry__header-mid__number", '[class*="notice-number"]'),
            ) or find_labeled_value(entry_text, ("Номер извещения", "Номер закупки"))
            if not number:
                number = cls._url_id(url)
            number = clean_text(number).lstrip("№ ")
            if number in seen:
                continue
            seen.add(number)
            published_labels = ("Дата размещения", "Размещено", "Дата публикации")
            published = cls._parse_date(
                labeled_value(entry, published_labels)
                or find_labeled_value(entry_text, published_labels),
                entry_text,
            )
            yield ListingRef(
                external_id=number,
                url=url,
                published_at=published,
                title_hint=first_text(entry, (".registry-entry__header-mid__title",)) or None,
            )

    def _start_url(self, since: Optional[datetime]) -> str:
        """Apply the portal's publication-date filter while keeping the local cutoff."""

        if since is None:
            return self.list_url
        parsed = urlsplit(self.list_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["publishDateFrom"] = since.astimezone(timezone.utc).strftime("%d.%m.%Y")
        query.setdefault("publishDateTo", "")
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    @staticmethod
    def _url_id(url: str) -> str:
        parts = [part for part in urlparse(url).path.split("/") if part]
        return parts[-1] if parts else url

    @staticmethod
    def _parse_date(labeled_value: Optional[str], fallback_text: str) -> Optional[datetime]:
        candidate = labeled_value
        if not candidate:
            match = _DATE_PATTERN.search(fallback_text)
            candidate = match.group(0) if match else None
        return parse_datetime(candidate)

    @staticmethod
    def _next_page(soup: Any, current_url: str) -> Optional[str]:
        candidates = list(soup.select('a[rel="next"], a.next, a.pagination-next'))
        if not candidates:
            candidates = [
                node
                for node in soup.select("a[href]")
                if clean_text(node.get_text(" ", strip=True)).lower()
                in {"следующая", "далее", "next", ">", "→"}
            ]
        for node in candidates:
            value = absolute_url(current_url, node.get("href"))
            if value and value != current_url:
                return value
        for node in soup.select('a[href*="PAGEN_"], a[href*="page="], a[href*="pageNumber"]'):
            value = absolute_url(current_url, node.get("href"))
            if (
                value
                and value != current_url
                and urlparse(value).path == urlparse(current_url).path
            ):
                return value
        return None

    @staticmethod
    def _attachments(soup: Any, base_url: str) -> Iterable[Attachment]:
        candidates: List[str] = []
        for node in soup.select("a[href], [data-href]"):
            href = node.get("href") or node.get("data-href")
            url = absolute_url(base_url, href)
            if not url:
                continue
            link_text = clean_text(node.get_text(" ", strip=True)).lower()
            path = urlparse(url).path.lower()
            if path.endswith(_DOCUMENT_EXTENSIONS) or any(
                marker in link_text for marker in ("скачать", "документ", "файл", "приложение")
            ):
                candidates.append(url)
        for url in unique_strings(candidates):
            filename = filename_from_url(url, fallback="procurement-document")
            yield Attachment(url=url, filename=filename, content_type=content_type_for(filename))
