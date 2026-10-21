import time
import unittest

from tests import TestDatabase
from parsing import content_hash


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tdb = TestDatabase()
        self.store = self.tdb.open()

    def tearDown(self):
        self.tdb.drop()

    def save(self, url='https://a/1', text='текст'):
        return self.store.save(url, 'a', b'<html/>', text, 't', 'utf-8', None, None)

    def doc(self, url='https://a/1'):
        return self.store.documents.find_one({'url': url})

    def test_enqueue_ignores_duplicates(self):
        self.assertTrue(self.store.enqueue('https://a/1', 'a'))
        self.assertFalse(self.store.enqueue('https://a/1', 'a', depth=3))
        self.assertEqual(self.store.queue_stats(), {'pending': 1})
        self.assertEqual(self.store.frontier.find_one()['depth'], 0)

    def test_enqueue_many(self):
        self.store.enqueue('https://a/1', 'a')
        self.assertEqual(self.store.enqueue_many(
            ['https://a/1', 'https://a/2', 'https://a/3'], 'a', depth=1), 2)
        self.assertEqual(self.store.enqueue_many([], 'a'), 0)
        self.assertEqual(self.store.frontier.count_documents({}), 3)

    def test_unique_index_on_url(self):
        from pymongo.errors import DuplicateKeyError
        self.save()
        with self.assertRaises(DuplicateKeyError):
            self.store.documents.insert_one({'url': 'https://a/1'})

    def test_next_url_fifo_and_mark(self):
        self.store.enqueue('https://a/1', 'a')
        self.store.enqueue('https://a/2', 'a')
        row = self.store.next_url()
        self.assertEqual(row['url'], 'https://a/1')
        self.store.mark(row['_id'], 'done')
        self.assertEqual(self.store.next_url()['url'], 'https://a/2')
        self.assertEqual(self.store.queue_stats(), {'done': 1, 'pending': 1})
        self.assertEqual(self.store.frontier.find_one({'url': 'https://a/1'})['attempts'], 1)

    def test_next_url_by_source_preference(self):
        for url, src in (('https://a/1', 'a'), ('https://a/2', 'a'), ('https://b/1', 'b')):
            self.store.enqueue(url, src)
        self.assertEqual(self.store.next_url(['b', 'a'])['url'], 'https://b/1')
        self.assertEqual(self.store.next_url(['a', 'b'])['url'], 'https://a/1')
        self.assertEqual(self.store.next_url(['c', 'a'])['url'], 'https://a/1')
        self.assertIsNone(self.store.next_url(['c']))

    def test_requeued_url_goes_first(self):
        self.store.enqueue('https://a/new', 'a')
        self.store.enqueue('https://a/old', 'a')
        self.store.mark(self.store.next_url()['_id'], 'done')
        self.store.mark(self.store.next_url()['_id'], 'done')
        self.store.enqueue('https://a/newer', 'a')
        self.store.requeue('https://a/old', 'a', priority=1)
        row = self.store.next_url()
        self.assertEqual((row['url'], row['status'], row['priority']),
                         ('https://a/old', 'pending', 1))

    def test_requeued_url_beats_preferred_source(self):
        self.store.enqueue('https://a/old', 'a')
        self.store.mark(self.store.next_url()['_id'], 'done')
        self.store.enqueue('https://b/new', 'b')
        self.store.requeue('https://a/old', 'a', priority=1)
        self.assertEqual(self.store.next_url(['b', 'a'])['url'], 'https://a/old')

    def test_save_new_updated_unchanged(self):
        self.assertEqual(self.save(text='v1'), 'new')
        self.assertEqual(self.save(text='v1'), 'unchanged')
        self.assertEqual(self.save(text='v2'), 'updated')
        doc = self.doc()
        self.assertEqual(doc['revision'], 3)
        self.assertEqual(doc['content_hash'], content_hash('v2'))
        self.assertEqual(self.store.documents.count_documents({}), 1)
        self.assertTrue(self.store.has('https://a/1'))
        self.assertFalse(self.store.has('https://a/2'))

    def test_required_fields_stored(self):
        before = int(time.time())
        self.store.save('https://a/1', 'wikipedia', b'<p>raw</p>', 'raw', 't',
                        'utf-8', '"e1"', 'Mon, 01 Jan 2024 00:00:00 GMT')
        doc = self.doc()
        self.assertEqual(doc['url'], 'https://a/1')
        self.assertEqual(bytes(doc['raw_html']), b'<p>raw</p>')
        self.assertEqual(doc['source'], 'wikipedia')
        self.assertGreaterEqual(doc['crawled_at'], before)
        self.assertIsInstance(doc['crawled_at'], int)
        self.assertEqual((doc['etag'], doc['last_modified']),
                         ('"e1"', 'Mon, 01 Jan 2024 00:00:00 GMT'))

    def test_documents_to_check_respects_age_and_limit(self):
        for i in range(3):
            self.save(url=f'https://a/{i}')
        self.assertEqual(self.store.documents_to_check(3600, 10), [])
        self.assertEqual(len(self.store.documents_to_check(0, 2)), 2)
        self.store.documents.update_one({'url': 'https://a/1'}, {'$set': {'checked_at': 0}})
        self.assertEqual([d['url'] for d in self.store.documents_to_check(3600, 10)],
                         ['https://a/1'])
        self.assertNotIn('raw_html', self.store.documents_to_check(0, 1)[0])

    def test_touch_keeps_old_validators_when_none_given(self):
        self.store.save('https://a/1', 'a', b'', 'x', '', 'utf-8', '"e1"', None)
        doc_id = self.doc()['_id']
        self.store.touch(doc_id)
        self.store.touch(doc_id, last_modified='LM')
        doc = self.doc()
        self.assertEqual((doc['etag'], doc['last_modified']), ('"e1"', 'LM'))

    def test_stats(self):
        self.assertEqual(self.store.doc_stats()['n'], 0)
        self.save(url='https://a/1', text='abc')
        self.save(url='https://a/1', text='abcd')
        d = self.store.doc_stats()
        self.assertEqual((d['n'], d['txt'], d['txt_bytes'], d['recrawled']), (1, 4, 4, 1))
        self.save(url='https://a/2', text='абв')
        d = self.store.doc_stats()
        self.assertEqual((d['txt'], d['txt_bytes']), (7, 10))
        s = self.store.source_stats()
        self.assertEqual((s[0]['source'], s[0]['n'], s[0]['txt_bytes'], s[0]['encodings']),
                         ('a', 2, 10, 'utf-8'))


