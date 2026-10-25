"""RSS feed scraper implementation."""

import calendar
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from email.utils import parsedate_to_datetime
import httpx
import feedparser

from . import topics
from .base import BaseScraper
from ..models import ContentItem, SourceType, RSSSourceConfig

logger = logging.getLogger(__name__)


# A FEED THAT REFUSES THE BARE HTTPX USER-AGENT GETS ONE RETRY AS A BROWSER.
# Measured 2026-10-20 from a GitHub Actions runner: The Atlanta Magazine,
# Florida Design and Northshore Magazine answer 403 to the default
# python-httpx request and 200 with ten current items to this header set,
# which is the one src/scrapers/sitefeed.py and the article matcher (PR 8)
# already send. The retry only fires on 401/403, so every feed that reads
# today is asked exactly as before. Set per request, not on the shared
# client, because that client also serves JSON APIs (GitHub, HN, Reddit)
# that must not receive an HTML Accept header or a browser user-agent.
FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


# Topical relevance filtering lives in src/scrapers/topics.py, driven by
# data/topics.json. It replaces the old TITLE_EXCLUDE_KEYWORDS dict that
# used naive substring matching (which also caught "starting" for "star"
# and "workshop" for "shop"). House Beautiful's original exclusions were
# migrated into that file unchanged in intent.


