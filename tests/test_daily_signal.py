import datetime as dt
import unittest

from daily_signal import Source, parse_feed, render_markdown


SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Signal Channel</title>
  <entry>
    <id>yt:video:abc123</id>
    <yt:videoId>abc123</yt:videoId>
    <title>Major AI Lab Ships a New Tool</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <published>2026-05-02T01:00:00+00:00</published>
    <media:group>
      <media:description>Launch details, developer impact, and early reactions.</media:description>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:old999</id>
    <yt:videoId>old999</yt:videoId>
    <title>Old News</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=old999"/>
    <published>2026-04-20T01:00:00+00:00</published>
  </entry>
</feed>
"""


class DailySignalTest(unittest.TestCase):
    def test_parse_feed_filters_by_time_and_extracts_links(self):
        source = Source(name="Signal", kind="channel_id", value="channel", limit=10)
        since = dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc)

        items = parse_feed(SAMPLE_FEED, source, since)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "abc123")
        self.assertEqual(items[0].channel, "Signal Channel")
        self.assertEqual(items[0].url, "https://www.youtube.com/watch?v=abc123")
        self.assertIn("developer impact", items[0].summary)

    def test_render_markdown_includes_sources(self):
        source = Source(name="Signal", kind="channel_id", value="channel", limit=10)
        since = dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc)
        items = parse_feed(SAMPLE_FEED, source, since)

        markdown = render_markdown(
            items=items,
            ai_brief=None,
            errors=[],
            config={"title": "Test Signal", "max_events": 3},
            brief_date=dt.date(2026, 5, 2),
            since=since,
        )

        self.assertIn("# Test Signal - 2026-05-02", markdown)
        self.assertIn("[Major AI Lab Ships a New Tool](https://www.youtube.com/watch?v=abc123)", markdown)
        self.assertIn("## 原始来源", markdown)


if __name__ == "__main__":
    unittest.main()
