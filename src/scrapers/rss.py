"""RSS feed scraper implementation."""

import calendar
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import List
from email.utils import parsedate_to_datetime
import httpx
import feedparser

from .base import BaseScraper
from ..models import ContentItem, SourceType, RSSSourceConfig

logger = logging.getLogger(__name__)


# Per-outlet title exclusion keywords. If an article's title contains any of
# these (case-insensitive), it is dropped before becoming a ContentItem.
# Keyed by feed `name` exactly as configured in config.json. This stays here
# (rather than as a config.json field) so it works regardless of whether the
# RSSSourceConfig model allows extra/unknown fields.
TITLE_EXCLUDE_KEYWORDS = {
    "House Beautiful": [
        "celebrity",
        "celebrities",
        "star",
        "shop",
        "shopping",
        "famous",
    ],
}


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

        exclude_keywords = [
            kw.lower() for kw in TITLE_EXCLUDE_KEYWORDS.get(source.name, [])
        ]

        try:
            # Expand environment variables in URL (e.g. ${LWN_TOKEN})
            feed_url = re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), m.group(0)).strip(),
                str(source.url),
            )

            # Fetch feed content
            response = await self.client.get(feed_url, follow_redirects=True)
            response.raise_for_status()

            # Parse feed
            feed = feedparser.parse(response.text)

            for entry in feed.entries:
                # Parse published date
                published_at = self._parse_date(entry)
                if not published_at or published_at < since:
                    continue

                title = entry.get("title", "Untitled")

                # Drop articles matching this outlet's title exclusion list
                # (e.g. House Beautiful: skip celebrity/shopping content)
                if exclude_keywords:
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in exclude_keywords):
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
                        "tags": self._extract_tags(entry, source.category),
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
        # Try different content fields
        if "summary" in entry:
            return entry.summary
        if "description" in entry:
            return entry.description
        if "content" in entry and entry.content:
            # content is usually a list
            return entry.content[0].get("value", "")

        return ""
