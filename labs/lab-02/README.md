# ЛР2: Поисковый робот

Обкачка корпуса «кино и сериалы» с двух источников: ru.wikipedia.org и kino-teatr.ru.

## Состав

| Файл | Что это |
|---|---|
| `crawler.py` | робот обкачки |
| `recrawler.py` | переобкачка: ищет изменившиеся документы и ставит в очередь |
| `common.py` | общий код: конфиг, хранилище, выкачка, нормализация, разбор |
| `config.yaml` | конфигурация обеих программ |
| `report.tex`, `src/`, `styles/` | отчёт |

## Требования

Python 3.8+, пакеты `requests` и `PyYAML`:

```
pip3 install requests PyYAML
```

База — SQLite, ставить ничего не нужно (модуль `sqlite3` в стандартной библиотеке).

## Запуск

```
python3 crawler.py config.yaml      # обкачка
python3 recrawler.py config.yaml    # проверка изменений
```

Единственный аргумент обеих программ — путь до конфига.

Робота можно прервать по Ctrl+C: он допишет текущий документ и выйдет. При следующем
запуске работа продолжится с того места, где остановилась, — очередь URL хранится в базе.

## Конфигурация

| Секция | Что задаёт |
|---|---|
| `db` | путь до файла базы |
| `logic` | задержка между запросами, таймаут, User-Agent, соблюдение robots.txt, глубина обхода, минимальная длина текста |
| `recrawl` | сколько документов проверять за запуск и как часто их перепроверять |
| `sources` | источники: стартовые URL, контейнер основного текста и фильтры ссылок |

Фильтров ссылок в каждом источнике три, и они отвечают на разные вопросы:

- `allow` / `deny` — обходить ли эту ссылку вообще;
- `follow_only` — собирать ли с этой страницы ссылки (иначе робот уходит из тематики);
- `store_allow` / `store_deny` — сохранять ли страницу как документ (каталоги и списки
  нужны только ради ссылок).

## Что лежит в базе

```
documents  url, source, raw_html, text, title, encoding,
           content_hash, etag, last_modified, crawled_at, checked_at, revision
frontier   url, source, depth, status, priority, attempts, added_at, error
```

Посмотреть содержимое:

```
python3 -c "import sqlite3; db=sqlite3.connect('crawler.db'); \
print(db.execute('SELECT COUNT(*), SUM(LENGTH(raw_html)), SUM(LENGTH(text)) FROM documents').fetchone())"
```

## Сборка отчёта

```
make pdf
```
