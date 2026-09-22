"""Общие компоненты поискового робота: конфиг, хранилище, выкачка, разбор.

Используется двумя программами:
  crawler.py   - обкачка документов;
  recrawler.py - проверка изменений у уже скачанных документов.
"""

import hashlib
import re
import sqlite3
import time
import urllib.parse
import urllib.robotparser

import requests
import yaml


# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

class Config:
    """Конфиг робота. Единственный аргумент программ -- путь до этого файла."""

    def __init__(self, path):
        with open(path, encoding='utf-8') as f:
            raw = yaml.safe_load(f)
        self.db = raw['db']
        self.logic = raw['logic']
        self.recrawl = raw.get('recrawl', {})
        self.sources = raw['sources']

    def source(self, name):
        for s in self.sources:
            if s['name'] == name:
                return s
        return None


# --------------------------------------------------------------------------
# Нормализация URL
# --------------------------------------------------------------------------

# Параметры, которые не влияют на содержимое страницы и только плодят дубли.
TRACKING_PARAMS = re.compile(r'^(utm_|openstat|gclid|yclid|fbclid|from$|ref$)')


def normalize_url(url, base=None):
    """Приводит URL к каноническому виду.

    Часть ссылок на странице относительные, поэтому нужен base. Помимо
    склейки убираем то, что не меняет содержимое: якорь, служебные параметры,
    регистр схемы и хоста, порт по умолчанию.
    """
    if base:
        url = urllib.parse.urljoin(base, url)

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        return None

    netloc = parts.netloc.lower()
    if netloc.endswith(':80') and parts.scheme == 'http':
        netloc = netloc[:-3]
    if netloc.endswith(':443') and parts.scheme == 'https':
        netloc = netloc[:-4]

    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not TRACKING_PARAMS.match(k)]
    query.sort()

    # Процентные последовательности раскрываем: /wiki/%D0%A4%D0%B8%D0%BB%D1%8C%D0%BC
    # и /wiki/Фильм -- один и тот же документ, и в базе он должен быть один.
    path = urllib.parse.unquote(parts.path)
    path = re.sub(r'/{2,}', '/', path) or '/'

    return urllib.parse.urlunsplit((
        parts.scheme.lower(), netloc, path,
        urllib.parse.urlencode(query), ''))     # фрагмент отбрасываем


# --------------------------------------------------------------------------
# Определение кодировки
# --------------------------------------------------------------------------

META_CHARSET = re.compile(rb'''<meta[^>]+charset=["']?\s*([\w-]+)''', re.I)


def decode(content, content_type):
    """Определяет кодировку в порядке, описанном на лекции.

    1) заголовок Content-Type, 2) <meta charset> в самом HTML, 3) UTF-8.
    Не пытаемся быть умнее браузера.
    """
    if content_type:
        m = re.search(r'charset=([\w-]+)', content_type, re.I)
        if m:
            try:
                return content.decode(m.group(1), errors='replace'), m.group(1).lower()
            except LookupError:
                pass

    m = META_CHARSET.search(content[:4096])
    if m:
        name = m.group(1).decode('ascii', errors='ignore')
        try:
            return content.decode(name, errors='replace'), name.lower()
        except LookupError:
            pass

    return content.decode('utf-8', errors='replace'), 'utf-8'


# --------------------------------------------------------------------------
# Разбор документа
# --------------------------------------------------------------------------

LINK = re.compile(r'''<a\s[^>]*href\s*=\s*["']([^"'>]+)["']''', re.I)
TITLE = re.compile(r'(?is)<title[^>]*>(.*?)</title>')
DROP_TAGS = re.compile(r'(?is)<(script|style|table|noscript)\b.*?</\1>')
TAG = re.compile(r'(?s)<[^>]+>')
ENTITY = re.compile(r'&(?:nbsp|#160);')


def extract_links(html, base_url):
    """Собирает со страницы нормализованные ссылки."""
    out = []
    seen = set()
    for href in LINK.findall(html):
        url = normalize_url(href, base_url)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_title(html):
    m = TITLE.search(html)
    return re.sub(r'\s+', ' ', TAG.sub('', m.group(1))).strip() if m else ''


