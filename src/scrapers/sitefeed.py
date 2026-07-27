"""Build RSS feeds for outlets that don't publish one.

Some of Alyssa's ranked outlets have no RSS feed at all. Those used to be
covered by rss.app-generated feeds, which all went dark at once when the
trial account behind them lapsed (every one started answering HTTP 402
Payment Required). This module replaces that paid service with about a
hundred lines of in-house code and no new dependencies: httpx and
BeautifulSoup are already base requirements.

How it works: fetch the outlet's section page with browser-like headers,
collect every link whose URL matches that outlet's article pattern, use
the link text as the title, and write a plain RSS 2.0 file. The daily
workflow runs this before Horizon and drops the results in docs/feeds/,
which src/scrapers/rss.py reads via file:// URLs in the same run.

Config lives in data/sitefeeds.json. Deliberately forgiving: one outlet
failing never fails the run, because a missing feed should cost that
outlet's articles for a day, not the whole digest.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from xml.sax.saxutils import escape

import httpx
from bs4 import BeautifulSoup

# Plain urllib gets 401/403 from several of these outlets. A full set of
# browser headers gets 200 from all but the Cloudflare-challenged ones.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

MAX_ITEMS = 30
TIMEOUT = 30.0


def _extract(html: str, page_url: str, spec: dict) -> list:
    """Pull (title, url) pairs out of a section page."""
    soup = BeautifulSoup(html, "html.parser")

    scope = soup
    container = spec.get("container")
    if container:
        found = soup.select(container)
        if found:
            # The container with the most links is the article stream; the
            # others are nav and promo blocks.
            scope = max(found, key=lambda e: len(e.find_all("a", href=True)))

    pattern = re.compile(spec["pattern"])
    deny = re.compile(spec["deny"]) if spec.get("deny") else None
    min_title = int(spec.get("min_title", 18))

    best = {}
    for a in scope.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0].split("?")[0]
        if not pattern.search(url):
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < min_title:
            continue
        if deny and deny.search(title):
            continue
        # The same article often appears as both an image link and a text
        # link; keep whichever gave us the fuller title.
        if url not in best or len(title) > len(best[url]):
            best[url] = title

    return [{"title": t, "url": u} for u, t in best.items()][:MAX_ITEMS]


def _render(name: str, page_url: str, items: list) -> str:
    """Render RSS 2.0. Every item is stamped with the build time.

    These pages carry no reliable per-article date, so we stamp "now".
    Horizon runs on a 24-hour window and dedupes on URL, so an article is
    only ever new to the digest once - the stamp decides which run picks
    it up, not how many times it appears.
    """
    now = format_datetime(datetime.now(timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>" + escape(name) + "</title>",
        "<link>" + escape(page_url) + "</link>",
        "<description>" + escape(name) + " - generated for the Press House Daily Digest</description>",
        "<lastBuildDate>" + now + "</lastBuildDate>",
    ]
    for it in items:
        parts += [
            "<item>",
            "<title>" + escape(it["title"]) + "</title>",
            "<link>" + escape(it["url"]) + "</link>",
            '<guid isPermaLink="true">' + escape(it["url"]) + "</guid>",
            "<pubDate>" + now + "</pubDate>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts)


def build_all(config_path, out_dir) -> int:
    """Build every configured feed. Returns the number written."""
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = json.loads(config_path.read_text(encoding="utf-8")).get("sites", [])
    written = 0

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for spec in specs:
            name = spec.get("name", "?")
            if not spec.get("enabled", True):
                print("  - " + name + ": skipped (disabled)")
                continue
            try:
                resp = client.get(spec["page"])
                resp.raise_for_status()
                items = _extract(resp.text, spec["page"], spec)
            except Exception as exc:  # one bad outlet must not fail the run
                print("  ! " + name + ": " + type(exc).__name__ + ": " + str(exc))
                continue

            if not items:
                print("  ! " + name + ": page loaded but no articles matched the pattern")
                continue

            target = out_dir / (spec["slug"] + ".xml")
            target.write_text(_render(name, spec["page"], items), encoding="utf-8")
            print("  + " + name + ": " + str(len(items)) + " articles -> " + target.name)
            written += 1

    return written


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    print("Building in-house feeds for outlets with no RSS...")
    count = build_all(root / "data" / "sitefeeds.json", root / "docs" / "feeds")
    print("Done: " + str(count) + " feed(s) written.")


if __name__ == "__main__":
    main()
