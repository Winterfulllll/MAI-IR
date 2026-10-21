#!/usr/bin/env python3
import sys
import time

import requests

from config import Config
from fetcher import Fetcher
from parsing import extract_links, extract_text, extract_title
from rules import SourceRules
from shutdown import StopFlag
from storage import Storage
from urls import host, normalize_url


class Crawler:
    def __init__(self, config, store=None, fetcher=None, stop=None):
        self.cfg = config
        self.store = store or Storage.from_config(config.db)
        self.fetch = fetcher or Fetcher(config.logic)
        self.stop = stop if stop is not None else StopFlag()
        self.max_documents = int(config.logic.get('max_documents', 0))
        self.max_depth = int(config.logic.get('max_depth', 3))
        self.min_text = int(config.logic.get('min_text_length', 0))
        self.retry = {
            'block': {'retries': int(config.logic.get('block_retries', 5)),
                      'pause': 600.0, 'pause_max': 7200.0},
            'network': {'retries': int(config.logic.get('network_retries', 5)),
                        'pause': float(config.logic.get('network_pause', 30)),
                        'pause_max': float(config.logic.get('network_pause_max', 600))},
        }
        self.backoff = {'block': {}, 'network': {}}
        self.rules = {s['name']: SourceRules(s) for s in config.sources}
        self.stats = {'fetched': 0, 'saved': 0, 'updated': 0, 'skipped': 0,
                      'failed': 0, 'blocked': 0, 'retried': 0}
        self.hosts = {}
        for s in config.sources:
            self.hosts[s['name']] = host(s['seeds'][0]) if s.get('seeds') else None
            if s.get('delay') and self.hosts[s['name']]:
                self.fetch.set_host_delay(self.hosts[s['name']], s['delay'])

    @staticmethod
    def seed_urls(source):
        yield from source.get('seeds', [])
        for pages in source.get('seed_pages', []):
            for n in range(int(pages['first']), int(pages['last']) + 1):
                yield pages['url'].format(n=n)

    def seed(self):
        added = 0
        for s in self.cfg.sources:
            for url in self.seed_urls(s):
                url = normalize_url(url)
                if url and self.store.enqueue(url, s['name'], depth=0):
                    added += 1
        if added:
            print(f'[+] в очередь добавлено стартовых URL: {added}')

    def run(self):
        started = time.time()
        while not self.stop:
            if self.max_documents and self.stats['saved'] >= self.max_documents:
                print(f'[i] достигнут предел в {self.max_documents} документов')
                break

            row = self.store.next_url(self.sources_by_readiness())
            if row is None:
                print('[i] очередь пуста')
                break

            if self.wait_for_host(row['source']):
                self.process(row)

        self.report(time.time() - started)

    def wait_for_host(self, source_name):
        host_name = self.hosts.get(source_name)
        while host_name:
            pause = self.fetch.wait_time(host_name)
            if pause <= 0:
                return True
            if self.stop:
                return False
            time.sleep(min(pause, 1.0))
        return True

    def retry_later(self, row, rules, kind, reason):
        policy = self.retry[kind]
        source = self.cfg.source(rules.name)
        base = float(source.get(f'{kind}_pause', policy['pause']))
        limit = float(source.get(f'{kind}_pause_max', policy['pause_max']))
        pause = min(self.backoff[kind].get(rules.name, base / 2) * 2, limit)
        self.backoff[kind][rules.name] = pause
        if self.hosts.get(rules.name):
            self.fetch.pause_host(self.hosts[rules.name], pause)
        if row['attempts'] + 1 >= policy['retries']:
            self.store.mark(row['_id'], 'failed', reason)
        else:
            self.store.mark(row['_id'], 'pending', f'{reason}, повторим позже')
        return pause

    def on_blocked(self, row, rules):
        self.stats['blocked'] += 1
        pause = self.retry_later(row, rules, 'block', 'блокировка: отдана капча')
        print(f'[блокировка] {row["url"]} -- пауза источника {pause / 60:.0f} мин')

    @staticmethod
    def is_transient(error):
        if isinstance(error, (requests.ConnectionError, requests.Timeout)):
            return True
        response = getattr(error, 'response', None)
        return isinstance(error, requests.HTTPError) and response is not None and (
            response.status_code >= 500 or response.status_code == 429)

    def sources_by_readiness(self):
        return sorted(self.hosts, key=lambda name: (
            self.fetch.wait_time(self.hosts[name]) if self.hosts[name] else 0.0,
            self.fetch.last_request(self.hosts[name]) if self.hosts[name] else 0.0))

    def process(self, row):
        url, source_name, depth = row['url'], row['source'], row['depth']
        source = self.cfg.source(source_name)
        rules = self.rules[source_name]

        if not self.fetch.allowed(url):
            self.store.mark(row['_id'], 'skipped', 'запрещено robots.txt')
            self.stats['skipped'] += 1
            return

        try:
            resp = self.fetch.get(url)
        except Exception as e:
            if self.is_transient(e):
                self.stats['retried'] += 1
                pause = self.retry_later(row, rules, 'network', str(e)[:200])
                print(f'[сеть] {url} -- {type(e).__name__}, пауза источника {pause:.0f} с')
                return
            self.store.mark(row['_id'], 'failed', str(e)[:200])
            self.stats['failed'] += 1
            print(f'[-] {url} -- {e}')
            return
        self.backoff['network'].pop(source_name, None)

        self.stats['fetched'] += 1
        text = extract_text(resp.html, source)

        if rules.is_blocked(text):
            self.on_blocked(row, rules)
            return
        self.backoff['block'].pop(source_name, None)

        if rules.storable(url) and len(text) >= self.min_text:
            what = self.store.save(
                url, source_name, resp.raw, text, extract_title(resp.html),
                resp.encoding, resp.headers.get('ETag'),
                resp.headers.get('Last-Modified'))
            self.stats['saved' if what == 'new' else 'updated'] += 1
            print(f'[{what:8s}] {len(text):7d} симв.  {url}')
        else:
            self.stats['skipped'] += 1

        if depth < self.max_depth and rules.may_follow(url):
            self.harvest(resp.html, url, rules, depth)

        self.store.mark(row['_id'], 'done')

    def harvest(self, html, base_url, rules, depth):
        links = [u for u in extract_links(html, base_url) if rules.acceptable(u)]
        return self.store.enqueue_many(links, rules.name, depth + 1)

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
        print(f'  сетевых сбоев:       {self.stats["retried"]}')
        print(f'  ошибок:              {self.stats["failed"]}')
        if self.stats['fetched'] and elapsed > 0:
            print(f'Средняя скорость:      {self.stats["fetched"]/elapsed:.2f} стр/с')
        print(f'Очередь:               {q}')
        print(f'Документов в базе:     {d["n"]}')
        if d['n']:
            print(f'  «сырых» данных:      {(d["raw"] or 0)/1048576:.1f} МБ')
            print(f'  выделенного текста:  {(d["txt_bytes"] or 0)/1048576:.1f} МБ')
        print('=' * 62)


def main():
    if len(sys.argv) != 2:
        print('использование: python3 src/crawler.py <config.yaml>', file=sys.stderr)
        return 1
    stop = StopFlag().install('получен сигнал остановки, завершаем текущий документ...')
    crawler = Crawler(Config.load(sys.argv[1]), stop=stop)
    try:
        crawler.seed()
        crawler.run()
    finally:
        crawler.store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
