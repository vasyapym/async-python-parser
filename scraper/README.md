# Async procurement / product scraper

Самостоятельный Python CLI для сбора публичных закупок и карточек товаров. Внутри есть два адаптера:

- `zakupki.gov.ru` — нормализует публичные закупки как `kind=purchase`;
- `4glaza.ru` — нормализует карточки товаров как `kind=product`.

Общий async-движок не знает HTML-селекторов источника. Источник реализует небольшой интерфейс discovery/detail, а PostgreSQL и локальное файловое хранилище подключаются через отдельные адаптеры. Поэтому второй источник не требует копирования оркестратора.

> Скрапер не пытается обходить CAPTCHA, прокси-блокировки или иные средства защиты. Он использует ограничение скорости, случайную задержку и retry только для временных ошибок. Перед запуском нужно проверить правила сайта, `robots.txt` и условия использования.

## Установка

Требуется Python 3.9+ и PostgreSQL 13+.

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
python -m pip install -e ".[dev]"

# Нужно только для --transport playwright или auto после HTTP 403:
playwright install chromium
```

Задайте подключение к PostgreSQL:

```bash
export DATABASE_URL='postgresql://scraper:scraper@localhost:5432/scraper'
python -m scraper init-db
```

Если PostgreSQL запущен локально с другим пользователем/портом, измените `DATABASE_URL`. Все остальные настройки перечислены в `.env.example`; файл `.env` автоматически не читается, переменные нужно экспортировать или передать через окружение запуска.

## Запуск

Первый запуск закупок с явным курсором:

```bash
python -m scraper run --source zakupki \
  --published-after 2026-08-01T00:00:00Z \
  --limit 50
```

Повторный запуск использует checkpoint из PostgreSQL:

```bash
python -m scraper run --source zakupki --limit 50
```

Каталог товаров:

```bash
python -m scraper run --source fourglaza --limit 20
```

Оба источника последовательно в одном CLI-вызове:

```bash
python -m scraper run --source all --limit 20
```

Полный обход игнорирует сохранённый checkpoint, но всё равно выполняет upsert по `(source, external_id)`:

```bash
python -m scraper run --source fourglaza --full --limit 100
```

Полезные параметры можно передать до подкоманды:

```bash
python -m scraper \
  --transport httpx \
  --concurrency 5 \
  --min-delay 1.5 \
  --jitter 0.75 \
  --attachments-dir ./data/attachments \
  run --source all --limit 25
```

`auto` сначала использует HTTPX и лениво запускает Playwright только после HTTP-блокировки. `httpx` удобнее для обычного запуска; `playwright` нужен для страниц, которые действительно отрисовывают данные только JavaScript-ом.

CLI печатает JSON-сводку. Ошибка отдельной закупки или вложения не прекращает остальные задачи; при наличии ошибок процесс завершается с кодом `1`, чтобы это было видно CI/планировщику.

## Что сохраняется

Инициализация создаёт таблицы:

- `listings` — источник, тип, внешний ID, номер/артикул, заголовок, описание, заказчик/продавец, статус, даты, цена, URL и исходной JSON-полезной нагрузкой;
- `attachments` — ссылка, безопасное имя, MIME, размер, SHA-256, локальный путь, статус и текст ошибки;
- `scraper_checkpoints` — последний успешно обработанный timestamp для источника.

Каждый файл пишется во временный `.part`-файл, проверяется по размеру и SHA-256, затем переименовывается атомарно. Путь не строится напрямую из URL: имя и компоненты источника/ID очищаются от traversal-символов. По умолчанию размер файла ограничен 25 MiB.

Для `zakupki.gov.ru` `--published-after` дополнительно передаётся в `publishDateFrom` запроса портала, а страницы идут до тех пор, пока на странице не останутся только записи старше checkpoint; `ZAKUPKI_MAX_PAGES` остаётся жёстким safety cap. Локальный cutoff сохраняется, поэтому записи на границе даты не теряются. Для `4glaza.ru` каталог не предоставляет надёжный publication cursor, поэтому discovery обходит каталог, а отсутствие/изменение записи определяется стабильным артикулом или URL и PostgreSQL upsert.

## Архитектура и тесты

Главный seam — `SourceAdapter`: `ZakupkiSource` и `FourGlazaSource` являются двумя реальными адаптерами одного интерфейса. `ScrapeRunner` скрывает параллелизм, deduplication, ошибки и checkpoint; тесты могут подставить in-memory source/repository/fetcher, не поднимая сеть и PostgreSQL.

Запуск тестов:

```bash
python -m unittest discover -s tests -v
# после pip install -e ".[dev]":
python -m pytest
```

Live HTML-источники могут менять разметку или быть временно недоступны; для этого парсер имеет offline fixtures. По умолчанию 4glaza использует категорию телескопов (`/katalog/teleskopy/`), потому что корень `/katalog/` является индексом категорий, а не списком карточек. Оба URL списка можно переопределить через `ZAKUPKI_LIST_URL` и `FOURGLAZA_LIST_URL`.
