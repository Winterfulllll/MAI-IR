#!/usr/bin/env python3
"""Поисковый робот: обкачка документов и сохранение их в базе данных.

Запуск:  python3 crawler.py config.yaml

Единственный аргумент -- путь до yaml-конфига. Робота можно остановить в любой
момент (Ctrl+C): очередь URL хранится в базе, и при следующем запуске работа
продолжится с того документа, на котором робот прервался.
"""

import re
import signal
import sys
import time
import urllib.parse

import common

stop = False


def on_signal(signum, frame):
    """Ctrl+C не убивает робота мгновенно: даём дописать текущий документ."""
    global stop
    stop = True
    print('\n[!] получен сигнал остановки, завершаем текущий документ...')


class Crawler:
    def __init__(self, config):
        self.cfg = config
        self.store = common.Storage(config.db['path'])
        self.fetch = common.Fetcher(config.logic)
        self.max_documents = int(config.logic.get('max_documents', 0))
        self.max_depth = int(config.logic.get('max_depth', 3))
        self.min_text = int(config.logic.get('min_text_length', 0))
        self.rules = {}        # какие ссылки вообще обходить
        self.follow_rules = {} # с каких страниц собирать ссылки
        self.store_rules = {}  # какие из обойдённых сохранять как документы
        for s in config.sources:
            self.rules[s['name']] = (
                [re.compile(p) for p in s.get('allow', [])],
                [re.compile(p) for p in s.get('deny', [])],
            )
            self.store_rules[s['name']] = (
                [re.compile(p) for p in s.get('store_allow', [])],
                [re.compile(p) for p in s.get('store_deny', [])],
            )
            # С каких страниц вообще собирать ссылки. Без этого робот уходит
            # из своей тематики: из статьи о фильме ссылки ведут куда угодно.
            self.follow_rules[s['name']] = [
                re.compile(p) for p in s.get('follow_only', [])]
        self.stats = {'fetched': 0, 'saved': 0, 'updated': 0, 'skipped': 0,
                      'failed': 0, 'blocked': 0}
        # Признак страницы-заглушки с капчей и своя пауза для источника.
        self.blocked_marker = {}
        for s in config.sources:
            if s.get('blocked_marker'):
                self.blocked_marker[s['name']] = s['blocked_marker']
            if s.get('delay'):
                for seed in s.get('seeds', []):
                    host = urllib.parse.urlsplit(seed).netloc
                    self.fetch.set_host_delay(host, s['delay'])

    # --- фильтры ---------------------------------------------------------

    def acceptable(self, url, source_name):
        """Проверяет, что ссылка проходит фильтры источника."""
        allow, deny = self.rules[source_name]
        if allow and not any(p.search(url) for p in allow):
            return False
        if any(p.search(url) for p in deny):
            return False
        return True

    def storable(self, url, source_name):
        """Отличает документ от служебной страницы.

        Списки, каталоги и оглавления нужны только чтобы собрать с них ссылки:
        обходим их, но в базу не кладём.
        """
        allow, deny = self.store_rules[source_name]
        if allow and not any(p.search(url) for p in allow):
            return False
        if any(p.search(url) for p in deny):
            return False
        return True

    def may_follow(self, url, source_name):
        """Собирать ли ссылки с этой страницы."""
        rules = self.follow_rules[source_name]
        return True if not rules else any(p.search(url) for p in rules)

    # --- посев -----------------------------------------------------------

    def seed(self):
        """Кладёт в очередь стартовые URL. Уже известные не дублируются."""
        added = 0
        for s in self.cfg.sources:
            for url in s.get('seeds', []):
                url = common.normalize_url(url)
                if url and self.store.enqueue(url, s['name'], depth=0):
                    added += 1
        if added:
            self.store.db.commit()
            print(f'[+] в очередь добавлено стартовых URL: {added}')

    # --- основной цикл ---------------------------------------------------

    def run(self):
        started = time.time()
        last_source = None
        while not stop:
            if self.max_documents and self.stats['saved'] >= self.max_documents:
                print(f'[i] достигнут предел в {self.max_documents} документов')
                break

            row = self.store.next_url(avoid_source=last_source)
            if row is None:
                print('[i] очередь пуста')
                break

            last_source = row['source']
            self.process(row)

        elapsed = time.time() - started
        self.report(elapsed)

    def process(self, row):
        url, source_name, depth = row['url'], row['source'], row['depth']
        source = self.cfg.source(source_name)

        if not self.fetch.allowed(url):
            self.store.mark(row['id'], 'skipped', 'запрещено robots.txt')
            self.stats['skipped'] += 1
            return

        try:
            code, raw, decoded, headers = self.fetch.get(url)
        except Exception as e:
            self.store.mark(row['id'], 'failed', str(e)[:200])
            self.stats['failed'] += 1
            print(f'[-] {url} -- {e}')
            return

        self.stats['fetched'] += 1
        html, encoding = decoded
        text = common.extract_text(html, source)

        # Защита сайта подсунула капчу вместо документа. Сохранять её нельзя:
        # в базу попадёт одинаковая заглушка вместо всех документов подряд.
        marker = self.blocked_marker.get(source_name)
        if marker and marker in text:
            self.stats['blocked'] += 1
            self.store.mark(row['id'], 'failed', 'блокировка: отдана капча')
            print(f'[блокировка] {url}')
            return

        # Служебная или списочная страница: ссылки с неё берём, но не сохраняем.
        if self.storable(url, source_name) and len(text) >= self.min_text:
            what = self.store.save(
                url, source_name, raw, text, common.extract_title(html), encoding,
                headers.get('ETag'), headers.get('Last-Modified'))
            if what == 'new':
                self.stats['saved'] += 1
            else:
                self.stats['updated'] += 1
            print(f'[{what:8s}] {len(text):7d} симв.  {url}')
        else:
            self.stats['skipped'] += 1

        if depth < self.max_depth and self.may_follow(url, source_name):
            self.harvest(html, url, source_name, depth)

        self.store.mark(row['id'], 'done')

    def harvest(self, html, base_url, source_name, depth):
        """Извлекает ссылки и добавляет в очередь те, что прошли фильтры."""
        added = 0
        for link in common.extract_links(html, base_url):
            if self.acceptable(link, source_name):
                if self.store.enqueue(link, source_name, depth + 1):
                    added += 1
        if added:
            self.store.db.commit()

    def report(self, elapsed):
        q = self.store.queue_stats()
        d = self.store.doc_stats()
        print('\n' + '=' * 62)
        print(f'Время работы:          {elapsed:.1f} с')
        print(f'Скачано страниц:       {self.stats["fetched"]}')
        print(f'  сохранено новых:     {self.stats["saved"]}')
        print(f'  обновлено:           {self.stats["updated"]}')
        print(f'  пропущено:           {self.stats["skipped"]}')
        print(f'  блокировок:          {self.stats["blocked"]}')
        print(f'  ошибок:              {self.stats["failed"]}')
        if self.stats['fetched']:
            print(f'Средняя скорость:      {self.stats["fetched"]/elapsed:.2f} стр/с')
        print(f'Очередь:               {q}')
        print(f'Документов в базе:     {d["n"]}')
        if d['n']:
            print(f'  «сырых» данных:      {(d["raw"] or 0)/1048576:.1f} МБ')
            print(f'  выделенного текста:  {(d["txt"] or 0)/1048576:.1f} МБ')
        print('=' * 62)


def main():
    if len(sys.argv) != 2:
        print('использование: python3 crawler.py <config.yaml>', file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    crawler = Crawler(common.Config(sys.argv[1]))
    crawler.seed()
    crawler.run()
    crawler.store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
