#!/usr/bin/env python3
import sys
import time

from config import Config
from fetcher import Fetcher
from parsing import content_hash, extract_text
from shutdown import StopFlag
from storage import Storage


class Recrawler:
    def __init__(self, config, store=None, fetcher=None, stop=None):
        self.cfg = config
        self.store = store or Storage.from_config(config.db)
        self.fetch = fetcher or Fetcher(config.logic)
        self.stop = stop if stop is not None else StopFlag()
        rc = config.recrawl
        self.batch = int(rc.get('batch', 100))
        self.min_age = int(float(rc.get('min_age_hours', 24)) * 3600)
        self.stats = {'checked': 0, 'not_modified': 0, 'same': 0,
                      'changed': 0, 'failed': 0}

    def run(self):
        docs = self.store.documents_to_check(self.min_age, self.batch)
        if not docs:
            print('[i] нет документов, требующих проверки')
            return

        print(f'[i] проверяем документов: {len(docs)}')
        started = time.time()
        for doc in docs:
            if self.stop:
                break
            self.check(doc)
        self.report(time.time() - started)

    def check(self, doc):
        url = doc['url']
        try:
            resp = self.fetch.get(
                url, etag=doc['etag'], last_modified=doc['last_modified'])
        except Exception as e:
            self.stats['failed'] += 1
            print(f'[-] {url} -- {e}')
            return

        self.stats['checked'] += 1

        if resp.not_modified:
            self.stats['not_modified'] += 1
            self.store.touch(doc['_id'])
            return

        text = extract_text(resp.html, self.cfg.source(doc['source']))
        etag, last_modified = resp.headers.get('ETag'), resp.headers.get('Last-Modified')

        if content_hash(text) == doc['content_hash']:
            self.stats['same'] += 1
            self.store.touch(doc['_id'], etag, last_modified)
            return

        self.stats['changed'] += 1
        self.store.requeue(url, doc['source'], priority=1)
        self.store.touch(doc['_id'], etag, last_modified)
        print(f'[изменился] {url}')

    def report(self, elapsed):
        s = self.stats
        print('\n' + '=' * 62)
        print(f'Время работы:              {elapsed:.1f} с')
        print(f'Проверено документов:      {s["checked"]}')
        print(f'  ответ 304 Not Modified:  {s["not_modified"]}')
        print(f'  текст совпал:            {s["same"]}')
        print(f'  изменились:              {s["changed"]}')
        print(f'  ошибок:                  {s["failed"]}')
        print(f'В очередь на переобкачку добавлено: {s["changed"]}')
        print('=' * 62)


def main():
    if len(sys.argv) != 2:
        print('использование: python3 src/recrawler.py <config.yaml>', file=sys.stderr)
        return 1
    stop = StopFlag().install('получен сигнал остановки...')
    r = Recrawler(Config.load(sys.argv[1]), stop=stop)
    try:
        r.run()
    finally:
        r.store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
