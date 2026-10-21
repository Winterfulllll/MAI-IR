import os
import unittest

from tests import CONFIG, FIXTURES, SAMPLES
from config import Config
from parsing import content_hash, container_body, decode, extract_links, extract_text, extract_title


KINO_TEATR = {'name': 'kino-teatr', 'container': ['art article_text', 'itemprop=description']}


def sample(*parts):
    with open(os.path.join(SAMPLES, *parts), 'rb') as f:
        return f.read()


class DecodeTest(unittest.TestCase):
    TEXT = 'Кинофильм'

    def test_header_wins(self):
        html, enc = decode(self.TEXT.encode('cp1251'), 'text/html; charset=windows-1251')
        self.assertEqual((html, enc), (self.TEXT, 'windows-1251'))

    def test_meta_charset_when_header_silent(self):
        raw = ('<meta charset="windows-1251">' + self.TEXT).encode('cp1251')
        html, enc = decode(raw, 'text/html')
        self.assertEqual(enc, 'windows-1251')
        self.assertIn(self.TEXT, html)

    def test_meta_http_equiv(self):
        raw = ('<meta http-equiv="Content-Type" content="text/html; charset=koi8-r">'
               + self.TEXT).encode('koi8-r')
        self.assertIn(self.TEXT, decode(raw, '')[0])

    def test_utf8_fallback(self):
        self.assertEqual(decode(self.TEXT.encode('utf-8'), ''), (self.TEXT, 'utf-8'))

    def test_unknown_charset_ignored(self):
        html, enc = decode(self.TEXT.encode('utf-8'), 'text/html; charset=x-bogus')
        self.assertEqual((html, enc), (self.TEXT, 'utf-8'))

    def test_real_kinoteatr_page_is_cp1251(self):
        html, enc = decode(sample('kinoteatr', 'article_kino_8447.html'), '')
        self.assertEqual(enc, 'windows-1251')
        self.assertIn('Место встречи изменить нельзя', html)


class ContainerTest(unittest.TestCase):
    def test_nested_divs_balanced(self):
        html = ('<div class="menu">меню</div><div class="main"><div>а</div>б</div>'
                '<div class="footer">подвал</div>')
        self.assertEqual(container_body(html, 'main'), '<div>а</div>б')

    def test_attribute_order_does_not_matter(self):
        html = '<div itemprop="text" class="art article_text">текст</div>подвал'
        self.assertEqual(container_body(html, 'art article_text'), 'текст')

    def test_selector_by_other_attribute(self):
        html = '<div class="x"><div itemprop="description">био</div></div>'
        self.assertEqual(container_body(html, 'itemprop=description'), 'био')

    def test_missing_container(self):
        self.assertIsNone(container_body('<div class="other">x</div>', 'main'))


class ExtractTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config.load(CONFIG)
        cls.wiki = cfg.source('wikipedia')
        cls.kino = KINO_TEATR
        cls.filmru = cfg.source('filmru')

    def test_scripts_styles_tables_dropped(self):
        html = ('<p>Сюжет фильма</p><script>var x=1</script><style>p{}</style>'
                '<table><tr><td>инфобокс</td></tr></table><noscript>включите JS</noscript>конец')
        self.assertEqual(extract_text(html, {}), 'Сюжет фильма конец')

    def test_entities_decoded_not_dropped(self):
        html = ('<p>&laquo;Сталкер&raquo; &mdash; фильм&nbsp;Тарковского &amp; Стругацких, '
                '&#171;x&#187; &#x2116;1, &lt;b&gt; &mdash; не тег</p>')
        self.assertEqual(extract_text(html, {}),
                         '«Сталкер» — фильм Тарковского & Стругацких, «x» №1, <b> — не тег')

    def test_cut_after_tail_sections(self):
        html = '<p>Текст</p><h2 id="Примечания">Примечания</h2><p>ссылки</p>'
        self.assertEqual(extract_text(html, self.wiki), 'Текст')

    def test_first_found_container_used(self):
        html = '<div itemprop="description">био</div><div class="footer">подвал</div>'
        self.assertEqual(extract_text(html, self.kino), 'био')

    def test_wikipedia_sample_has_no_navigation(self):
        html, _ = decode(sample('wikipedia', 'film_stalker.html'), '')
        text = extract_text(html, self.wiki)
        self.assertIn('Тарковск', text)
        for junk in ('Навигация', 'Заглавная страница', 'Примечания', 'Политика конфиденциальности'):
            self.assertNotIn(junk, text)

    def test_kinoteatr_article_has_no_site_footer(self):
        html, _ = decode(sample('kinoteatr', 'article_kino_8447.html'), '')
        text = extract_text(html, self.kino)
        self.assertIn('Место встречи', text)
        for junk in ('Мобильная версия', 'Реклама Контакты', 'пользовательского соглашения'):
            self.assertNotIn(junk, text)

    def test_filmru_review_text_only(self):
        with open(os.path.join(FIXTURES, 'filmru_review.html'), 'rb') as f:
            html, enc = decode(f.read(), '')
        text = extract_text(html, self.filmru)
        self.assertEqual(enc, 'utf-8')
        self.assertTrue(text.startswith('Безбожно упрощенная'))
        self.assertTrue(text.endswith('чтобы не быть полным «фейлом».'))
        for junk in ('Войти', 'Поделиться', 'Текст: Борис Иванов'):
            self.assertNotIn(junk, text)

    def test_kinoteatr_bio_uses_description_container(self):
        html, _ = decode(sample('kinoteatr', 'acter_w_2800_bio.html'), '')
        text = extract_text(html, self.kino)
        self.assertTrue(text.startswith('Родилась'))
        self.assertNotIn('Мобильная версия', text)


class LinksTitleHashTest(unittest.TestCase):
    def test_links_normalized_and_deduplicated(self):
        html = ('<a href="/wiki/A#x">1</a><a class="c" href=\'/wiki/A\'>2</a>'
                '<a href="mailto:x@y">3</a><a href="https://Other.org/b">4</a>')
        self.assertEqual(extract_links(html, 'https://ru.wikipedia.org/wiki/S'),
                         ['https://ru.wikipedia.org/wiki/A', 'https://other.org/b'])

    def test_links_from_real_page(self):
        html, _ = decode(sample('wikipedia', 'film_stalker.html'), '')
        links = extract_links(html, 'https://ru.wikipedia.org/wiki/Сталкер_(фильм)')
        self.assertIn('https://ru.wikipedia.org/wiki/Тарковский,_Андрей_Арсеньевич', links)
        self.assertEqual(len(links), len(set(links)))
        self.assertFalse(any('#' in u or 'amp;' in u for u in links))
        self.assertFalse(any('%D0' in u.split('?')[0] for u in links))

    def test_html_entities_in_href(self):
        html = '<a href="/w/index.php?title=A&amp;action=edit">x</a>'
        self.assertEqual(extract_links(html, 'https://ru.wikipedia.org/'),
                         ['https://ru.wikipedia.org/w/index.php?action=edit&title=A'])

    def test_title(self):
        self.assertEqual(extract_title('<title>\n Сталкер <b>(фильм)</b>\n</title>'),
                         'Сталкер (фильм)')
        self.assertEqual(extract_title('<p>нет</p>'), '')
        self.assertEqual(extract_title('<title>&laquo;Сталкер&raquo; &mdash; Википедия</title>'),
                         '«Сталкер» — Википедия')

    def test_hash_depends_only_on_text(self):
        self.assertEqual(content_hash('текст'), content_hash('текст'))
        self.assertNotEqual(content_hash('текст'), content_hash('текст.'))
        self.assertEqual(len(content_hash('')), 64)


if __name__ == '__main__':
    unittest.main()
