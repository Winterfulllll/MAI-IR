import re


def _compile(patterns):
    return [re.compile(p) for p in patterns or []]


def _passes(url, allow, deny):
    if allow and not any(p.search(url) for p in allow):
        return False
    return not any(p.search(url) for p in deny)


class SourceRules:
    def __init__(self, source):
        self.name = source['name']
        self.allow = _compile(source.get('allow'))
        self.deny = _compile(source.get('deny'))
        self.store_allow = _compile(source.get('store_allow'))
        self.store_deny = _compile(source.get('store_deny'))
        self.follow_only = _compile(source.get('follow_only'))
        self.blocked_marker = source.get('blocked_marker')

    def acceptable(self, url):
        return _passes(url, self.allow, self.deny)

    def storable(self, url):
        return _passes(url, self.store_allow, self.store_deny)

    def may_follow(self, url):
        return not self.follow_only or any(p.search(url) for p in self.follow_only)

    def is_blocked(self, text):
        return bool(self.blocked_marker) and self.blocked_marker in text
