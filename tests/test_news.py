import datetime,unittest
from unittest.mock import patch
import news
class News(unittest.TestCase):
 def test_only_recent_dated_official_sources(self):
  now=datetime.datetime(2026,10,18,tzinfo=datetime.timezone.utc)
  raw=b'''<rss><channel><item><title>Recent release</title><link>https://github.blog/release</link><pubDate>Sat, 17 Oct 2026 12:00:00 GMT</pubDate><description>&lt;p&gt;Published details.&lt;/p&gt;</description></item><item><title>Old</title><link>https://github.blog/old</link><pubDate>Sat, 03 Oct 2026 12:00:00 GMT</pubDate></item><item><title>Offsite</title><link>https://evil.example/news</link><pubDate>Sat, 17 Oct 2026 12:00:00 GMT</pubDate></item><item><title>Undated</title><link>https://github.blog/no-date</link></item></channel></rss>'''
  items=news.parse(raw,'GitHub',{'github.blog'},now);self.assertEqual(len(items),1);self.assertEqual(items[0]['summary'],'Published details.')
 def test_entities_rejected_and_no_fresh_story_holds(self):
  with self.assertRaises(news.NewsUnavailable):news.parse(b'<!DOCTYPE foo [<!ENTITY x "bad">]><rss/>','GitHub',{'github.blog'},datetime.datetime.now(datetime.timezone.utc))
  with patch('news.fetch',return_value=[]),patch('news.FETCHED',0):
   with self.assertRaises(news.NewsUnavailable):news.select()
