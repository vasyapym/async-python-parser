import unittest
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from scraper.domain import ListingKind, ListingRef
from scraper.parsing import parse_datetime
from scraper.ports import FetchedResponse
from scraper.sources import FourGlazaSource
from scraper.sources.zakupki import ZakupkiSource

try:
    import bs4  # noqa: F401

    HAS_BEAUTIFULSOUP = True
except ImportError:
    HAS_BEAUTIFULSOUP = False


class FixtureFetcher:
    def __init__(self, content: bytes, responses: Optional[Dict[str, bytes]] = None):
        self.content = content
        self.responses = responses or {}
        self.urls = []

    async def fetch(self, url):
        self.urls.append(url)
        content = self.content
        for marker, response in self.responses.items():
            if marker in url:
                content = response
                break
        return FetchedResponse(200, url, content, {})


@unittest.skipUnless(HAS_BEAUTIFULSOUP, "install beautifulsoup4 to run HTML adapter tests")
class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fourglaza_discovery_extracts_unique_product_links(self):
        content = Path("tests/fixtures/fourglaza.html").read_bytes()
        source = FourGlazaSource(FixtureFetcher(content), list_url="https://4glaza.ru/katalog/")

        references = [
            reference async for reference in source.discover(since=None, limit=None, full=False)
        ]

        self.assertEqual(source.kind, ListingKind.PRODUCT.value)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].external_id, "sample-telescope")

    async def test_fourglaza_detail_extracts_sku_price_and_attachments(self):
        content = Path("tests/fixtures/fourglaza_detail.html").read_bytes()
        fetcher = FixtureFetcher(content)
        source = FourGlazaSource(fetcher)

        listing = await source.fetch_detail(
            ListingRef("sample-telescope", "https://4glaza.ru/products/sample-telescope/")
        )

        self.assertEqual(listing.external_id, "69299")
        self.assertEqual(listing.price, Decimal("6590"))
        self.assertEqual(listing.status, "in_stock")
        self.assertEqual(
            {attachment.url for attachment in listing.attachments},
            {
                "https://4glaza.ru/upload/sample.jpg",
                "https://4glaza.ru/upload/manual.pdf",
            },
        )

    async def test_zakupki_discovery_extracts_number_and_date(self):
        content = Path("tests/fixtures/zakupki.html").read_bytes()
        source = ZakupkiSource(
            FixtureFetcher(content),
            list_url="https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
        )

        references = [
            reference async for reference in source.discover(since=None, limit=None, full=False)
        ]

        self.assertEqual(source.kind, ListingKind.PURCHASE.value)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].external_id, "123456")
        self.assertIsNotNone(references[0].published_at)

    async def test_zakupki_detail_extracts_table_fields_and_attachment(self):
        content = Path("tests/fixtures/zakupki_detail.html").read_bytes()
        source = ZakupkiSource(
            FixtureFetcher(content),
            list_url="https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
        )

        listing = await source.fetch_detail(
            ListingRef("123456", "https://zakupki.gov.ru/epz/order/notice/123456")
        )

        self.assertEqual(listing.external_id, "123456")
        self.assertEqual(listing.customer_name, "ООО Ромашка")
        self.assertEqual(listing.status, "Опубликована")
        self.assertEqual(listing.price, Decimal("1234.50"))
        self.assertEqual(listing.published_at, parse_datetime("18.08.2026 12:30"))
        self.assertEqual(listing.deadline_at, parse_datetime("25.08.2026 12:30"))
        self.assertEqual(listing.attachments[0].url, "https://zakupki.gov.ru/docs/spec.pdf")

    async def test_zakupki_cursor_filters_query_and_stops_after_old_page(self):
        current = Path("tests/fixtures/zakupki.html").read_bytes()
        old = Path("tests/fixtures/zakupki_old.html").read_bytes()
        fetcher = FixtureFetcher(current, responses={"pageNumber=2": old})
        source = ZakupkiSource(
            fetcher,
            list_url="https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
            max_pages=5,
        )

        references = [
            reference
            async for reference in source.discover(
                since=parse_datetime("18.08.2026"),
                limit=None,
                full=False,
            )
        ]

        self.assertEqual([reference.external_id for reference in references], ["123456"])
        self.assertIn("publishDateFrom=18.08.2026", fetcher.urls[0])
        self.assertEqual(len(fetcher.urls), 2)
        self.assertEqual(urlparse(fetcher.urls[1]).query, "pageNumber=2")


if __name__ == "__main__":
    unittest.main()
