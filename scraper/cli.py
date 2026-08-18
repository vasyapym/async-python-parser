from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .attachments import LocalAttachmentStore
from .config import Settings
from .parsing import parse_datetime
from .repository import PostgresRepository
from .runner import ScrapeRunner
from .sources import FourGlazaSource, ZakupkiSource
from .transport import build_fetcher


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luna-scraper",
        description="Уважительный асинхронный сборщик публичных закупок и карточек товаров",
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--transport", choices=("auto", "httpx", "playwright"), default=None)
    parser.add_argument("--concurrency", type=_positive_int, default=None)
    parser.add_argument("--attachments-dir", type=Path, default=None)
    parser.add_argument("--max-attachment-bytes", type=_positive_int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--min-delay", type=float, default=None)
    parser.add_argument("--jitter", type=float, default=None)

    commands = parser.add_subparsers(dest="command", required=True)
    init_db = commands.add_parser("init-db", help="создать таблицы PostgreSQL")
    init_db.set_defaults(command="init-db")

    run = commands.add_parser("run", help="найти записи источника и сохранить результат")
    run.add_argument("--source", choices=("zakupki", "fourglaza", "all"), default="all")
    run.add_argument(
        "--published-after",
        help="дата UTC/ISO или ДД.ММ.ГГГГ; начальная дата для источников с курсором публикации",
    )
    run.add_argument("--limit", type=_positive_int, default=None)
    run.add_argument(
        "--full",
        action="store_true",
        help="игнорировать сохранённую контрольную точку; идентификаторы остаются идемпотентными",
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    values: Dict[str, Any] = {
        "database_url": args.database_url,
    }
    if not values["database_url"]:
        raise ValueError("требуется DATABASE_URL; передайте --database-url или экспортируйте DATABASE_URL")
    settings = Settings.from_env(database_url=values["database_url"])
    overrides: Dict[str, Any] = {}
    for name in (
        "transport",
        "concurrency",
        "attachments_dir",
        "max_attachment_bytes",
        "timeout",
        "min_delay",
        "jitter",
    ):
        value = getattr(args, name, None)
        if value is not None:
            target = "request_timeout" if name == "timeout" else name
            overrides[target] = value
    return replace(settings, **overrides)


def _parse_since(value: Optional[str]) -> Optional[Any]:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError("не удалось разобрать --published-after: " + value)
    return parsed


def _sources(fetcher: Any, settings: Settings, source_name: str) -> List[Any]:
    candidates = {
        "zakupki": ZakupkiSource(
            fetcher,
            list_url=settings.zakupki_list_url,
            max_pages=settings.zakupki_max_pages,
        ),
        "fourglaza": FourGlazaSource(fetcher, list_url=settings.fourglaza_list_url),
    }
    if source_name == "all":
        return [candidates["zakupki"], candidates["fourglaza"]]
    return [candidates[source_name]]


async def _run_command(args: argparse.Namespace) -> int:
    settings = _settings(args)
    repository = PostgresRepository(settings.database_url, max_size=settings.concurrency)
    await repository.initialize()
    if args.command == "init-db":
        await repository.close()
        print("Схема PostgreSQL готова")
        return 0

    since = _parse_since(args.published_after)
    fetcher = build_fetcher(
        settings.transport,
        user_agent=settings.user_agent,
        timeout=settings.request_timeout,
        concurrency=settings.concurrency,
        min_delay=settings.min_delay,
        jitter=settings.jitter,
    )
    attachment_store = LocalAttachmentStore(
        fetcher,
        settings.attachments_dir,
        max_bytes=settings.max_attachment_bytes,
    )
    summaries = []
    try:
        async with fetcher:
            for source in _sources(fetcher, settings, args.source):
                runner = ScrapeRunner(
                    source,
                    repository,
                    attachment_store,
                    concurrency=settings.concurrency,
                )
                summaries.append(
                    await runner.run(
                        since=since,
                        limit=args.limit,
                        full=args.full,
                    )
                )
    finally:
        await repository.close()

    payload = (
        summaries[0].as_dict()
        if len(summaries) == 1
        else {
            "source": "all",
            "discovered": sum(item.discovered for item in summaries),
            "processed": sum(item.processed for item in summaries),
            "inserted": sum(item.inserted for item in summaries),
            "updated": sum(item.updated for item in summaries),
            "attachments_downloaded": sum(item.attachments_downloaded for item in summaries),
            "errors": [error for item in summaries for error in item.as_dict()["errors"]],
            "sources": [item.as_dict() for item in summaries],
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(item.ok for item in summaries) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_command(args))
    except KeyboardInterrupt:
        print("Выполнение прервано", file=sys.stderr)
        return 130
    except Exception as error:
        print("ошибка парсера: " + str(error), file=sys.stderr)
        return 2
