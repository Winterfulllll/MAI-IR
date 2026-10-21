import contextlib
import io
import unittest

from tests import TestDatabase
from tests.fakes import OFFLINE, FakeFetcher
from config import Config
from crawler import Crawler
from recrawler import Recrawler

ROOT = 'https://site.test/cat/'
BODY = 'Текст статьи о фильме. ' * 10


def page(links=(), text=BODY):
    anchors = ''.join(f'<a href="{u}">ссылка</a>' for u in links)
    return (f'<html><title>T</title><div class="nav">Меню</div>'
            f'<div class="main">{text}{anchors}</div><div class="footer">Подвал</div></html>')


def make_config(**logic):
    return Config({
        'db': {},
        'logic': {'max_depth': 2, 'min_text_length': 100, **logic},
        'recrawl': {'batch': 10, 'min_age_hours': 0},
        'sources': [{
            'name': 'site',
            'container': 'main',
            'seeds': [ROOT],
            'allow': ['^https://site\\.test/'],
            'deny': ['/forum/'],
            'follow_only': ['/cat/'],
            'store_deny': ['/cat/$'],
            'blocked_marker': 'Роботам запрещено',
        }],
    })


class StopAfter:
    def __init__(self, n):
        self.n = n

    def __bool__(self):
        self.n -= 1
        return self.n < 0


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


class CrawlerTest(unittest.TestCase):
    def setUp(self):
        self.pages = {
            ROOT: page(['/doc/1', '/doc/2', '/doc/1#dup', '/forum/x', 'https://other.test/']),
            'https://site.test/doc/1': page(['/doc/deep']),
            'https://site.test/doc/2': page(text='коротко'),
        }
        self.tdb = TestDatabase()
        self.store = self.tdb.open()

    def tearDown(self):
        self.tdb.drop()

    def crawl(self, fetcher=None, stop=None, **logic):
        crawler = Crawler(make_config(**logic), self.store,
                          fetcher or FakeFetcher(self.pages), stop)
        quiet(crawler.seed)
        quiet(crawler.run)
        return crawler

    def urls(self, collection='documents'):
        return {d['url'] for d in self.store.db[collection].find({}, {'url': 1})}

    def queued(self, url):
        return self.store.frontier.find_one({'url': url})

    def test_full_crawl(self):
        c = self.crawl()
        self.assertEqual(self.urls(), {'https://site.test/doc/1'})
        self.assertEqual(self.urls('frontier'),
                         {ROOT, 'https://site.test/doc/1', 'https://site.test/doc/2'})
        self.assertEqual(self.store.queue_stats(), {'done': 3})
        self.assertEqual((c.stats['saved'], c.stats['skipped']), (1, 2))

    def test_only_container_text_stored(self):
        self.crawl()
        text = self.store.documents.find_one()['text']
        self.assertTrue(text.startswith('Текст статьи'))
        self.assertNotIn('Меню', text)
        self.assertNotIn('Подвал', text)

    def test_max_depth(self):
        self.pages[ROOT] = page(['/cat/2'])
        self.pages['https://site.test/cat/2'] = page(['/cat/3'])
        self.pages['https://site.test/cat/3'] = page(['/cat/4'])
        self.crawl(max_depth=1)
        self.assertNotIn('https://site.test/cat/3', self.urls('frontier'))

    def test_max_documents(self):
        self.pages['https://site.test/doc/2'] = page()
        c = self.crawl(max_documents=1)
        self.assertEqual(c.stats['saved'], 1)
        self.assertIn('pending', self.store.queue_stats())

    def test_robots_forbidden(self):
        self.crawl(FakeFetcher(self.pages, forbidden={'https://site.test/doc/1'}))
        row = self.queued('https://site.test/doc/1')
        self.assertEqual((row['status'], row['error']), ('skipped', 'запрещено robots.txt'))
        self.assertEqual(self.urls(), set())

    def test_network_error_marks_failed(self):
        del self.pages['https://site.test/doc/2']
        c = self.crawl()
        self.assertEqual(c.stats['failed'], 1)
        self.assertEqual(self.queued('https://site.test/doc/2')['status'], 'failed')

    def test_network_outage_retried_not_burned(self):
        doc = 'https://site.test/doc/1'
        real = self.pages[doc]

        class Outage(FakeFetcher):
            def get(self, url, etag=None, last_modified=None):
                self.pages[doc] = OFFLINE if self.requests.count(doc) < 3 else real
                return super().get(url, etag, last_modified)

        fetcher = Outage(self.pages)
        c = self.crawl(fetcher)
        self.assertEqual(c.stats['retried'], 3)
        self.assertEqual([s for _, s in fetcher.pauses], [30, 60, 120])
        self.assertEqual(self.queued(doc)['status'], 'done')
        self.assertIn(doc, self.urls())

    def test_long_outage_fails_after_retries(self):
        self.pages['https://site.test/doc/2'] = OFFLINE
        fetcher = FakeFetcher(self.pages)
        c = self.crawl(fetcher)
        self.assertEqual(c.stats['retried'], 5)
        self.assertEqual([s for _, s in fetcher.pauses], [30, 60, 120, 240, 480])
        row = self.queued('https://site.test/doc/2')
        self.assertEqual((row['status'], row['attempts']), ('failed', 5))

    def test_captcha_page_not_stored(self):
        self.pages['https://site.test/doc/1'] = page(
            text='Роботам запрещено посещать наш сайт. ' * 10)
        fetcher = FakeFetcher(self.pages)
        c = self.crawl(fetcher)
        self.assertEqual(c.stats['blocked'], 5)
        self.assertEqual(self.urls(), set())
        self.assertEqual([s for _, s in fetcher.pauses], [600, 1200, 2400, 4800, 7200])
        row = self.queued('https://site.test/doc/1')
        self.assertEqual((row['status'], row['attempts']), ('failed', 5))

    def test_captcha_then_page_recovers(self):
        doc = 'https://site.test/doc/1'
        captcha = page(text='Роботам запрещено посещать наш сайт. ' * 10)

        class Flaky(FakeFetcher):
            def get(self, url, etag=None, last_modified=None):
                if url == doc and self.requests.count(doc) < 2:
                    self.pages[doc] = captcha
                elif url == doc:
                    self.pages[doc] = page()
                return super().get(url, etag, last_modified)

        c = self.crawl(Flaky(self.pages))
        self.assertEqual(c.stats['blocked'], 2)
        self.assertIn(doc, self.urls())
        self.assertEqual(self.queued(doc)['status'], 'done')
        self.assertEqual(c.backoff, {'block': {}, 'network': {}})

    def test_stop_and_resume(self):
        first = FakeFetcher(self.pages)
        self.crawl(first, stop=StopAfter(1))
        self.assertEqual(first.requests, [ROOT])

        self.store = self.tdb.open()
        second = FakeFetcher(self.pages)
        self.crawl(second)
        self.assertEqual(second.requests,
                         ['https://site.test/doc/1', 'https://site.test/doc/2'])
        self.assertEqual(self.urls(), {'https://site.test/doc/1'})

    def test_seed_pages_template(self):
        cfg = make_config()
        cfg.sources[0]['seed_pages'] = [{'url': 'https://site.test/cat/?page={n}', 'first': 0, 'last': 2}]
        quiet(Crawler(cfg, self.store, FakeFetcher()).seed)
        self.assertEqual(self.urls('frontier'), {ROOT, 'https://site.test/cat/?page=0',
                                                  'https://site.test/cat/?page=1',
                                                  'https://site.test/cat/?page=2'})

    def test_rerun_does_not_duplicate(self):
        self.crawl()
        self.crawl()
        self.assertEqual(self.store.frontier.count_documents({}), 3)


