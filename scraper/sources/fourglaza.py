from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Dict, Iterable, Optional, Set
from urllib.parse import urlparse

from ..domain import Attachment, Listing, ListingKind, ListingRef
from ..html import (
    content_type_for,
    first_attr,
    first_text,
    json_ld,
    labeled_value,
    parse_html,
    text_of,
)
from ..parsing import (
    absolute_url,
    clean_text,
    filename_from_url,
    parse_datetime,
    parse_decimal,
    unique_strings,
)
from ..ports import Fetcher

_PRODUCT_PATH = "/products/"
_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".txt")


class FourGlazaSource:
    """Adapter for the public 4glaza catalog; product records are kind=product."""

    name = "4glaza.ru"
    kind = ListingKind.PRODUCT.value

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        list_url: str = "https://4glaza.ru/katalog/teleskopy/",
        max_pages: int = 20,
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
        del full  # A catalog has no reliable publication cursor; the repository deduplicates IDs.
        current_url: Optional[str] = self.list_url
        visited: Set[str] = set()
        emitted: Set[str] = set()
        pages = 0
        while current_url and current_url not in visited and pages < self.max_pages:
            visited.add(current_url)
            pages += 1
            response = await self.fetcher.fetch(current_url)
            soup = parse_html(response.content)
            for reference in self._parse_references(soup, current_url):
                if reference.external_id in emitted:
                    continue
                if since is not None and reference.published_at is not None:
                    if reference.published_at < since:
                        continue
                emitted.add(reference.external_id)
                yield reference
                if limit is not None and len(emitted) >= limit:
                    return
            current_url = self._next_page(soup, current_url)

    async def fetch_detail(self, reference: ListingRef) -> Listing:
        response = await self.fetcher.fetch(reference.url)
        soup = parse_html(response.content)
        structured = self._product_json_ld(soup)
        page_text = text_of(soup)

        title = (
            clean_text(str(structured.get("name", "")))
            or first_text(soup, ("h1", '[itemprop="name"]', ".product-name", ".detail-title"))
            or reference.title_hint
            or reference.external_id
        )
        sku = (
            clean_text(str(structured.get("sku", "")))
            or first_text(soup, ('[itemprop="sku"]', ".article", ".product-article"))
            or labeled_value(soup, ("Артикул", "Код товара", "SKU"))
            or reference.external_id
        )
        description = (
            clean_text(str(structured.get("description", "")))
            or first_text(
                soup, ('[itemprop="description"]', ".product-description", ".description")
            )
            or first_attr(soup, ('meta[name="description"]',), "content")
        )
        offers = structured.get("offers") if isinstance(structured.get("offers"), dict) else {}
        price_value = str(offers.get("price", "")) if offers else ""
        price = (
            parse_decimal(price_value)
            or parse_decimal(
                labeled_value(soup, ("Цена", "Цена в интернет-магазине", "Цена онлайн"))
            )
            or parse_decimal(first_text(soup, ('[itemprop="price"]', ".price", '[class*="price"]')))
        )
        currency = (
            clean_text(str(offers.get("priceCurrency", "")))
            or first_attr(soup, ('[itemprop="priceCurrency"]',), "content")
            or ("RUB" if price is not None else None)
        )
        availability = (
            clean_text(str(offers.get("availability", "")))
            if offers
            else labeled_value(soup, ("Наличие", "Статус")) or ""
        )
        status = self._status(availability, page_text)
        published_at = parse_datetime(
            str(structured.get("datePublished", ""))
            or labeled_value(soup, ("Дата публикации", "Дата размещения"))
            or first_attr(soup, ('meta[property="article:published_time"]',), "content")
        )

        return Listing(
            source=self.name,
            kind=ListingKind.PRODUCT,
            external_id=sku,
            number=sku,
            title=title,
            description=description or None,
            customer_name="4glaza.ru",
            status=status,
            published_at=published_at,
            price=price,
            currency=currency,
            url=reference.url,
            raw_payload={
                "source": self.name,
                "json_ld": structured,
                "discovered_id": reference.external_id,
            },
            attachments=tuple(self._attachments(soup, reference.url)),
        )

    @staticmethod
    def _parse_references(soup: Any, base_url: str) -> Iterable[ListingRef]:
        seen: Set[str] = set()
        for link in soup.select('a[href*="/products/"]'):
            url = absolute_url(base_url, link.get("href"))
            if not url or urlparse(url).path.rstrip("/") == _PRODUCT_PATH.rstrip("/"):
                continue
            path_parts = [part for part in urlparse(url).path.split("/") if part]
            if not path_parts or "products" not in path_parts:
                continue
            external_id = path_parts[-1]
            if external_id in seen:
                continue
            seen.add(external_id)
            yield ListingRef(
                external_id=external_id,
                url=url,
                title_hint=text_of(link) or None,
            )

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
        for node in soup.select('a[href*="PAGEN_"], a[href*="page="]'):
            value = absolute_url(current_url, node.get("href"))
            if (
                value
                and value != current_url
                and urlparse(value).path == urlparse(current_url).path
            ):
                return value
        return None

    @staticmethod
    def _product_json_ld(soup: Any) -> Dict[str, Any]:
        for item in json_ld(soup):
            item_type = item.get("@type")
            if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
                return item
            graph = item.get("@graph")
            if isinstance(graph, list):
                for nested in graph:
                    if isinstance(nested, dict) and nested.get("@type") == "Product":
                        return nested
        return {}

    @staticmethod
    def _status(availability: str, page_text: str) -> Optional[str]:
        candidate = (availability + " " + page_text).lower()
        if "нет в наличии" in candidate:
            return "out_of_stock"
        if "в наличии" in candidate:
            return "in_stock"
        if "под заказ" in candidate:
            return "on_order"
        return availability or None

    @staticmethod
    def _attachments(soup: Any, base_url: str) -> Iterable[Attachment]:
        urls = []
        for node in soup.select(
            'meta[property="og:image"], img[itemprop="image"], .product-detail img'
        ):
            value = node.get("content") or node.get("data-src") or node.get("src")
            url = absolute_url(base_url, value)
            if url and urlparse(url).netloc == urlparse(base_url).netloc:
                urls.append(url)
        for node in soup.select("a[href]"):
            href = absolute_url(base_url, node.get("href"))
            if not href:
                continue
            link_text = clean_text(node.get_text(" ", strip=True)).lower()
            path = urlparse(href).path.lower()
            if path.endswith(_DOCUMENT_EXTENSIONS) or any(
                marker in link_text
                for marker in ("руководство", "инструкция", "фотографии", "(pdf)", "(zip)")
            ):
                urls.append(href)
        for url in unique_strings(urls):
            filename = filename_from_url(url, fallback="product-attachment")
            yield Attachment(url=url, filename=filename, content_type=content_type_for(filename))