def extract_text(html, source):
    """Выделяет полезный текст: только содержимое основного контейнера.

    Навигационная обвязка (меню, боковые панели, подвал) остаётся за границами
    контейнера и в текст не попадает.
    """
    container = source.get('container')
    body = html
    if container:
        m = re.search(r'(?s)<div class="' + re.escape(container) + r'"[^>]*>(.*)', html)
        if m:
            body = m.group(1)

    # Хвостовые разделы -- списки источников и внешних адресов, а не текст.
    for marker in source.get('cut_after', []):
        pos = body.find(marker)
        if pos > 0:
            body = body[:pos]

    body = DROP_TAGS.sub(' ', body)
    body = TAG.sub(' ', body)
    body = ENTITY.sub(' ', body)
    body = re.sub(r'&[a-zA-Z]+;|&#\d+;', '', body)
    return re.sub(r'\s+', ' ', body).strip()


def content_hash(text):
    """Отпечаток содержимого. По нему переобкачка понимает, что документ изменился."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    url           TEXT    NOT NULL UNIQUE,   -- нормализованный
    source        TEXT    NOT NULL,          -- название источника
    raw_html      BLOB    NOT NULL,          -- «сырой» html-текст документа
    text          TEXT    NOT NULL,          -- выделенный текст
    title         TEXT,
    encoding      TEXT,
    content_hash  TEXT    NOT NULL,          -- отпечаток текста
    etag          TEXT,
    last_modified TEXT,
    crawled_at    INTEGER NOT NULL,          -- unix timestamp обкачки
    checked_at    INTEGER,                   -- когда переобкачка проверяла
    revision      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS frontier (
    id         INTEGER PRIMARY KEY,
    url        TEXT    NOT NULL UNIQUE,
    source     TEXT    NOT NULL,
    depth      INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'pending',  -- pending|done|failed|skipped
    priority   INTEGER NOT NULL DEFAULT 0,          -- 1 -- очередь на переобкачку
    attempts   INTEGER NOT NULL DEFAULT 0,
    added_at   INTEGER NOT NULL,
    error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_frontier_next ON frontier(status, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_docs_checked  ON documents(checked_at);
"""


