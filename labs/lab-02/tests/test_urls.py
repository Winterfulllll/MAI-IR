import unittest

from urls import host, normalize_url


class NormalizeUrlTest(unittest.TestCase):
    def test_relative_link_joined_with_base(self):
        self.assertEqual(
            normalize_url('/wiki/Сталкер', 'https://ru.wikipedia.org/wiki/Тарковский'),
            'https://ru.wikipedia.org/wiki/Сталкер')
        self.assertEqual(
            normalize_url('../bio/', 'https://www.kino-teatr.ru/kino/acter/m/ros/2979/photo/'),
            'https://www.kino-teatr.ru/kino/acter/m/ros/2979/bio/')

    def test_fragment_dropped(self):
        self.assertEqual(normalize_url('https://ru.wikipedia.org/wiki/Сталкер#Сюжет'),
                         'https://ru.wikipedia.org/wiki/Сталкер')

    def test_percent_encoding_decoded(self):
        self.assertEqual(
            normalize_url('https://ru.wikipedia.org/wiki/%D0%A4%D0%B8%D0%BB%D1%8C%D0%BC'),
            'https://ru.wikipedia.org/wiki/Фильм')

    def test_reserved_escapes_kept(self):
        self.assertEqual(normalize_url('https://ru.wikipedia.org/wiki/1_%25_(%D1%84)'),
                         'https://ru.wikipedia.org/wiki/1_%25_(ф)')
        self.assertEqual(normalize_url('https://a.ru/x%2fy%3Fz'), 'https://a.ru/x%2Fy%3Fz')

    def test_tracking_params_removed_and_rest_sorted(self):
        self.assertEqual(
            normalize_url('https://example.com/p?utm_source=x&b=2&gclid=1&a=1&ref=y'),
            'https://example.com/p?a=1&b=2')

    def test_scheme_host_case_and_default_port(self):
        self.assertEqual(normalize_url('HTTPS://RU.Wikipedia.ORG:443/wiki/A'),
                         'https://ru.wikipedia.org/wiki/A')
        self.assertEqual(normalize_url('http://example.com:80/a'), 'http://example.com/a')
        self.assertEqual(normalize_url('http://example.com:8080/a'),
                         'http://example.com:8080/a')

    def test_double_slash_kept_and_empty_path(self):
        self.assertEqual(normalize_url('https://ru.wikipedia.org/wiki/.hack//SIGN'),
                         'https://ru.wikipedia.org/wiki/.hack//SIGN')
        self.assertEqual(normalize_url('https://example.com'), 'https://example.com/')

    def test_non_http_links_rejected(self):
        for href in ('mailto:a@b.ru', 'javascript:void(0)', 'ftp://example.com/f'):
            self.assertIsNone(normalize_url(href, 'https://example.com/'))

    def test_idempotent(self):
        url = normalize_url('https://ru.wikipedia.org/wiki/%D0%90?b=1&a=2#x')
        self.assertEqual(normalize_url(url), url)

    def test_host(self):
        self.assertEqual(host('https://www.kino-teatr.ru/kino/art/'), 'www.kino-teatr.ru')


if __name__ == '__main__':
    unittest.main()
