import unittest
from datetime import datetime, timezone

from scraper.sources.zakupki import ZakupkiSource


class SourceConfigurationTests(unittest.TestCase):
    def test_zakupki_cursor_is_applied_as_portal_date_filter(self):
        source = ZakupkiSource(
            fetcher=None,
            list_url="https://zakupki.gov.ru/epz/order/extendedsearch/results.html?foo=bar",
        )

        url = source._start_url(datetime(2026, 8, 18, 12, tzinfo=timezone.utc))

        self.assertIn("foo=bar", url)
        self.assertIn("publishDateFrom=18.08.2026", url)
        self.assertIn("publishDateTo=", url)


if __name__ == "__main__":
    unittest.main()
