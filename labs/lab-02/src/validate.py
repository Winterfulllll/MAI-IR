#!/usr/bin/env python3
import argparse
import sys
import time

from config import Config
from fetcher import Fetcher
from parsing import content_hash, decode, extract_text
from rules import SourceRules
from storage import Storage
from urls import normalize_url

REQUIRED = ('url', 'source', 'raw_html', 'text', 'content_hash', 'crawled_at', 'revision')
EARLIEST = 1577836800


class Report:
    def __init__(self):
        self.failed = 0

    def check(self, name, bad, total, examples=()):
        status = 'ok  ' if not bad else 'FAIL'
        print(f'[{status}] {name}: {total - bad}/{total}')
        for example in list(examples)[:3]:
            print(f'         {example}')
        self.failed += bool(bad)


def document_checks(cfg, rules, min_text):
    def text_mismatch(d):
        html, _ = decode(bytes(d['raw_html']), f"charset={d.get('encoding') or ''}")
        return extract_text(html, cfg.source(d['source'])) != d['text']

    return [
        ('все обязательные поля на месте',
         lambda d: any(d.get(f) in (None, '', b'') for f in REQUIRED)),
        ('источник есть в конфиге', lambda d: d['source'] not in rules),
        ('crawled_at -- Unix timestamp в разумных пределах',
         lambda d: not isinstance(d['crawled_at'], int)
         or not EARLIEST <= d['crawled_at'] <= time.time() + 60),
        ('url нормализованы', lambda d: normalize_url(d['url']) != d['url']),
        ('url проходит фильтры источника',
         lambda d: d['source'] in rules and not (
             rules[d['source']].acceptable(d['url']) and rules[d['source']].storable(d['url']))),
        ('raw_size, text_size и text_bytes совпадают с содержимым',
         lambda d: (d.get('raw_size'), d.get('text_size'), d.get('text_bytes'))
         != (len(d['raw_html']), len(d['text']), len(d['text'].encode('utf-8')))),
        ('text заново выделяется из raw_html один в один', text_mismatch),
        ('content_hash = SHA-256 текста', lambda d: content_hash(d['text']) != d['content_hash']),
        (f'текст не короче min_text_length ({min_text})', lambda d: len(d['text']) < min_text),
        ('нет страниц-капч',
         lambda d: d['source'] in rules and rules[d['source']].is_blocked(d['text'])),
    ]


def offline_checks(cfg, store, report, batch=500):
    rules = {s['name']: SourceRules(s) for s in cfg.sources}
    checks = document_checks(cfg, rules, int(cfg.logic.get('min_text_length', 0)))
    queue_check = 'документ отмечен в очереди как done (или ждёт переобкачки)'
    bad = {name: [] for name, _ in checks + [(queue_check, None)]}
    print(f'Документов: {store.documents.count_documents({})}, очередь: {store.queue_stats()}\n')

    n = 0
    chunk = []
    for d in store.documents.find(batch_size=batch):
        n += 1
        for name, failed in checks:
            if failed(d):
                bad[name].append(d.get('url'))
        chunk.append(d['url'])
        if len(chunk) == batch:
            bad[queue_check] += unfinished(store, chunk)
            chunk = []
    bad[queue_check] += unfinished(store, chunk)

    dups = [d['_id'] for d in store.documents.aggregate([
        {'$group': {'_id': '$url', 'n': {'$sum': 1}}}, {'$match': {'n': {'$gt': 1}}}],
        allowDiskUse=True)]
    for name, _ in checks[:3]:
        report.check(name, len(bad[name]), n, bad[name])
    report.check('url уникальны', len(dups), n, dups)
    for name, _ in checks[3:]:
        report.check(name, len(bad[name]), n, bad[name])
    report.check(queue_check, len(bad[queue_check]), n, bad[queue_check])
    return n


def unfinished(store, urls):
    if not urls:
        return []
    statuses = {f['url']: f['status'] for f in store.frontier.find(
        {'url': {'$in': urls}}, {'url': 1, 'status': 1})}
    return [f'{u} {statuses.get(u)}' for u in urls if statuses.get(u) not in ('done', 'pending')]


def live_check(cfg, store, count, report):
    sample = list(store.documents.aggregate([
        {'$sample': {'size': count}},
        {'$project': {'url': 1, 'source': 1, 'content_hash': 1}}]))
    print(f'\nПерекачиваем с сайтов случайные документы: {len(sample)}')
    fetcher = Fetcher(cfg.logic)
    for s in cfg.sources:
        if s.get('delay'):
            for seed in s.get('seeds', []):
                fetcher.set_host_delay(seed.split('/')[2], s['delay'])
    errors, changed = [], 0
    for d in sample:
        try:
            resp = fetcher.get(d['url'])
        except Exception as e:
            errors.append(f"{d['url']} -- {e}")
            continue
        same = content_hash(extract_text(resp.html, cfg.source(d['source']))) == d['content_hash']
        changed += not same
        print(f"  {'совпал   ' if same else 'изменился'} {d['url']}")
    report.check('документы доступны на сайте', len(errors), len(sample), errors)
    if changed:
        print(f'         изменились на сайте после обкачки: {changed} -- их найдёт make recrawl')


def main():
    parser = argparse.ArgumentParser(description='Проверка содержимого базы робота')
    parser.add_argument('config')
    parser.add_argument('--live', type=int, default=0, metavar='N',
                        help='перекачать N случайных документов и сравнить с базой')
    args = parser.parse_args()

    cfg = Config.load(args.config)
    store = Storage.from_config(cfg.db)
    report = Report()
    try:
        if offline_checks(cfg, store, report) and args.live:
            live_check(cfg, store, args.live, report)
    finally:
        store.close()
    print('\nИтог:', 'всё в порядке' if not report.failed else f'проверок не прошло: {report.failed}')
    return 1 if report.failed else 0


if __name__ == '__main__':
    sys.exit(main())
