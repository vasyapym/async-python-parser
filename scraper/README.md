# Асинхронный сборщик закупок и карточек товаров

Самостоятельный Python CLI, который собирает публичные закупки с `zakupki.gov.ru` и карточки товаров с `4glaza.ru`. Он находит ссылки в каталогах, загружает подробные страницы, приводит данные к общей модели, сохраняет результат в PostgreSQL и скачивает найденные вложения в локальную папку.

Парсер не обходит CAPTCHA, прокси-блокировки и другие средства защиты. Перед live-запуском проверьте `robots.txt`, правила сайта, условия использования и разрешённые URL.

## Требования

- Python 3.9 или новее;
- PostgreSQL 13 или новее;
- сеть для обращения к источникам;
- Chromium, только если нужен транспорт Playwright.

PostgreSQL должен быть установлен и запущен до выполнения `init-db`. Команда `init-db` создаёт таблицы в уже существующей базе, но не устанавливает PostgreSQL и не создаёт пользователя или саму базу.

## Установка

Перейдите в каталог репозитория и создайте виртуальное окружение:

```bash
cd async-python-parser
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
python -m pip install -e ".[dev]"
```

Установка включает:

- `asyncpg` — подключение к PostgreSQL;
- `httpx` — асинхронные HTTP-запросы;
- `beautifulsoup4` — разбор HTML;
- `playwright` — браузерный транспорт;
- `pytest`, `pytest-asyncio` и `ruff` — проверки проекта.

Chromium для Playwright устанавливается отдельно:

```bash
python -m playwright install chromium
```

Он нужен при явном `--transport playwright` или когда режим `auto` переключается на браузер после HTTP-блокировки. Для первого запуска через обычный HTTP этот шаг можно пропустить.

## PostgreSQL

Укажите строку подключения в `DATABASE_URL`:

```bash
export DATABASE_URL='postgresql://scraper:scraper@localhost:5432/scraper'
```

Используйте свои имя пользователя, пароль, хост, порт и имя базы, если они отличаются от примера. Значение можно передать и через CLI:

```bash
python -m scraper \
  --database-url 'postgresql://user:password@localhost:5432/database' \
  init-db
```

Docker — только один из способов запустить PostgreSQL. Если Docker уже установлен, можно использовать локальный контейнер:

```bash
docker run \
  --name async-parser-postgres \
  -e POSTGRES_USER=scraper \
  -e POSTGRES_PASSWORD=scraper \
  -e POSTGRES_DB=scraper \
  -p 5432:5432 \
  -d postgres:16
```

После запуска PostgreSQL создайте таблицы:

```bash
python -m scraper init-db
```

Ожидаемый вывод:

```text
Схема PostgreSQL готова
```

Инициализация создаёт три таблицы:

- `listings` — нормализованные закупки и товары;
- `attachments` — сведения о вложениях и ошибках скачивания;
- `scraper_checkpoints` — дата последнего успешного прохода по источнику.

## Запуск

Посмотреть все параметры можно так:

```bash
python -m scraper --help
python -m scraper run --help
```

Глобальные параметры нужно указывать перед подкомандой `run`. Ограничения источника (`--source`, `--limit`, `--full`) указываются после неё.

### Небольшой запуск товаров

```bash
python -m scraper \
  --transport httpx \
  --concurrency 2 \
  --min-delay 2 \
  run --source fourglaza --limit 3
```

### Запуск закупок с начальной датой

```bash
python -m scraper \
  --transport auto \
  --concurrency 2 \
  --min-delay 2 \
  run --source zakupki \
  --published-after 2026-08-01T00:00:00Z \
  --limit 5
```

`--published-after` принимает ISO-дату или дату в формате `ДД.ММ.ГГГГ`. Для первого запуска лучше указывать дату и небольшой `--limit`, чтобы не обходить большой диапазон.

### Оба источника

```bash
python -m scraper \
  --transport httpx \
  --concurrency 2 \
  run --source all --limit 5
```

Источники обрабатываются последовательно, а записи внутри каждого источника — параллельно с ограничением. `--limit 5` применяется к каждому источнику отдельно.

### Полный проход

```bash
python -m scraper \
  --transport httpx \
  run --source fourglaza --full --limit 100
```

`--full` игнорирует сохранённый checkpoint. Записи всё равно проходят через upsert по `(source, external_id)`, поэтому повторный запуск не создаёт дубликаты.

После установки проекта можно использовать эквивалентную консольную команду:

