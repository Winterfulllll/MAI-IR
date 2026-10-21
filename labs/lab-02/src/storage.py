import time

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import BulkWriteError, DuplicateKeyError

from parsing import content_hash

QUEUE_ORDER = [('priority', DESCENDING), ('_id', ASCENDING)]


class Storage:
    def __init__(self, uri='mongodb://localhost:27017', name='crawler', client=None):
        self.client = client or MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[name]
        self.documents = self.db['documents']
        self.frontier = self.db['frontier']
        self.documents.create_index('url', unique=True)
        self.documents.create_index('checked_at')
        for field in ('source', 'text_size', 'revision'):
            self.documents.create_index(field)
        self.frontier.create_index('url', unique=True)
        self.frontier.create_index([('status', ASCENDING)] + QUEUE_ORDER)

    @classmethod
    def from_config(cls, db):
        return cls(db.get('uri', 'mongodb://localhost:27017'), db.get('name', 'crawler'))

    def close(self):
        self.client.close()

    @staticmethod
    def _queue_item(url, source, depth, priority):
        return {'url': url, 'source': source, 'depth': depth, 'status': 'pending',
                'priority': priority, 'attempts': 0, 'added_at': int(time.time()),
                'error': None}

    def enqueue(self, url, source, depth=0, priority=0):
        try:
            r = self.frontier.update_one(
                {'url': url},
                {'$setOnInsert': self._queue_item(url, source, depth, priority)},
                upsert=True)
        except DuplicateKeyError:
            return False
        return r.upserted_id is not None

    def enqueue_many(self, urls, source, depth=0):
        if not urls:
            return 0
        try:
            return len(self.frontier.insert_many(
                [self._queue_item(u, source, depth, 0) for u in urls],
                ordered=False).inserted_ids)
        except BulkWriteError as e:
            if any(err['code'] != 11000 for err in e.details['writeErrors']):
                raise
            return e.details['nInserted']

    def requeue(self, url, source, priority=1):
        self.frontier.update_one(
            {'url': url},
            {'$set': {'status': 'pending', 'priority': priority, 'attempts': 0,
                      'error': None},
             '$setOnInsert': {'source': source, 'depth': 0, 'added_at': int(time.time())}},
            upsert=True)

    def next_url(self, sources=None):
        for urgent in (True, False):
            for source in sources or [None]:
                query = {'status': 'pending'}
                if urgent:
                    query['priority'] = {'$gt': 0}
                if source is not None:
                    query['source'] = source
                row = self.frontier.find_one(query, sort=QUEUE_ORDER)
                if row is not None:
                    return row
        return None

    def mark(self, row_id, status, error=None):
        self.frontier.update_one(
            {'_id': row_id},
            {'$set': {'status': status, 'error': error, 'priority': 0}, '$inc': {'attempts': 1}})

    def queue_stats(self):
        return {r['_id']: r['n'] for r in self.frontier.aggregate(
            [{'$group': {'_id': '$status', 'n': {'$sum': 1}}}])}

    def save(self, url, source, raw, text, title, encoding, etag, last_modified):
        now = int(time.time())
        h = content_hash(text)
        fields = {'source': source, 'raw_html': raw, 'text': text, 'title': title,
                  'encoding': encoding, 'content_hash': h, 'etag': etag,
                  'last_modified': last_modified, 'crawled_at': now, 'checked_at': now,
                  'raw_size': len(raw), 'text_size': len(text),
                  'text_bytes': len(text.encode('utf-8'))}
        old = self.documents.find_one_and_update(
            {'url': url},
            {'$set': fields, '$inc': {'revision': 1}},
            projection={'content_hash': 1}, upsert=True)
        if old is None:
            return 'new'
        return 'updated' if old['content_hash'] != h else 'unchanged'

    def has(self, url):
        return self.documents.count_documents({'url': url}, limit=1) > 0

    def documents_to_check(self, min_age_seconds, limit):
        border = int(time.time()) - min_age_seconds
        return list(self.documents.find(
            {'$or': [{'checked_at': None}, {'checked_at': {'$lte': border}}]},
            {'url': 1, 'source': 1, 'content_hash': 1, 'etag': 1,
             'last_modified': 1, 'checked_at': 1},
            sort=[('checked_at', ASCENDING)], limit=limit))

    def touch(self, doc_id, etag=None, last_modified=None):
        fields = {'checked_at': int(time.time())}
        if etag is not None:
            fields['etag'] = etag
        if last_modified is not None:
            fields['last_modified'] = last_modified
        self.documents.update_one({'_id': doc_id}, {'$set': fields})

    def doc_stats(self):
        r = next(self.documents.aggregate([{'$group': {
            '_id': None, 'n': {'$sum': 1}, 'raw': {'$sum': '$raw_size'},
            'txt': {'$sum': '$text_size'}, 'txt_bytes': {'$sum': '$text_bytes'},
            'revisions': {'$sum': '$revision'}}}]), None)
        if r is None:
            return {'n': 0, 'raw': 0, 'txt': 0, 'txt_bytes': 0, 'recrawled': 0}
        return {'n': r['n'], 'raw': r['raw'], 'txt': r['txt'], 'txt_bytes': r['txt_bytes'],
                'recrawled': r['revisions'] - r['n']}

    def source_stats(self):
        return [{'source': r['_id'], 'n': r['n'], 'raw': r['raw'], 'txt': r['txt'],
                 'txt_bytes': r['txt_bytes'],
                 'encodings': ','.join(sorted(e for e in r['encodings'] if e))}
                for r in self.documents.aggregate([
                    {'$group': {'_id': '$source', 'n': {'$sum': 1},
                                'raw': {'$sum': '$raw_size'}, 'txt': {'$sum': '$text_size'},
                                'txt_bytes': {'$sum': '$text_bytes'},
                                'encodings': {'$addToSet': '$encoding'}}},
                    {'$sort': {'n': -1}}])]
