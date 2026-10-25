"""Feed-level refusals and walled-article fallbacks, 2026-10-25.

1. A feed that answers 401/403 to the default httpx request is asked once more
   with browser headers (Atlanta Magazine, Florida Design, Northshore Magazine,
   measured from a GitHub runner 2026-10-20: 403 bare, 200 as a browser).
   A feed that reads today is asked exactly once, with no extra headers.
2. _extract_content keeps the longest text (Dengarden: 45-char summary,
   4,848-char content:encoded).
3. The matcher reads the feed's own body when the article page is walled,
   but only when that body is long enough to be the article, so a 166-char
   New York Times teaser still counts unread.

No network in this file.
"""
from __future__ import annotations

import asyncio
import urllib.error
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import feedparser
import pytest

import src.ai.summarizer as summarizer
from src.models import RSSSourceConfig
from src.scrapers.rss import FEED_HEADERS, RSSScraper

FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Test</title>
  <item>
    <guid>entry-1</guid>
    <title>A Kitchen</title>
    <link>https://example.com/a-kitchen</link>
    <pubDate>Fri, 24 Apr 2026 12:00:00 GMT</pubDate>
    <description>Short teaser.</description>
    <content:encoded><![CDATA[<p>%s</p>]]></content:encoded>
  </item>
</channel></rss>
"""

LONG_BODY = ("The renovation took nine months. " * 40) + \
    "&quot;We wanted the light to do the work,&quot; says designer Krystal Reinhard."


def _resp(status, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if status >= 400:
        import httpx
        req = httpx.Request("GET", "https://example.com/feed/")
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "refused", request=req, response=httpx.Response(status, request=req))
    else:
        r.raise_for_status.return_value = None
    return r


SINCE = datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)
SOURCE = RSSSourceConfig(name="The Atlanta Magazine", url="https://example.com/feed/")


def test_a_refused_feed_is_retried_with_browser_headers_and_reads():
    client = AsyncMock()
    client.get.side_effect = [_resp(403), _resp(200, FEED % LONG_BODY)]
    items = asyncio.run(RSSScraper([SOURCE], client).fetch(SINCE))

    assert len(items) == 1
    assert client.get.call_count == 2
    assert "headers" not in client.get.call_args_list[0].kwargs
    sent = client.get.call_args_list[1].kwargs["headers"]
    assert sent is FEED_HEADERS
    assert sent["User-Agent"].startswith("Mozilla/5.0")
    assert "rss+xml" in sent["Accept"]


def test_a_feed_that_reads_today_is_asked_once_with_no_extra_headers():
    client = AsyncMock()
    client.get.side_effect = [_resp(200, FEED % LONG_BODY)]
    items = asyncio.run(RSSScraper([SOURCE], client).fetch(SINCE))

    assert len(items) == 1
    assert client.get.call_count == 1
    assert "headers" not in client.get.call_args_list[0].kwargs


def test_a_second_refusal_fails_cleanly_with_no_items():
    client = AsyncMock()
    client.get.side_effect = [_resp(403), _resp(403)]
    items = asyncio.run(RSSScraper([SOURCE], client).fetch(SINCE))

    assert items == []
    assert client.get.call_count == 2


def test_a_404_is_not_retried():
    client = AsyncMock()
    client.get.side_effect = [_resp(404)]
    items = asyncio.run(RSSScraper([SOURCE], client).fetch(SINCE))

    assert items == []
    assert client.get.call_count == 1


def test_extract_content_keeps_the_full_body_over_a_short_summary():
    entry = feedparser.parse(FEED % LONG_BODY).entries[0]
    text = RSSScraper._extract_content(None, entry)
    assert "Krystal Reinhard" in text
    assert len(text) > 1000


# ------------------------------------------------------- matcher fallback

ROSTER = ["Krystal Reinhard"]


@pytest.fixture
def walled(monkeypatch):
    monkeypatch.setattr(summarizer, "get_clean_sources", lambda: list(ROSTER))
    monkeypatch.setattr(summarizer, "_CLEAN_SOURCES_CACHE", list(ROSTER))
    summarizer._ARTICLE_SOURCE_CACHE.clear()
    summarizer.reset_unread_articles()

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(
            "u", 403, "refused", {}, __import__("io").BytesIO(b"captcha-delivery.com"))

    monkeypatch.setattr(summarizer._urlreq, "urlopen", refuse)
    yield
    summarizer._ARTICLE_SOURCE_CACHE.clear()
    summarizer.reset_unread_articles()


def test_walled_article_is_read_from_the_feed_body(walled):
    url = "https://dengarden.com/walled-article"
    found = summarizer.find_press_club_sources(
        url, "", "Dengarden", feed_body="<p>" + LONG_BODY + "</p>")
    assert found == ["Krystal Reinhard"]
    assert url not in summarizer.unread_articles()


def test_a_short_teaser_does_not_count_as_a_read(walled):
    url = "https://www.nytimes.com/walled-article"
    teaser = "A designer in Los Angeles, Krystal Reinhard, reworks a kitchen."
    found = summarizer.find_press_club_sources(url, "", "New York Times", feed_body=teaser)
    assert found == []
    assert url in summarizer.unread_articles()


def test_no_feed_body_keeps_the_old_unread_behaviour(walled):
    url = "https://dengarden.com/no-body"
    assert summarizer.find_press_club_sources(url, "", "Dengarden") == []
    assert url in summarizer.unread_articles()
