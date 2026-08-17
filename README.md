# Асинхронный парсер закупок и товаров

Async CLI на Python для сбора публичных закупок и карточек товаров.

## Возможности

- bounded `asyncio` concurrency, HTTPX, retry/backoff и per-host rate limit;
- Playwright fallback для страниц, требующих браузерный transport;
- адаптеры `zakupki.gov.ru` (`purchase`) и `4glaza.ru` (`product`);
- PostgreSQL metadata/checkpoints через `asyncpg`;
- безопасное скачивание вложений с SHA-256 и atomic rename;
- offline HTML fixtures, local transport smoke test и opt-in PostgreSQL integration test.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
export DATABASE_URL='postgresql://scraper:scraper@localhost:5432/scraper'
python -m scraper init-db
python -m scraper run --source zakupki --published-after 2026-08-01T00:00:00Z --limit 50
```

Подробная документация находится в [`scraper/README.md`](scraper/README.md). Перед live-запуском проверьте `robots.txt`, условия использования источника и используйте только разрешённые URL. Скрапер не обходит CAPTCHA и другие access controls.

## Проверки

```bash
python -m pytest
ruff check scraper tests
ruff format --check scraper tests
```

PostgreSQL integration test запускается при заданном `TEST_DATABASE_URL` для отдельной тестовой базы.