class Storage:
    """Очередь URL и хранилище документов в одной базе SQLite.

    Очередь лежит в базе, а не в памяти. Благодаря этому робота можно
    остановить в любой момент: при следующем запуске он продолжит с того
    документа, на котором прервался.
    """

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # --- очередь ---------------------------------------------------------

    def enqueue(self, url, source, depth=0, priority=0):
        """Кладёт URL в очередь. Дубликаты отсекаются на уровне UNIQUE."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO frontier (url, source, depth, priority, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (url, source, depth, priority, int(time.time())))
        return cur.rowcount > 0

    def requeue(self, url, source, priority=1):
        """Возвращает уже обойдённый URL в очередь -- для переобкачки."""
        self.db.execute(
            "INSERT INTO frontier (url, source, depth, priority, added_at, status)"
            " VALUES (?, ?, 0, ?, ?, 'pending')"
            " ON CONFLICT(url) DO UPDATE SET status='pending', priority=?, attempts=0",
            (url, source, priority, int(time.time()), priority))
        self.db.commit()

    def next_url(self, avoid_source=None):
        """Следующий URL из очереди.

        Сначала то, что ждёт переобкачки. Дальше стараемся взять источник,
        отличный от предыдущего: пока идёт запрос к одному хосту, для другого
        успевает истечь пауза вежливости, и оба источника наполняются
        равномерно, а не по очереди целыми сайтами.
        """
        if avoid_source:
            row = self.db.execute(
                "SELECT * FROM frontier WHERE status='pending' AND source<>?"
                " ORDER BY priority DESC, id LIMIT 1", (avoid_source,)).fetchone()
            if row is not None:
                return row
        return self.db.execute(
            "SELECT * FROM frontier WHERE status='pending'"
            " ORDER BY priority DESC, id LIMIT 1").fetchone()

    def mark(self, row_id, status, error=None):
        self.db.execute(
            "UPDATE frontier SET status=?, attempts=attempts+1, error=? WHERE id=?",
            (status, error, row_id))
        self.db.commit()

    def queue_stats(self):
        rows = self.db.execute(
            "SELECT status, COUNT(*) n FROM frontier GROUP BY status").fetchall()
        return {r['status']: r['n'] for r in rows}

    # --- документы -------------------------------------------------------

    def save(self, url, source, raw, text, title, encoding, etag, last_modified):
        """Сохраняет документ. Повторная обкачка обновляет запись и ревизию."""
        now = int(time.time())
        h = content_hash(text)
        old = self.db.execute(
            "SELECT id, content_hash, revision FROM documents WHERE url=?",
            (url,)).fetchone()

        if old is None:
            self.db.execute(
                "INSERT INTO documents (url, source, raw_html, text, title, encoding,"
                " content_hash, etag, last_modified, crawled_at, checked_at, revision)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                (url, source, raw, text, title, encoding, h, etag, last_modified, now, now))
            self.db.commit()
            return 'new'

        self.db.execute(
            "UPDATE documents SET raw_html=?, text=?, title=?, encoding=?,"
            " content_hash=?, etag=?, last_modified=?, crawled_at=?, checked_at=?,"
            " revision=revision+1 WHERE id=?",
            (raw, text, title, encoding, h, etag, last_modified, now, now, old['id']))
        self.db.commit()
        return 'updated' if old['content_hash'] != h else 'unchanged'

    def has(self, url):
        return self.db.execute(
            "SELECT 1 FROM documents WHERE url=?", (url,)).fetchone() is not None

    def documents_to_check(self, min_age_seconds, limit):
        """Документы, которые давно не проверялись на изменения."""
        border = int(time.time()) - min_age_seconds
        return self.db.execute(
            "SELECT id, url, source, content_hash, etag, last_modified, checked_at"
            " FROM documents WHERE checked_at IS NULL OR checked_at <= ?"
            " ORDER BY checked_at LIMIT ?", (border, limit)).fetchall()

    def touch(self, doc_id, etag=None, last_modified=None):
        """Отмечает, что документ проверен и не изменился."""
        self.db.execute(
            "UPDATE documents SET checked_at=?, etag=COALESCE(?, etag),"
            " last_modified=COALESCE(?, last_modified) WHERE id=?",
            (int(time.time()), etag, last_modified, doc_id))
        self.db.commit()

    def doc_stats(self):
        r = self.db.execute(
            "SELECT COUNT(*) n, SUM(LENGTH(raw_html)) raw, SUM(LENGTH(text)) txt,"
            " SUM(revision-1) recrawled FROM documents").fetchone()
        return dict(r)


# --------------------------------------------------------------------------
# Выкачка
# --------------------------------------------------------------------------

class Fetcher:
    """HTTP-клиент робота: вежливость, robots.txt, определение кодировки."""

    def __init__(self, logic):
        self.delay = float(logic.get('delay', 1.0))
        self.timeout = float(logic.get('timeout', 30))
        self.user_agent = logic.get('user_agent', 'MAI-IR-crawler/1.0')
        self.respect_robots = bool(logic.get('respect_robots', True))
        self.session = requests.Session()
        self.session.headers['User-Agent'] = self.user_agent
        self._last_request = {}   # хост -> время последнего запроса
        self._host_delay = {}     # хост -> своя пауза, если нужна длиннее общей
        self._robots = {}         # хост -> разборщик robots.txt

    def set_host_delay(self, host, delay):
        """Своя пауза для хоста: у источников разная терпимость к роботам."""
        self._host_delay[host] = float(delay)

    def _wait(self, host):
        """Задержка между обкачкой страниц одного хоста."""
        last = self._last_request.get(host)
        if last is not None:
            pause = self._host_delay.get(host, self.delay) - (time.time() - last)
            if pause > 0:
                time.sleep(pause)
        self._last_request[host] = time.time()

    def allowed(self, url):
        """Явные правила вежливости: robots.txt.

        robots.txt скачиваем своей же сессией, а не методом RobotFileParser.read():
        тот ходит через urllib со стандартным User-Agent, на который Викимедиа
        отвечает 403, а парсер трактует 403 как «запрещено всё».
        """
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(parts.scheme, host)
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def _load_robots(self, scheme, host):
        self._wait(host)
        try:
            r = self.session.get(f'{scheme}://{host}/robots.txt', timeout=self.timeout)
        except Exception:
            return None                      # сеть подвела -- не запрещаем
        if r.status_code != 200:
            return None                      # нет robots.txt -- можно всё
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp

    def get(self, url, etag=None, last_modified=None):
        """Скачивает документ. С etag/last_modified делает условный запрос."""
        host = urllib.parse.urlsplit(url).netloc
        self._wait(host)

        headers = {}
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified

        r = self.session.get(url, headers=headers, timeout=self.timeout)
        if r.status_code == 304:                     # не изменился
            return 304, None, None, r.headers
        r.raise_for_status()
        html, encoding = decode(r.content, r.headers.get('Content-Type', ''))
        return r.status_code, r.content, (html, encoding), r.headers
