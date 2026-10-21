import requests

import urls
from fetcher import Response
from parsing import decode


OFFLINE = object()


class FakeFetcher:
    def __init__(self, pages=None, forbidden=(), busy=()):
        self.pages = dict(pages or {})
        self.forbidden = set(forbidden)
        self.busy = set(busy)
        self.requests = []
        self.pauses = []

    def set_host_delay(self, host, delay):
        pass

    def pause_host(self, host, seconds):
        self.pauses.append((host, seconds))

    def wait_time(self, host):
        return 5.0 if host in self.busy else 0.0

    def last_request(self, host):
        hosts = [urls.host(u) for u in self.requests]
        return len(hosts) - hosts[::-1].index(host) if host in hosts else 0

    def allowed(self, url):
        return url not in self.forbidden

    def get(self, url, etag=None, last_modified=None):
        self.requests.append(url)
        if url not in self.pages:
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError(f'404 для {url}', response=response)
        if self.pages[url] is OFFLINE:
            raise requests.ConnectionError(f'нет сети: {url}')
        page = self.pages[url]
        html, headers = page if isinstance(page, tuple) else (page, {})
        if etag and headers.get('ETag') == etag:
            return Response(304, headers=headers)
        raw = html.encode('utf-8')
        text, encoding = decode(raw, 'text/html; charset=utf-8')
        return Response(200, raw, text, encoding, headers)
