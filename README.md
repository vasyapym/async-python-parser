# Асинхронный парсер закупок и товаров

Асинхронный Python CLI для сбора публичных закупок с `zakupki.gov.ru` и карточек товаров с `4glaza.ru`. Парсер нормализует данные, сохраняет их в PostgreSQL, скачивает вложения в локальное хранилище и печатает JSON-сводку запуска.

## Быстрый запуск

Нужны Python 3.9+, PostgreSQL 13+ и доступ к сети. PostgreSQL должен быть запущен до выполнения `init-db`; эта команда создаёт таблицы в уже существующей базе, но не устанавливает сам сервер и не создаёт базу данных.

```bash
cd async-python-parser
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

export DATABASE_URL='postgresql://scraper:scraper@localhost:5432/scraper'
python -m scraper init-db

# Первый небольшой запуск через обычные HTTP-запросы:
python -m scraper --transport httpx run --source fourglaza --limit 3
```

Результат запуска появится в терминале в формате JSON. Полные записи находятся в таблице `listings`, сведения о файлах — в `attachments`, а сами файлы по умолчанию сохраняются в `data/attachments/`. Подробная инструкция по PostgreSQL, параметрам и просмотру данных находится в [`scraper/README.md`](scraper/README.md).

## Возможности

- ограниченная параллельная обработка через `asyncio`;
- HTTPX, повторные попытки с backoff и задержка между запросами к одному хосту;
- Playwright как резервный транспорт для страниц, которым нужен браузер;
- адаптеры `zakupki.gov.ru` (`purchase`) и `4glaza.ru` (`product`);
- upsert по `(source, external_id)` без дублирования записей;
- PostgreSQL для нормализованных данных, исходного JSON и checkpoint;
- безопасное скачивание вложений с ограничением размера, SHA-256 и атомарным переименованием;
- offline fixtures и локальные проверки транспорта.

## Запуск

Глобальные параметры указываются перед подкомандой `run`:

```bash
# Закупки начиная с указанной даты:
python -m scraper \
  --transport auto \
  --concurrency 2 \
  --min-delay 2 \
  run --source zakupki \
  --published-after 2026-08-01T00:00:00Z \
  --limit 5

# Товары:
python -m scraper --transport httpx run --source fourglaza --limit 5

# Оба источника последовательно:
python -m scraper --transport httpx run --source all --limit 5
```

Доступны подкоманды:

- `init-db` — создать таблицы PostgreSQL;
- `run` — найти записи, загрузить подробные страницы и сохранить результат.

Параметр `--full` игнорирует сохранённый checkpoint. Идентификаторы всё равно обрабатываются идемпотентно, поэтому повторный запуск не создаёт дубликаты.

После установки доступна и консольная команда `luna-scraper`:

```bash
luna-scraper --transport httpx run --source fourglaza --limit 5
```

## Источники и ограничения

Адаптер закупок использует дату публикации, фильтр портала и постраничную навигацию. Адаптер товаров обходит каталог телескопов по умолчанию: у каталога нет надёжного cursor публикации, поэтому повторный обход определяется стабильным артикулом или URL и PostgreSQL upsert.

Парсер использует уважительные задержки и не пытается обходить CAPTCHA, блокировки или другие средства контроля доступа. Перед live-запуском проверьте `robots.txt`, условия использования сайта и разрешённые URL.

## Проверки

```bash
python -m unittest discover -s tests -v
python -m pytest
ruff check scraper tests
ruff format --check scraper tests
```

PostgreSQL integration-проверка запускается только при заданном `TEST_DATABASE_URL` для отдельной тестовой базы. HTML-проверки используют fixtures и не требуют обращения к реальным сайтам.

## Структура проекта

- [`scraper/README.md`](scraper/README.md) — подробное руководство пользователя;
- `scraper/cli.py` — аргументы командной строки и сборка запуска;
- `scraper/sources/` — адаптеры сайтов;
- `scraper/runner.py` — общий конвейер обнаружения, обработки и checkpoint;
- `scraper/repository.py` — PostgreSQL persistence;
- `scraper/attachments.py` — безопасное локальное хранилище файлов;
- `tests/` — offline fixtures и автоматические проверки.
