import os
import unittest

from tests import CONFIG, FIXTURES
from config import Config
from parsing import extract_links
from rules import SourceRules
from urls import normalize_url

WIKI = 'https://ru.wikipedia.org/wiki/'
FILMRU = 'https://www.film.ru/'


class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config.load(CONFIG)
        cls.wiki = SourceRules(cfg.source('wikipedia'))
        cls.filmru = SourceRules(cfg.source('filmru'))

    def test_wikipedia_category_is_crawled_but_not_stored(self):
        url = WIKI + 'Категория:Фильмы_по_алфавиту'
        self.assertTrue(self.wiki.acceptable(url))
        self.assertTrue(self.wiki.may_follow(url))
        self.assertFalse(self.wiki.storable(url))

    def test_wikipedia_article_is_stored_but_links_not_followed(self):
        url = WIKI + 'Сталкер_(фильм)'
        self.assertTrue(self.wiki.acceptable(url))
        self.assertTrue(self.wiki.storable(url))
        self.assertFalse(self.wiki.may_follow(url))

    def test_wikipedia_only_film_categories_crawled(self):
        for name in ('Фильмы_по_алфавиту', 'Актрисы_по_алфавиту', 'Фильмы', 'Фильмы_1995_года',
                     'Фильмы_Франции_1995_года', 'Телесериалы_России', 'Актёры_по_странам',
                     'Кинорежиссёры_СССР', 'Экранизации_литературных_произведений_по_жанрам_фильмов'):
            self.assertTrue(self.wiki.acceptable(WIKI + 'Категория:' + name), name)
        for name in ('Категории_произведений_искусства_по_алфавиту', 'Режиссёры_по_алфавиту',
                     'Алфавитные_категории,_наполняемые_шаблонами', 'Книги_1995_года',
                     'Википедия:Статьи_о_фильмах_без_источников',
                     'Категории_кинематографической_тематики_по_алфавиту'):
            self.assertFalse(self.wiki.acceptable(WIKI + 'Категория:' + name), name)

    def test_wikipedia_red_links_and_actions_denied(self):
        for url in (WIKI + 'Категория:Фильмы_Исландии_2025_года?action=edit&redlink=1',
                    WIKI + 'Сталкер_(фильм)?action=history', WIKI + 'Сталкер_(фильм)?oldid=1'):
            self.assertFalse(self.wiki.acceptable(url), url)

    def test_wikipedia_index_php_forbidden_anyway(self):
        url = normalize_url('https://ru.wikipedia.org/w/index.php?title=Категория:Фильмы_по_алфавиту'
                            '&pagefrom=36+безумных+кулаков#mw-pages')
        self.assertFalse(self.wiki.acceptable(url))

    def test_wikipedia_lists_not_stored(self):
        for url in (WIKI + 'Список_фильмов_СССР', WIKI + 'Заглавная_страница'):
            self.assertFalse(self.wiki.storable(url), url)

    def test_wikipedia_service_pages_and_media_denied(self):
        for url in (WIKI + 'Служебная:Поиск', WIKI + 'Обсуждение:Сталкер',
                    WIKI + 'Файл:Stalker.jpg', WIKI + 'Шаблон:Фильмы_Андрея_Тарковского',
                    WIKI + 'Обсуждение_участника:X', WIKI + 'Постер.png',
                    'https://en.wikipedia.org/wiki/Stalker_(1979_film)'):
            self.assertFalse(self.wiki.acceptable(url), url)

    def test_filmru_articles_stored(self):
        for url in (FILMRU + 'articles/recenziya-na-film-uchenik', FILMRU + 'articles/15-glavnyh-filmov-fevralya-1'):
            self.assertTrue(self.filmru.acceptable(url), url)
            self.assertTrue(self.filmru.storable(url), url)
            self.assertFalse(self.filmru.may_follow(url), url)

    def test_filmru_lists_followed_not_stored(self):
        for url in (FILMRU + 'topic/review', FILMRU + 'topic/review?page=17', FILMRU + 'topic/top10?page=0'):
            self.assertTrue(self.filmru.acceptable(url), url)
            self.assertTrue(self.filmru.may_follow(url), url)
            self.assertFalse(self.filmru.storable(url), url)

    def test_filmru_other_pages_denied(self):
        for url in (FILMRU + 'movies/cheburashka-2', FILMRU + 'topic/news', FILMRU + 'search?q=x',
                    FILMRU + 'topic/review/ajax?page=2', FILMRU + 'user/31173', FILMRU + 'a-z/2026'):
            self.assertFalse(self.filmru.acceptable(url), url)

    def test_filmru_real_list_page(self):
        with open(os.path.join(FIXTURES, 'filmru_review_list.html'), encoding='utf-8') as f:
            links = [u for u in extract_links(f.read(), FILMRU + 'topic/review') if self.filmru.acceptable(u)]
        self.assertEqual(sum(self.filmru.storable(u) for u in links), 28)

    def test_blocked_marker(self):
        rules = SourceRules({'name': 'x', 'blocked_marker': 'Роботам запрещено'})
        self.assertTrue(rules.is_blocked('Кино-Театр.Ру Роботам запрещено посещать наш сайт.'))
        self.assertFalse(rules.is_blocked('Обычная статья'))
        self.assertFalse(self.wiki.is_blocked('Роботам запрещено посещать наш сайт'))

    def test_empty_rules_allow_everything(self):
        rules = SourceRules({'name': 'x'})
        self.assertTrue(rules.acceptable('https://a/'))
        self.assertTrue(rules.storable('https://a/'))
        self.assertTrue(rules.may_follow('https://a/'))


if __name__ == '__main__':
    unittest.main()
