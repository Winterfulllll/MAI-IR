import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field

import requests

from parsing import decode


@dataclass
class Response:
    status: int
    raw: bytes = None
    html: str = None
    encoding: str = None
    headers: dict = field(default_factory=dict)

    @property
    def not_modified(self):
        return self.status == 304


class Fetcher:
    def __init__(self, logic):
        self.delay = float(logic.get('delay', 1.0))
        self.timeout = float(logic.get('timeout', 30))
        self.user_agent = logic.get('user_agent', 'MAI-IR-crawler/1.0')
        self.respect_robots = bool(logic.get('respect_robots', True))
        self.session = requests.Session()
        self.session.headers['User-Agent'] = self.user_agent
        self._last_request = {}
        self._host_delay = {}
        self._paused_until = {}
        self._robots = {}

    def set_host_delay(self, host, delay):
        self._host_delay[host] = float(delay)

    def pause_host(self, host, seconds):
        self._paused_until[host] = time.time() + seconds

    def wait_time(self, host):
        now = time.time()
        paused = self._paused_until.get(host, 0.0) - now
        last = self._last_request.get(host)
        if last is None:
            return max(0.0, paused)
        return max(0.0, paused, self._host_delay.get(host, self.delay) - (now - last))

    def last_request(self, host):
        return self._last_request.get(host, 0.0)

    def _wait(self, host):
        pause = self.wait_time(host)
        if pause > 0:
            time.sleep(pause)
        self._last_request[host] = time.time()

    def allowed(self, url):
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(parts.scheme, host)
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def _load_robots(self, scheme, host):
        self._wait(host)
        try:
            r = self.session.get(f'{scheme}://{host}/robots.txt', timeout=self.timeout)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp

    def get(self, url, etag=None, last_modified=None):
        self._wait(urllib.parse.urlsplit(url).netloc)

        headers = {}
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified

        r = self.session.get(url, headers=headers, timeout=self.timeout)
        if r.status_code == 304:
            return Response(304, headers=r.headers)
        r.raise_for_status()
        html, encoding = decode(r.content, r.headers.get('Content-Type', ''))
        return Response(r.status_code, r.content, html, encoding, r.headers)
