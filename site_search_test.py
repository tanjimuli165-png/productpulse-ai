import unittest
from unittest.mock import patch, Mock
from app.collectors.site_search import QuoraCollector, InstagramCollector

HTML = '''<div class="result"><a class="result__a" href="https://www.quora.com/example-question">Example question</a><a class="result__snippet">People describe a recurring problem and ask how to solve it.</a></div>'''

class SiteSearchCollectorTest(unittest.TestCase):
    @patch('app.collectors.site_search.requests.get')
    def test_quora_uses_public_search_index(self, get):
        response = Mock(); response.raise_for_status.return_value = None; response.text = HTML
        get.return_value = response
        rows = QuoraCollector().collect('client onboarding', 3)
        self.assertEqual(rows[0].source, 'Quora')
        self.assertIn('public_search_index', rows[0].metadata['collection_method'])

    @patch('app.collectors.site_search.requests.get')
    def test_instagram_uses_public_search_index(self, get):
        response = Mock(); response.raise_for_status.return_value = None; response.text = HTML.replace('quora.com', 'instagram.com')
        get.return_value = response
        rows = InstagramCollector().collect('client onboarding', 3)
        self.assertEqual(rows[0].source, 'Instagram')

if __name__ == '__main__':
    unittest.main()
