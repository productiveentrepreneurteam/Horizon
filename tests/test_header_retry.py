"""find_press_club_sources() sends a full browser header set and retries a 403 once.

Root cause, measured 2026-09-10: The Spruce, Southern Living, Real Simple and
Better Homes & Gardens refuse the matcher's OWN header set intermittently (7 of
28 interleaved live requests failed with HTTP 403; a full browser header set
failed 0 of 28). The refusal is not a wall: the same URL that 403'd once 200'd
on a bare retry. So the fix is (1) send the fields a real browser navigation
sends that were missing -- Sec-Fetch-*, client hints, Upgrade-Insecure-Requests
-- and (2) retry once on a 403 before giving up.

No network in this file. `_urlreq.urlopen` is swapped for a fake that records
every Request it was handed and can be told to fail N times before succeeding,
the same mocking shape tests/../horizon-unread-articles-2026-09-09/test_unread_
articles.py uses against this same function.
"""
from __future__ import annotations

import urllib.error

import pytest

import src.ai.summarizer as summarizer

ROSTER = ["Krystal Reinhard"]

GOOD_PAGE = (
    "<!DOCTYPE html><html><head><title>A Kitchen</title></head>"
    "<body class=\"article-page\"><article class=\"article-body\">"
    "<p>" + ("The renovation took nine months. " * 40) + "</p>"
    "<p>\"We wanted the light to do the work,\" says designer "
    "Krystal Reinhard, who led the project.</p>"
    "<p>" + ("The renovation took nine months. " * 40) + "</p>"
    "</article></body></html>"
)


class _Resp:
    def __init__(self, body):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError(
        "u", code, "refused", {}, __import__("io").BytesIO(b"refused")
    )


class _FakeOpener:
    """Records every Request; fails `fail_times` times with `fail_code`, then 200s."""

    def __init__(self, fail_times=0, fail_code=403, body=GOOD_PAGE):
        self.fail_times = fail_times
        self.fail_code = fail_code
        self.body = body
        self.calls = []  # list[Request]

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if len(self.calls) <= self.fail_times:
            raise _http_error(self.fail_code)
        return _Resp(self.body)


@pytest.fixture(autouse=True)
def _stub_roster(monkeypatch):
    monkeypatch.setattr(summarizer, "get_clean_sources", lambda: list(ROSTER))
    monkeypatch.setattr(summarizer, "_CLEAN_SOURCES_CACHE", list(ROSTER))
    summarizer._ARTICLE_SOURCE_CACHE.clear()
    yield
    summarizer._ARTICLE_SOURCE_CACHE.clear()


def _sent_headers(req):
    """Request.add_header() stores keys via .capitalize(), so read case-insensitively."""
    return {k.lower(): v for k, v in req.header_items()}


# ---------------------------------------------------------------- the header set

def test_sends_the_full_browser_header_set(monkeypatch):
    fake = _FakeOpener(fail_times=0)
    monkeypatch.setattr(summarizer._urlreq, "urlopen", fake)

    summarizer.find_press_club_sources(
        "https://www.thespruce.com/some-article", "")

    assert len(fake.calls) == 1
    hdrs = _sent_headers(fake.calls[0])
    # The matcher headers that were already there (2026-08-25 fix):
    assert hdrs["user-agent"].startswith("Mozilla/5.0")
    assert "text/html" in hdrs["accept"]
    assert hdrs["accept-language"] == "en-US,en;q=0.9"
    # The fields a real browser navigation sends that this fix adds:
    assert hdrs["upgrade-insecure-requests"] == "1"
    assert hdrs["sec-fetch-dest"] == "document"
    assert hdrs["sec-fetch-mode"] == "navigate"
    assert hdrs["sec-fetch-site"] == "none"
    assert hdrs["sec-fetch-user"] == "?1"
    assert "sec-ch-ua" in hdrs
    assert hdrs["sec-ch-ua-mobile"] == "?0"
    assert "windows" in hdrs["sec-ch-ua-platform"].lower()


# ---------------------------------------------------------------- the retry

def test_a_403_is_retried_once_and_the_retry_can_succeed(monkeypatch):
    fake = _FakeOpener(fail_times=1, fail_code=403)
    monkeypatch.setattr(summarizer._urlreq, "urlopen", fake)

    found = summarizer.find_press_club_sources(
        "https://www.southernliving.com/retry-recovers", "")

    assert found == ["Krystal Reinhard"]
    assert len(fake.calls) == 2, "one 403 + one retry"
    # Both attempts carry the same full header set -- the retry is not a
    # degraded second try, it uses exactly what the first attempt used.
    assert _sent_headers(fake.calls[0]) == _sent_headers(fake.calls[1])


def test_a_second_403_still_fails_cleanly_no_crash(monkeypatch):
    fake = _FakeOpener(fail_times=2, fail_code=403)
    monkeypatch.setattr(summarizer._urlreq, "urlopen", fake)

    found = summarizer.find_press_club_sources(
        "https://www.realsimple.com/still-refused", "")

    assert found == []
    assert len(fake.calls) == 2, "exactly one retry, then give up"


def test_a_non_403_error_is_not_retried(monkeypatch):
    """A 500, a timeout, anything that is not 403 falls straight to the outer
    except unchanged -- the retry is specific to the intermittent-403 shape
    measured on 2026-09-10, not a general retry-everything policy."""
    fake = _FakeOpener(fail_times=1, fail_code=500)
    monkeypatch.setattr(summarizer._urlreq, "urlopen", fake)

    found = summarizer.find_press_club_sources(
        "https://www.bhg.com/server-error", "")

    assert found == []
    assert len(fake.calls) == 1, "no retry for a non-403"


def test_success_on_first_try_needs_no_retry(monkeypatch):
    fake = _FakeOpener(fail_times=0)
    monkeypatch.setattr(summarizer._urlreq, "urlopen", fake)

    found = summarizer.find_press_club_sources(
        "https://www.thespruce.com/clean-first-try", "")

    assert found == ["Krystal Reinhard"]
    assert len(fake.calls) == 1
