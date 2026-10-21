import os
import sys
import uuid

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(LAB, 'src'))

CONFIG = os.path.join(LAB, 'config.yaml')
SAMPLES = os.path.join(LAB, '..', 'lab-01', 'samples')
FIXTURES = os.path.join(LAB, 'tests', 'fixtures')
MONGO_URI = os.environ.get('MONGO_URI')


class TestDatabase:
    def __init__(self):
        self.name = 'crawler_test_' + uuid.uuid4().hex[:8]
        if MONGO_URI:
            from pymongo import MongoClient
            self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        else:
            import mongomock
            self.client = mongomock.MongoClient()

    def open(self):
        from storage import Storage
        store = Storage(name=self.name, client=self.client)
        store.close = lambda: None
        return store

    def drop(self):
        self.client.drop_database(self.name)
        self.client.close()