class ReparseTest(unittest.TestCase):
    def setUp(self):
        self.tdb = TestDatabase()
        self.store = self.tdb.open()

    def tearDown(self):
        self.tdb.drop()

    def test_reparse_from_raw_html_without_refetch(self):
        from config import Config
        cfg = Config({'db': {}, 'logic': {}, 'sources': [{'name': 'a', 'container': 'main'}]})
        raw = '<title>&laquo;Т&raquo;</title><div class="main">&laquo;Текст&raquo; &amp; ещё</div>'.encode()
        self.store.save('https://a/1', 'a', raw, 'Текст ещё', 'Т', 'utf-8', None, None)
        self.store.save('https://a/2', 'a', b'<div class="main">ok</div>', 'ok', '', 'utf-8', None, None)
        before = self.store.documents.find_one({'url': 'https://a/1'})

        checked, changed, delta = quiet_reparse(cfg, self.store)

        doc = self.store.documents.find_one({'url': 'https://a/1'})
        self.assertEqual((checked, changed), (2, 1))
        self.assertEqual((doc['text'], doc['title']), ('«Текст» & ещё', '«Т»'))
        self.assertEqual(doc['content_hash'], content_hash('«Текст» & ещё'))
        self.assertEqual((doc['text_size'], doc['text_bytes']), (13, len('«Текст» & ещё'.encode())))
        self.assertEqual((doc['revision'], doc['crawled_at']), (before['revision'], before['crawled_at']))
        self.assertEqual(delta, 4)
        self.assertEqual(quiet_reparse(cfg, self.store)[1], 0)


def quiet_reparse(cfg, store):
    import contextlib
    import io
    from reparse import reparse
    with contextlib.redirect_stdout(io.StringIO()):
        return reparse(cfg, store)


class PersistenceTest(unittest.TestCase):
    def test_queue_survives_reopen(self):
        tdb = TestDatabase()
        try:
            s = tdb.open()
            s.enqueue('https://a/1', 'a')
            s.enqueue('https://a/2', 'a')
            s.mark(s.next_url()['_id'], 'done')

            s = tdb.open()
            self.assertEqual(s.next_url()['url'], 'https://a/2')
            self.assertEqual(s.queue_stats(), {'done': 1, 'pending': 1})
        finally:
            tdb.drop()


if __name__ == '__main__':
    unittest.main()
