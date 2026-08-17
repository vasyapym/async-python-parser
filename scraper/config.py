from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Settings:
    database_url: str
    attachments_dir: Path = Path("./data/attachments")
    user_agent: str = "LunaResearchScraper/0.1 (+respectful async crawler)"
    request_timeout: float = 30.0
    concurrency: int = 5
    min_delay: float = 1.5
    jitter: float = 0.75
    max_attachment_bytes: int = 25 * 1024 * 1024
    transport: str = "auto"
    zakupki_list_url: str = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"
    zakupki_max_pages: int = 100
    fourglaza_list_url: str = "https://4glaza.ru/katalog/teleskopy/"

    @classmethod
    def from_env(cls, database_url: Optional[str] = None) -> "Settings":
        database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not database_url:
            raise ValueError(
                "DATABASE_URL is required, for example postgresql://user:pass@localhost/db"
            )
        return cls(
            database_url=database_url,
            attachments_dir=Path(os.getenv("ATTACHMENTS_DIR", "./data/attachments")),
            user_agent=os.getenv(
                "SCRAPER_USER_AGENT",
                "LunaResearchScraper/0.1 (+respectful async crawler)",
            ),
            request_timeout=float(os.getenv("SCRAPER_TIMEOUT", "30")),
            concurrency=max(1, int(os.getenv("SCRAPER_CONCURRENCY", "5"))),
            min_delay=max(0.0, float(os.getenv("SCRAPER_MIN_DELAY", "1.5"))),
            jitter=max(0.0, float(os.getenv("SCRAPER_JITTER", "0.75"))),
            max_attachment_bytes=max(
                1,
                int(os.getenv("MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024))),
            ),
            transport=os.getenv("SCRAPER_TRANSPORT", "auto").lower(),
            zakupki_list_url=os.getenv(
                "ZAKUPKI_LIST_URL",
                "https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
            ),
            zakupki_max_pages=max(1, int(os.getenv("ZAKUPKI_MAX_PAGES", "100"))),
            fourglaza_list_url=os.getenv(
                "FOURGLAZA_LIST_URL",
                "https://4glaza.ru/katalog/teleskopy/",
            ),
        )