class SchedulingTest(unittest.TestCase):
    def setUp(self):
        self.tdb = TestDatabase()
        self.store = self.tdb.open()
        cfg = make_config()
        cfg.sources.append({'name': 'slow', 'seeds': ['https://slow.test/'],
                            'delay': 5.0, 'allow': ['^https://slow\\.test/']})
        self.cfg = cfg

    def tearDown(self):
        self.tdb.drop()

    def crawler(self, fetcher):
        c = Crawler(self.cfg, self.store, fetcher)
        quiet(c.seed)
        for i in range(3):
            self.store.enqueue(f'https://site.test/doc/{i}', 'site')
            self.store.enqueue(f'https://slow.test/doc/{i}', 'slow')
        return c

    def test_busy_host_is_not_waited_for(self):
        c = self.crawler(FakeFetcher(busy={'slow.test'}))
        self.assertEqual(c.sources_by_readiness(), ['site', 'slow'])
        self.assertEqual(self.store.next_url(c.sources_by_readiness())['source'], 'site')

    def test_ready_sources_alternate(self):
        fetcher = FakeFetcher({ROOT: page(), 'https://slow.test/': page()})
        c = self.crawler(fetcher)
        for _ in range(4):
            quiet(c.process, self.store.next_url(c.sources_by_readiness()))
        self.assertEqual([u.split('/')[2] for u in fetcher.requests],
                         ['site.test', 'slow.test', 'site.test', 'slow.test'])


class RecrawlerTest(unittest.TestCase):
    DOC = 'https://site.test/doc/1'

    def setUp(self):
        self.tdb = TestDatabase()
        self.store = self.tdb.open()
        self.cfg = make_config()
        self.pages = {ROOT: page([self.DOC]), self.DOC: (page(), {'ETag': '"v1"'})}
        quiet(self._crawl)

    def _crawl(self):
        c = Crawler(self.cfg, self.store, FakeFetcher(self.pages))
        c.seed()
        c.run()
        return c

    def tearDown(self):
        self.tdb.drop()

    def recrawl(self):
        r = Recrawler(self.cfg, self.store, FakeFetcher(self.pages))
        quiet(r.run)
        return r

    def doc(self):
        return self.store.documents.find_one({'url': self.DOC})

    def test_not_modified_by_etag(self):
        r = self.recrawl()
        self.assertEqual((r.stats['not_modified'], r.stats['changed']), (1, 0))

    def test_same_text_different_html(self):
        self.pages[self.DOC] = (page().replace('Меню', 'Реклама'), {'ETag': '"v2"'})
        r = self.recrawl()
        self.assertEqual((r.stats['same'], r.stats['changed']), (1, 0))
        self.assertEqual(self.doc()['etag'], '"v2"')

    def test_changed_document_requeued_and_recrawled(self):
        self.pages[self.DOC] = (page(text='Новая редакция статьи. ' * 10), {'ETag': '"v2"'})
        r = self.recrawl()
        self.assertEqual(r.stats['changed'], 1)
        row = self.store.frontier.find_one({'url': self.DOC})
        self.assertEqual((row['status'], row['priority']), ('pending', 1))

        c = quiet(self._crawl)
        self.assertEqual(c.stats['updated'], 1)
        row = self.store.frontier.find_one({'url': self.DOC})
        self.assertEqual((row['status'], row['priority']), ('done', 0))
        self.assertEqual(self.doc()['revision'], 2)
        self.assertTrue(self.doc()['text'].startswith('Новая редакция'))

    def test_recently_checked_skipped(self):
        self.cfg.recrawl['min_age_hours'] = 1
        fetcher = FakeFetcher(self.pages)
        quiet(Recrawler(self.cfg, self.store, fetcher).run)
        self.assertEqual(fetcher.requests, [])


if __name__ == '__main__':
    unittest.main()
