import yaml


class Config:
    def __init__(self, raw):
        self.db = raw['db']
        self.logic = raw['logic']
        self.recrawl = raw.get('recrawl', {})
        self.sources = raw['sources']

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as f:
            return cls(yaml.safe_load(f))

    def source(self, name):
        for s in self.sources:
            if s['name'] == name:
                return s
        return None