```bash
luna-scraper --transport httpx run --source fourglaza --limit 5
```

## Результат запуска

CLI печатает JSON-сводку, а не полный набор записей:

```json
{
  "source": "4glaza.ru",
  "discovered": 3,
  "processed": 3,
  "inserted": 3,
  "updated": 0,
  "attachments_downloaded": 3,
  "errors": []
}
```

Поля означают:

- `discovered` — ссылки, найденные на страницах списка;
- `processed` — успешно загруженные подробные страницы;
- `inserted` — новые строки в `listings`;
- `updated` — обновлённые существующие строки;
- `attachments_downloaded` — успешно сохранённые файлы;
- `errors` — ошибки по отдельным записям или вложениям.

При `--source all` сводка дополнительно содержит общие счётчики и массив `sources` с отдельным результатом каждого источника.

Чтобы сохранить сводку в файл и одновременно видеть её в терминале:

```bash
python -m scraper \
  --transport httpx \
  run --source fourglaza --limit 3 \
  | tee run-summary.json
```

Коды завершения:

- `0` — запуск завершён без ошибок;
- `1` — записи обработаны, но возникли ошибки;
- `2` — ошибка конфигурации или запуска;
- `130` — выполнение прервано через Ctrl+C.

## Как посмотреть записи

Установите `psql` и выполните запрос к той же базе, что указана в `DATABASE_URL`:

```bash
psql "$DATABASE_URL" -c "
SELECT
  id,
  source,
  kind,
  external_id,
  title,
  price,
  currency,
  published_at,
  url
FROM listings
ORDER BY id DESC
LIMIT 20;
"
```

Количество записей по источникам:

```bash
psql "$DATABASE_URL" -c "
SELECT source, kind, COUNT(*)
FROM listings
GROUP BY source, kind
ORDER BY source, kind;
"
```

Исходная полезная нагрузка записи хранится в `raw_payload`:

```bash
psql "$DATABASE_URL" -c "
SELECT jsonb_pretty(raw_payload)
FROM listings
ORDER BY id DESC
LIMIT 1;
"
```

Информация о вложениях:

```bash
psql "$DATABASE_URL" -c "
SELECT
  listing_id,
  filename,
  status,
  size,
  sha256,
  local_path,
  error
FROM attachments
ORDER BY id DESC
LIMIT 20;
"
```

Checkpoint:

```bash
psql "$DATABASE_URL" -c "SELECT * FROM scraper_checkpoints ORDER BY source;"
```

## Где лежат вложения

По умолчанию файлы сохраняются в:

```text
./data/attachments/
```

Путь считается относительно каталога, из которого запущен CLI. Пример структуры:

```text
data/attachments/
├── zakupki_gov_ru/
│   └── 123456789/
│       └── a1b2c3d4e5f6-document.pdf
└── 4glaza_ru/
    └── telescope-123/
        └── 98ab76cd-image.jpg
```

Каждый файл сначала записывается во временный `.part`-файл. После проверки размера и SHA-256 он атомарно переименовывается в итоговый путь. Компоненты пути очищаются от traversal-символов.

Размер одного файла ограничен 25 MiB. Изменить папку и лимит можно так:

```bash
python -m scraper \
  --attachments-dir ./data/attachments \
  --max-attachment-bytes 10485760 \
  run --source fourglaza --limit 5
```

## Как работает конвейер

1. `cli.py` читает аргументы и переменные окружения.
2. `PostgresRepository` создаёт пул соединений и проверяет схему.
3. Адаптер источника загружает страницу списка и выдаёт ссылки `ListingRef`.
4. `ScrapeRunner` удаляет дубликаты внешних ID.
5. Подробные страницы обрабатываются асинхронно с ограничением concurrency.
6. Адаптер извлекает поля из HTML, таблиц, подписей и JSON-LD.
7. Нормализованный `Listing` сохраняется через PostgreSQL upsert.
8. Найденные вложения скачиваются и записываются в `attachments`.
9. Если ошибок нет, для источника сохраняется самая новая дата публикации.
10. CLI печатает `RunSummary` и возвращает код завершения.

Источники и хранилище подключены через небольшие интерфейсы из `ports.py`. Поэтому runner не знает HTML-селекторов сайтов, а тесты могут подставлять in-memory источник, репозиторий и загрузчик файлов.

## Источники

### `zakupki.gov.ru`

Адаптер использует страницу расширенного поиска:

```text
https://zakupki.gov.ru/epz/order/extendedsearch/results.html
```

