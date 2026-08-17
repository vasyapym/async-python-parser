import unittest
from datetime import timezone
from decimal import Decimal

from scraper.parsing import (
    absolute_url,
    clean_text,
    filename_from_url,
    find_labeled_value,
    parse_datetime,
    parse_decimal,
    safe_filename,
)


class ParsingTests(unittest.TestCase):
    def test_parse_russian_money(self):
        self.assertEqual(parse_decimal("1 234 567,89 ₽"), Decimal("1234567.89"))
        self.assertEqual(parse_decimal("12.50 EUR"), Decimal("12.50"))

    def test_parse_dates_as_utc(self):
        value = parse_datetime("18.08.2026 14:30")
        self.assertIsNotNone(value)
        self.assertEqual(value.tzinfo, timezone.utc)
        self.assertEqual(value.hour, 14)
        self.assertEqual(parse_datetime("2026-08-18T14:30:00+03:00").hour, 11)

    def test_html_text_and_labels(self):
        self.assertEqual(clean_text("  hello\n  world "), "hello world")
        self.assertEqual(
            find_labeled_value("Заказчик: ООО Ромашка Статус: Опубликована", ("Заказчик",)),
            "ООО Ромашка",
        )

    def test_urls_and_names_are_safe(self):
        self.assertEqual(
            absolute_url("https://example.test/catalog/", "/item/1"), "https://example.test/item/1"
        )
        self.assertEqual(
            filename_from_url("https://example.test/files/%D1%82%D0%B5%D1%81%D1%82.pdf"), "тест.pdf"
        )
        self.assertNotIn("..", safe_filename("../../secret.pdf"))
        self.assertNotIn("/", safe_filename("foo/bar.txt"))


if __name__ == "__main__":
    unittest.main()
