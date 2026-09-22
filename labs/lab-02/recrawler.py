#!/usr/bin/env python3
"""Переобкачка: проверка того, изменились ли уже скачанные документы.

Запуск:  python3 recrawler.py config.yaml

Это отдельная программа. Она не качает документы заново, а только проверяет,
изменилось ли содержимое URL, который уже лежит в базе. Если изменилось --
кладёт URL в очередь на переобкачку, и дальше его заберёт crawler.py.

Проверка идёт в два шага, от дешёвого к дорогому:
  1. Условный запрос с If-None-Match / If-Modified-Since. Если сервер ответил
     304 Not Modified, документ точно не менялся и качать его не пришлось.
  2. Если сервер условные запросы не поддерживает, сравниваем отпечаток
     выделенного текста с тем, что сохранён в базе. Сравнивается именно текст,
     а не «сырой» HTML: реклама и счётчики меняются на каждой перезагрузке, и
     по HTML почти любой документ выглядел бы изменившимся.
"""

import signal
import sys
import time

import common

stop = False


def on_signal(signum, frame):
    global stop
    stop = True
    print('\n[!] получен сигнал остановки...')


class Recrawler:
    def __init__(self, config):
        self.cfg = config
        self.store = common.Storage(config.db['path'])
        self.fetch = common.Fetcher(config.logic)
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
            if stop:
                break
            self.check(doc)
        self.report(time.time() - started)

    def check(self, doc):
        url = doc['url']
        try:
            code, raw, decoded, headers = self.fetch.get(
                url, etag=doc['etag'], last_modified=doc['last_modified'])
        except Exception as e:
            self.stats['failed'] += 1
            print(f'[-] {url} -- {e}')
            return

        self.stats['checked'] += 1

        # Шаг 1: сервер сам сказал, что документ не менялся.
        if code == 304:
            self.stats['not_modified'] += 1
            self.store.touch(doc['id'])
            return

        # Шаг 2: сравниваем отпечаток текста.
        source = self.cfg.source(doc['source'])
        html, _ = decoded
        text = common.extract_text(html, source)
        new_hash = common.content_hash(text)

        if new_hash == doc['content_hash']:
            self.stats['same'] += 1
            self.store.touch(doc['id'], headers.get('ETag'),
                             headers.get('Last-Modified'))
            return

        # Содержимое изменилось -- в очередь на переобкачку.
        self.stats['changed'] += 1
        self.store.requeue(url, doc['source'], priority=1)
        self.store.touch(doc['id'], headers.get('ETag'),
                         headers.get('Last-Modified'))
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
        print('использование: python3 recrawler.py <config.yaml>', file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    r = Recrawler(common.Config(sys.argv[1]))
    r.run()
    r.store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