class RSSScraper(BaseScraper):
    """Scraper for RSS/Atom feeds."""

    def __init__(self, sources: List[RSSSourceConfig], http_client: httpx.AsyncClient):
        """Initialize RSS scraper.

        Args:
            sources: List of RSS feed configurations
            http_client: Shared async HTTP client
        """
        super().__init__({"sources": sources}, http_client)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Fetch RSS feed items.

        Args:
            since: Only fetch items published after this time

        Returns:
            List[ContentItem]: Fetched content items
        """
        items = []
        sources = self.config["sources"]

        for source in sources:
            if not source.enabled:
                continue

            feed_items = await self._fetch_feed(source, since)
            items.extend(feed_items)

        return items

    async def _fetch_feed(
        self, source: RSSSourceConfig, since: datetime
    ) -> List[ContentItem]:
        """Fetch items from a single RSS feed.

        Args:
            source: RSS feed configuration
            since: Only fetch items after this time

        Returns:
            List[ContentItem]: Feed content items
        """
        items = []

        try:
            # Expand environment variables in URL (e.g. ${LWN_TOKEN})
            feed_url = re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), m.group(0)).strip(),
                str(source.url),
            )

            # Feeds we build ourselves (src/scrapers/sitefeed.py, for outlets
            # that publish no RSS) are written to disk earlier in the same run
            # and referenced as file://<path-relative-to-repo-root>. Reading
            # them from disk rather than over HTTP means today's run uses
            # today's scrape - going via the published Pages URL would always
            # be one run behind, since the deploy happens after this step.
            if feed_url.startswith("file://"):
                local_path = Path(feed_url[len("file://"):])
                if not local_path.is_absolute():
                    local_path = Path(__file__).resolve().parents[2] / local_path
                raw = local_path.read_text(encoding="utf-8")
            else:
                response = await self.client.get(feed_url, follow_redirects=True)
                if response.status_code in (401, 403):
                    logger.info(
                        "RSS feed %s refused the default request (HTTP %s); "
                        "retrying with browser headers",
                        source.name, response.status_code,
                    )
                    response = await self.client.get(
                        feed_url, headers=FEED_HEADERS, follow_redirects=True
                    )
                    if response.status_code == 200:
                        logger.info("RSS feed %s read on the browser-header retry", source.name)
                response.raise_for_status()
                raw = response.text

            # Parse feed
            feed = feedparser.parse(raw)

            # Work out which category tags are this feed's own branding before
            # filtering anything. Dotdash stamps every item with the channel
            # name ("Real Simple: Home Decor Ideas, Recipes, DIY & Beauty
            # Tips"), which contains "Recipes" and "Beauty" and would
            # otherwise wipe out the entire feed.
            _all_cats = [
                [str(t.get("term") or "").strip() for t in (e.get("tags") or [])]
                for e in feed.entries
            ]
            _branding = topics.branding_tags(_all_cats)

            for entry in feed.entries:
                # Parse published date
                published_at = self._parse_date(entry)
                if not published_at or published_at < since:
                    continue

                title = entry.get("title", "Untitled")

                # Google News appends the publication name to every headline
                # ("...Asking $59.5M - Mansion Global"). We already show the
                # outlet as the section heading, so strip the duplicate.
                _suffix = " - " + source.name
                if title.endswith(_suffix):
                    title = title[: -len(_suffix)].rstrip()

                # Drop articles that are off-topic for an interior-design
                # press digest: recipes, fashion, beauty, deal roundups.
                _cats = [str(t.get("term") or "").strip() for t in (entry.get("tags") or [])]
                _keep, _why = topics.check(
                    source.name, title, _cats, entry.get("link", ""), _branding
                )
                if not _keep:
                    logger.debug("Filtered %s: %s (%s)", source.name, title, _why)
                    continue

                # Generate unique ID from feed URL and entry ID
                feed_id = str(source.url).split("//")[1].replace("/", "_")
                entry_id = entry.get("id", entry.get("link", ""))
                entry_hash = hashlib.sha256(str(entry_id).encode("utf-8")).hexdigest()[
                    :16
                ]

                # Extract content
                content = self._extract_content(entry)

                item = ContentItem(
                    id=self._generate_id("rss", feed_id, entry_hash),
                    source_type=SourceType.RSS,
                    title=title,
                    url=entry.get("link", str(source.url)),
                    content=content,
                    author=self._extract_author(entry, source.name),
                    published_at=published_at,
                    metadata={
                        "feed_name": source.name,
                        "category": source.category,
                        # Branding tags ("Real Simple: Home Decor Ideas,
                        # Recipes, DIY & Beauty Tips") are the channel name,
                        # not a topic. They are already ignored for filtering;
                        # hide them from the tag chips too.
                        "tags": [
                            t for t in self._extract_tags(entry, source.category)
                            if t not in _branding
                        ],
                    },
                )
                items.append(item)

        except httpx.HTTPError as e:
            logger.warning("Error fetching RSS feed %s: %s", source.name, e)
        except Exception as e:
            logger.warning("Error parsing RSS feed %s: %s", source.name, e)

        return items

    def _parse_date(self, entry: dict) -> datetime:
        """Parse publication date from feed entry.

        Args:
            entry: Feed entry data

        Returns:
            datetime: Parsed publication date or None
        """
        # Try different date fields
        for field in ["published", "updated", "created"]:
            if field in entry:
                try:
                    # Try parsing structured time first
                    if f"{field}_parsed" in entry and entry[f"{field}_parsed"]:
                        return datetime.fromtimestamp(
                            calendar.timegm(entry[f"{field}_parsed"]), tz=timezone.utc
                        )
                    # Fallback to string parsing
                    date_str = entry[field]
                    return parsedate_to_datetime(date_str)
                except Exception:
                    continue

        return None

    @staticmethod
    def _extract_author(entry: dict, outlet_name: str) -> str:
        """Extract a clean human author name from a feed entry.

        feedparser exposes the author in several places depending on the feed
        format. Some feeds (e.g. The Spruce via Dotdash) put a Facebook/social
        URL in <author> instead of a name. We try multiple fields in priority
        order and fall back to the outlet name only when nothing human-readable
        is found.
        """
        candidates = []

        # 1. author_detail.name  — most reliable when present
        author_detail = entry.get("author_detail") or {}
        if author_detail.get("name"):
            candidates.append(author_detail["name"])

        # 2. dc:creator  — Dublin Core, used by WordPress and many magazine CMS
        if entry.get("dc_creator"):
            candidates.append(entry["dc_creator"])
        if entry.get("tags"):
            # Some feeds encode author as a tag with scheme="author"
            for tag in entry.get("tags", []):
                if getattr(tag, "scheme", "") == "author" and getattr(tag, "term", ""):
                    candidates.append(tag.term)

        # 3. Plain author field
        if entry.get("author"):
            candidates.append(entry["author"])

        for candidate in candidates:
            name = str(candidate).strip()
            # Discard anything that looks like a URL or email
            if not name:
                continue
            if name.startswith("http") or name.startswith("www."):
                continue
            if "@" in name and "." in name.split("@")[-1]:
                # looks like an email address — skip
                continue
            # Discard if it's just the outlet name repeated (fallback noise)
            if name.lower() == outlet_name.lower():
                continue
            return name

        # Nothing usable found — return outlet name as fallback
        return outlet_name

    @staticmethod
    def _extract_tags(entry: dict, feed_category: str) -> list:
        """Extract tags from a feed entry, falling back to the feed's own
        category when the entry carries no per-article tags.

        feedparser normalises <category> elements into entry.tags as objects
        with .term (the label) and optional .scheme / .label. We collect the
        .term values, skip any that look like URLs or internal scheme markers,
        and cap the list at 6 tags to keep the UI clean.
        """
        raw_tags = []
        for tag in entry.get("tags", []):
            term = getattr(tag, "term", "") or ""
            term = term.strip()
            if not term:
                continue
            # Skip URL-like terms and single-char noise
            if term.startswith("http") or len(term) < 2:
                continue
            # Skip if it looks like a numeric ID
            if term.isdigit():
                continue
            raw_tags.append(term)

        if raw_tags:
            return raw_tags[:6]

        # Fallback: use the feed-level category from config.json as a single
        # tag so there is always something to display (e.g. "design", "home").
        if feed_category:
            return [feed_category.replace("-", " ").title()]

        return []

    def _extract_content(self, entry: dict) -> str:
        """Extract text content from feed entry.

        Args:
            entry: Feed entry data

        Returns:
            str: Extracted text content
        """
        # Keep the LONGEST of summary, description and content. This used to
        # return summary first, so a feed carrying the full article in
        # content:encoded lost it: Dengarden's feed (measured 2026-10-20) gives
        # feedparser a 45-character summary and a 4,848-character body, and the
        # body was thrown away. Dengarden's article pages sit behind DataDome,
        # so the feed body is the only free copy of the text the matcher needs.
        candidates = []
        if entry.get("summary"):
            candidates.append(str(entry.summary))
        if entry.get("description"):
            candidates.append(str(entry.description))
        if "content" in entry and entry.content:
            # content is usually a list
            candidates.append(str(entry.content[0].get("value", "") or ""))
        if not candidates:
            return ""
        return max(candidates, key=len)
