# ЛР2: Поисковый робот

Обкачка корпуса «кино и сериалы» с двух источников: ru.wikipedia.org (статьи о фильмах, сериалах и людях кино)
и film.ru (рецензии, подборки, интервью). Кино-Театр.Ру, исходный второй источник, закрыт от роботов
защитой ddos-guard и заменён на film.ru.

## Состав

```
lab-02/
├── config.yaml          конфигурация обеих программ
├── docker-compose.yml   MongoDB
├── Makefile             запуск, тесты, сборка отчёта
├── requirements.txt
├── src/
│   ├── crawler.py       робот обкачки (точка входа)
│   ├── recrawler.py     переобкачка: ищет изменившиеся документы и ставит в очередь
│   ├── stats.py         сводка по базе
│   ├── validate.py      проверка содержимого базы
│   ├── reparse.py       переразбор текста из сохранённого HTML без перекачки
│   ├── config.py        чтение конфига
│   ├── urls.py          нормализация URL
│   ├── parsing.py       кодировка, выделение текста и ссылок, отпечаток
│   ├── rules.py         фильтры источника: что обходить, откуда брать ссылки, что сохранять
│   ├── fetcher.py       HTTP: паузы вежливости, robots.txt, условные запросы
│   ├── storage.py       MongoDB: очередь URL и документы
│   └── shutdown.py      мягкая остановка по Ctrl+C / SIGTERM
├── tests/               автотесты, офлайн (unittest; mongomock или настоящая MongoDB)
└── report/              отчёт: LaTeX-исходники, PDF, лог автотестов
```

## Требования

Python 3.8+, Docker (для MongoDB).

```
make venv       # .venv с requests, PyYAML, pymongo, mongomock
make db-up      # MongoDB 7 в докере на 127.0.0.1:27017, данные в томе mongo-data
```

`make` сам берёт `.venv/bin/python`, если окружение создано.

## Запуск

Все команды — из каталога `lab-02`.

```
make crawl       # = python3 src/crawler.py config.yaml    обкачка
make recrawl     # = python3 src/recrawler.py config.yaml  проверка изменений
make stats       # = python3 src/stats.py config.yaml      что лежит в базе
make reparse     # заново выделить текст из сохранённого raw_html (после смены правил разбора), без перекачки
make validate    # проверка базы; make validate LIVE=5 -- ещё сверить 5 документов с сайтами
make db-shell    # mongosh в базе crawler
make test        # автотесты на mongomock: без сети и докера
make test-mongo  # те же тесты на настоящей MongoDB
make report      # сборка отчёта (нужен pdflatex)
make db-down     # остановить MongoDB
```

Единственный аргумент программ — путь до конфига; другой конфиг: `make crawl CONFIG=other.yaml`.
В `config.yaml` стоит `max_documents: 35000` — предел новых документов за один запуск (`0` — без предела).

Полная обкачка идёт часами, поэтому её удобно запускать отдельным процессом, не привязанным к терминалу:

```
setsid nohup make crawl >> crawl-full.log 2>&1 < /dev/null &
tail -f crawl-full.log            # ход обкачки
make stats                        # сколько уже в базе
pkill -INT -f src/crawler.py      # мягкая остановка, как Ctrl+C; потом можно запустить снова
```

Робота можно прервать по Ctrl+C: он допишет текущий документ и выйдет. При следующем
запуске работа продолжится с того места, где остановилась, — очередь URL хранится в базе.

## Переобкачка по расписанию

`make recrawl` сам по себе разовый: периодичность задаёт планировщик. Например, `crontab -e`:

```
0 3 * * *  cd ~/MAI-IR/labs/lab-02 && make recrawl
```

`recrawl.min_age_hours` не даст проверить документ чаще раза в сутки, `recrawl.batch` ограничивает
число запросов за запуск. Изменившиеся документы робот заберёт первыми при ближайшем `make crawl`.

## Конфигурация

| Секция | Что задаёт |
|---|---|
| `db` | адрес MongoDB (`uri`) и имя базы (`name`) |
| `logic` | задержка между запросами, таймаут, User-Agent, соблюдение robots.txt, глубина обхода, предел документов, минимальная длина текста |
| `recrawl` | сколько документов проверять за запуск и как часто их перепроверять |
| `sources` | источники: стартовые URL (`seeds` и шаблон страниц `seed_pages`), контейнер основного текста, хвостовые маркеры `cut_after`, фильтры ссылок, своя пауза, признак капчи |

`container` — один селектор или список: значение `class` (`"art article_text"`) или пара
`атрибут=значение` (`"itemprop=description"`). Берётся первый найденный на странице.

Фильтров ссылок в каждом источнике три, и они отвечают на разные вопросы:

- `allow` / `deny` — обходить ли эту ссылку вообще;
- `follow_only` — собирать ли с этой страницы ссылки (иначе робот уходит из тематики);
- `store_allow` / `store_deny` — сохранять ли страницу как документ (каталоги и списки
  нужны только ради ссылок).

## Что лежит в базе

```
documents  url (уникальный), source, raw_html, text, title, encoding, content_hash,
           etag, last_modified, crawled_at, checked_at, revision, raw_size, text_size
frontier   url (уникальный), source, depth, status, priority, attempts, added_at, error
```

## Демонстрация на защите

1. `make test-mongo` — 79 тестов, среди них остановка/продолжение и полный цикл переобкачки.
2. `make crawl`, через несколько документов Ctrl+C — робот допишет текущий документ
   и выведет итоги.
3. `make stats` — документы и очередь (`pending` — то, что осталось).
4. Снова `make crawl` — продолжит с того же места, дублей не будет.
5. Изменение документа имитируется подменой отпечатка в базе (`make db-shell`):
   ```
   db.documents.find({}, {url: 1}).limit(2).forEach(d => db.documents.updateOne({_id: d._id},
       {$set: {content_hash: "x", etag: null, last_modified: null, checked_at: 0}}))
   ```
   Затем `make recrawl` найдёт ровно эти два, `make crawl` перекачает их первыми, `revision` станет 2.
6. Посмотреть документ: `db.documents.findOne({}, {raw_html: 0})`.
7. `make validate LIVE=3` — все проверки базы плюс сверка трёх случайных документов с сайтами.