Он:

- добавляет `publishDateFrom` в запрос, если задан checkpoint или `--published-after`;
- переходит по страницам до `ZAKUPKI_MAX_PAGES`;
- останавливается, когда записи страницы старше локальной границы;
- извлекает номер, название, описание, заказчика, статус, цену и даты;
- ищет документы по расширению и тексту ссылки.

### `4glaza.ru`

По умолчанию используется каталог:

```text
https://4glaza.ru/katalog/teleskopy/
```

Адаптер:

- ищет ссылки `/products/`;
- извлекает название, SKU, цену и наличие из JSON-LD и HTML;
- использует артикул или URL как стабильный идентификатор;
- собирает изображения и ссылки на инструкции или документы;
- не полагается на дату публикации: у каталога нет надёжного cursor.

URL списков можно заменить через `ZAKUPKI_LIST_URL` и `FOURGLAZA_LIST_URL`.

## Транспорт и ограничения запросов

Доступны три режима:

- `httpx` — обычные асинхронные HTTP-запросы;
- `playwright` — headless Chromium;
- `auto` — сначала HTTPX, затем Playwright после блокировки HTTP-транспорта.

HTTP-транспорт использует:

- ограничение общей concurrency;
- отдельную задержку для каждого host;
- retry для временных ошибок;
- потоковое скачивание файлов;
- ограничение размера вложения.

HTTP 401, 403 и 429 считаются блокировкой. Ошибки 408, 425 и 5xx могут быть повторены. Остальные ошибки 4xx считаются постоянными.

Режим `auto` переключается на Playwright только после блокировки HTTP-транспорта. Если сайт отдаёт HTTP 200, но требует JavaScript для содержимого, используйте `--transport playwright` явно.

## Настройки

Все параметры можно задать через окружение или соответствующие CLI-флаги:

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `DATABASE_URL` | — | строка подключения к PostgreSQL |
| `ATTACHMENTS_DIR` | `./data/attachments` | папка для файлов |
| `SCRAPER_TRANSPORT` | `auto` | `auto`, `httpx` или `playwright` |
| `SCRAPER_CONCURRENCY` | `5` | число параллельных операций |
| `SCRAPER_TIMEOUT` | `30` | timeout запроса в секундах |
| `SCRAPER_MIN_DELAY` | `1.5` | базовая задержка между запросами к host |
| `SCRAPER_JITTER` | `0.75` | случайная добавка к задержке |
| `MAX_ATTACHMENT_BYTES` | `26214400` | максимальный размер файла |
| `SCRAPER_USER_AGENT` | `LunaResearchScraper/0.1 ...` | HTTP User-Agent |
| `ZAKUPKI_LIST_URL` | URL портала | страница списка закупок |
| `ZAKUPKI_MAX_PAGES` | `100` | лимит страниц закупок |
| `FOURGLAZA_LIST_URL` | каталог телескопов | страница списка товаров |

Файл `scraper/.env.example` содержит шаблон параметров. Файл `.env` автоматически не загружается: переменные нужно экспортировать или передать окружению процесса самостоятельно.

## Проверки

Обычные проверки не требуют доступа к реальным сайтам:

```bash
python -m unittest discover -s tests -v
python -m pytest
ruff check scraper tests
ruff format --check scraper tests
```

Fixtures находятся в `tests/fixtures/`. PostgreSQL integration-проверка запускается только при наличии `TEST_DATABASE_URL` и отдельной тестовой базы. Это позволяет проверять repository, не смешивая тестовые данные с рабочей базой.

## Диагностика

### `DATABASE_URL is required`

Задайте переменную перед запуском:

```bash
export DATABASE_URL='postgresql://user:password@localhost:5432/database'
```

### `connection refused` или `no response`

PostgreSQL не запущен, недоступен на `localhost:5432` или указан неверный порт. Проверить сервер можно командой:

```bash
pg_isready -h localhost -p 5432
```

### Ошибка Playwright

Установите Chromium:

```bash
python -m playwright install chromium
```

Или временно используйте `--transport httpx`.

### HTTP 403, 429 или CAPTCHA

Не увеличивайте concurrency и не пытайтесь обходить блокировку. Проверьте правила источника, уменьшите скорость запросов и при необходимости остановите запуск.

### В базе нет строк

Проверьте JSON-сводку, `errors`, `--source`, `--limit`, дату `--published-after` и доступность страницы. Если HTML строится JavaScript, попробуйте Playwright после установки Chromium.
