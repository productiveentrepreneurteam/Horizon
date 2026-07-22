"""Daily summary generation — pure programmatic rendering."""

import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List, Dict

from ..models import ContentItem


# Outlet display order, by 2026 publication frequency (highest first).
# Outlets not in this list are sorted after all ranked outlets (see
# _outlet_sort_key). Matching is case-insensitive and ignores surrounding
# whitespace, but otherwise must match the outlet's display name exactly
# (the same name used as `feed_name` / config.json's RSS source `name`).
OUTLET_RANKING = [
    "Homes & Gardens",
    "The Spruce",
    "Better Homes & Gardens",
    "Good Housekeeping",
    "Real Simple",
    "House Beautiful",
    "Livingetc",
    "Martha Stewart",
    "Southern Living",
    "Sunset",
    "Mansion Global",
    "Veranda",
    "Apartment Therapy",
    "Architectural Digest",
    "C+B Print",
    "Forbes",
    "Parade Home & Garden",
    "Elle Decor USA",
    "Wall Street Journal",
    "Country Living",
    "Business of Home",
    "Dengarden",
    "Modern Luxury",
    "5280",
    "AOL.com",
    "American Farmhouse",
    "Boston Home Magazine",
    "Colorado Homes",
    "Cubby",
    "Daily Mail",
    "Florida Design",
    "HGTV",
    "House & Home",
    "Kitchen Bath Design",
    "Lakeshore Living",
    "Luxe Magazine",
    "Morris&Essex",
    "Mountain Living",
    "Northshore Magazine",
    "RUE",
    "Ranch & Coast",
    "Realtor.com",
    "Sonoma Magazine",
    "Sophisticated Living Magazine",
    "Style at Home",
    "The Atlanta Magazine",
    "The Kitchn",
    "The Philadelphia Inquirer",
    "USA Today",
    "Vogue",
]
_OUTLET_RANK_INDEX = {name.strip().lower(): i for i, name in enumerate(OUTLET_RANKING)}

# Writer display priority, by 2026 publication frequency (highest first).
# Within each outlet section, articles by writers earlier in this list are
# shown first. Writers not in this list (or articles with no/fallback
# author) sort after all ranked writers, then by publish time.
WRITER_RANKING = [
    "Kelsey Mulvey", "Sarah Lyon", "Emily Moorman", "Tessa Cooper", "Shelby Deering",
    "Cori Sears", "Monique Valeris", "Maya Glantz", "Patricia Shannon", "Eleanor Richardson",
    "Hannah Baker", "Julia Demer", "Heather Bien", "Melissa Epifano Varley", "Angelika Pokovba",
    "Pippa Blenkinsop", "Kelly McMaster", "Michelle Mastro", "Jenny Tzeses", "Maria Sabella",
    "Martha Davies", "Nina Derwin", "Sophie Edwards", "Julia Cancilla", "Nishaa Sharma",
    "Alyssa Longobucco", "Amanda Lauren", "Ameena Walker", "Devin Toolen", "Lauren Jones",
    "Marina Liao", "Olivia Wolfe", "Alyssa Gautieri", "Janae McKenzie", "Megan McCarty",
    "Sarah Yang", "Tracy Kaler", "Elizabeth Stamp", "Jessica Cherner", "Kamron Sanders",
    "Kelli Lamb", "Kristina McGuirk", "Lacey Ramburger", "Lauren Thomann", "Monica Petrucci",
    "Perri O. Blumberg", "Quincy Bulin", "Sal Vaglica", "Timothy Dale", "Yelena Alpert",
    "Alexandra Kelly", "Aliyah Rodriguez", "Amiya Baratan", "Anna Baluch", "Anna Logan",
    "Ashlyn Needham", "Camryn Rabideau", "Charlotte Olby", "Daniel Foster", "Danielle Blundell",
    "Ellie Conley", "Ericka Saurit", "Jessica Flint", "Kathy Barnes", "Katie Mortram",
    "Katrina Harper-Lewis", "Lauren Taylor", "Leeron Hoory", "Lilith Hudson", "Linda Clayton",
    "Luis Rigal", "Madeline Bilis", "Maya Chawla", "Megan Shouse", "Molly Malsom",
    "Morgan McMurrin", "Ottilie Blackhall", "R. Daniel Foster", "Rebecca Shinners", "Sarah Sekula",
    "Sarah Wilson", "Sophia Stanford", "Stacy Sare Cohen", "Tenielle Jordison",
    "Vaishnavi Nayel Talawadekar", "Wendy Rose Gould",
]
_WRITER_RANK_INDEX = {name.strip().lower(): i for i, name in enumerate(WRITER_RANKING)}
# --- 🏆 Press House Wins: confirmed wins pulled from the press tracker ----------
# A row in the tracker's "2026 Active Stories" tab is a WIN when "Published" is
# TRUE and "Published Url" is a real link. "Sources" = the designer, "Outlet" =
# the publication. We match a digest article to a win by its link (exact, no
# scraping) AND only keep wins from Alyssa's tracked outlets (her ranked list of
# outlets with 3+ all-time appearances). Fetched once per run; if the sheet
# can't be reached the digest still publishes normally.
import csv as _csv
import io as _io
import json as _json
import os as _os
import urllib.parse as _urlparse
import urllib.request as _urlreq

# Private feed: a token-gated Apps Script web app bound to the PRC Master Press
# Tracker. Returns {"students": [names...], "wins": [{story,outlet,writer,
# sources,url,date}...]} read LIVE from the sheet. URL + token come from repo
# secrets, so the sheet is never published publicly.
PRESS_FEED_URL = _os.getenv("PRESS_FEED_URL", "")
PRESS_FEED_TOKEN = _os.getenv("PRESS_FEED_TOKEN", "")
_FEED_CACHE = None


def get_feed() -> dict:
    """Fetch the private press feed once (students + wins). Cached per run."""
    global _FEED_CACHE
    if _FEED_CACHE is not None:
        return _FEED_CACHE
    data = {"students": [], "wins": []}
    if PRESS_FEED_URL and PRESS_FEED_TOKEN:
        try:
            sep = "&" if "?" in PRESS_FEED_URL else "?"
            url = f"{PRESS_FEED_URL}{sep}token={_urlparse.quote(PRESS_FEED_TOKEN)}"
            req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=30) as resp:
                payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(payload, dict) and isinstance(payload.get("students"), list):
                data = {"students": payload.get("students", []),
                        "wins": payload.get("wins", [])}
        except Exception:
            data = {"students": [], "wins": []}
    _FEED_CACHE = data
    return data

# Alyssa's tracked outlets — "Top Outlets by Overall Frequency", 3+ appearances.
# Only wins from these outlets show in Press House Wins. Edit this list to
# add/remove outlets (keep the exact outlet name as it appears in the tracker).
TRACKED_OUTLETS = {
    "The Spruce", "Good Housekeeping", "Homes & Gardens", "Apartment Therapy",
    "Livingetc", "Better Homes & Gardens", "Architectural Digest", "Mansion Global",
    "Real Simple", "House Beautiful", "Southern Living", "Martha Stewart",
    "Real Homes", "MyDomaine", "Sunset", "Parade Home & Garden",
    "Wall Street Journal", "Veranda", "Forbes", "Elle Decor USA", "Realtor.com",
    "Luxury Portfolio", "Magnolia", "Domino", "C+B Print", "Hunker", "RUE",
    "Luxury Portfolio Online", "Country Living", "Mountain Living",
    "Pepper Home Blog", "The New York Times", "The Washington Post", "HGTV",
    "Modern Luxury", "USA Today", "Atomic Ranch", "Clean Outlet", "Saavta Blog",
    "The Kitchn", "C+B Digital", "Cottage Home Magazine", "Cottages & Bungalows",
    "Cubby", "Elle Decor Spain", "Ranch & Coast", "Aspire", "Business of Home",
    "Dwell", "Mi Casa",
}

# OPTIONAL: a published-to-web CSV with ONE column of outlet names (one per row).
# If you set this URL, it OVERRIDES the list above and refreshes live every run.
# Leave it as "" to just use the TRACKED_OUTLETS list above.
PRESS_HOUSE_OUTLETS_CSV = ""

_WINS_CACHE = None


def _normalize_url(url: str) -> str:
    """Lowercase; drop scheme, www, query, fragment, trailing slash for matching."""
    if not url:
        return ""
    u = str(url).strip().lower().split("?")[0].split("#")[0]
    for scheme in ("https://", "http://"):
        if u.startswith(scheme):
            u = u[len(scheme):]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def _normalize_outlet(name: str) -> str:
    """Lowercase, collapse whitespace, strip stray punctuation for matching."""
    s = " ".join(str(name or "").strip().lower().split())
    return s.strip(" .,&-")


def _tracked_outlets() -> set:
    """Normalized set of tracked outlets (live CSV if set, else TRACKED_OUTLETS)."""
    if PRESS_HOUSE_OUTLETS_CSV:
        try:
            req = _urlreq.Request(PRESS_HOUSE_OUTLETS_CSV, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            live = {_normalize_outlet(r[0]) for r in _csv.reader(_io.StringIO(text)) if r and r[0].strip()}
            live.discard("")
            live.discard(_normalize_outlet("outlet"))  # drop a header cell if present
            if live:
                return live
        except Exception:
            pass
    return {_normalize_outlet(o) for o in TRACKED_OUTLETS}


def get_press_house_wins() -> dict:
    """Return {normalized_url: designer} for confirmed wins from tracked outlets."""
    global _WINS_CACHE
    if _WINS_CACHE is not None:
        return _WINS_CACHE
    wins: dict = {}
    for w in get_feed().get("wins", []):
        link = str(w.get("url") or "").strip()
        if not link.lower().startswith("http"):
            continue
        key = _normalize_url(link)
        if key:
            wins[key] = str(w.get("sources") or "").strip()
    _WINS_CACHE = wins
    return wins
    # --- 🏆 Recent Press House Wins: latest logged wins, shown every day ----------
import datetime as _dt

_RECENT_WINS_CACHE = None


def _parse_win_date(s):
    """Best-effort parse of the tracker's Date Published (e.g. 2/1/26)."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def get_recent_press_house_wins(limit: int = 12) -> list:
    """Most recent confirmed wins (tracked outlets, real link), newest first."""
    global _RECENT_WINS_CACHE
    if _RECENT_WINS_CACHE is not None:
        return _RECENT_WINS_CACHE
    out = []
    for w in get_feed().get("wins", [])[:limit]:
        link = str(w.get("url") or "").strip()
        if not link.lower().startswith("http"):
            continue
        writer = str(w.get("writer") or "").strip()
        if "choose writer" in writer.lower():
            writer = ""
        out.append({
            "story": str(w.get("story") or "").strip() or str(w.get("outlet") or "").strip(),
            "url": link,
            "outlet": str(w.get("outlet") or "").strip(),
            "designer": str(w.get("sources") or "").strip(),
            "writer": writer,
            "date": str(w.get("date") or "").strip(),
        })
    _RECENT_WINS_CACHE = out
    return out
    # --- ⭐ Auto-detect Press Club designers mentioned inside an article ----------
_CLEAN_SOURCES_CACHE = None
_ARTICLE_SOURCE_CACHE = {}


def get_clean_sources() -> list:
    """Client designer full names, from the private feed's students list."""
    global _CLEAN_SOURCES_CACHE
    if _CLEAN_SOURCES_CACHE is not None:
        return _CLEAN_SOURCES_CACHE
    names = []
    for n in get_feed().get("students", []):
        n = str(n).strip()
        if n and " " in n and "choose" not in n.lower() and "✏" not in n and "🖋" not in n:
            names.append(n)
    names = sorted(set(names), key=len, reverse=True)
    _CLEAN_SOURCES_CACHE = names
    return names


def find_press_club_sources(url: str) -> list:
    """Return any client designer full names found in the article's page text."""
    url = str(url or "")
    if not url.lower().startswith("http"):
        return []
    if url in _ARTICLE_SOURCE_CACHE:
        return _ARTICLE_SOURCE_CACHE[url]
    found = []
    sources = get_clean_sources()
    if sources:
        try:
            req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("utf-8", errors="replace").lower()
            seen = set()
            for name in sources:
                low = name.lower()
                if low in body and low not in seen:
                    seen.add(low)
                    found.append(name)
        except Exception:
            found = []
    _ARTICLE_SOURCE_CACHE[url] = found
    return found


_CJK = r"[\u4e00-\u9fff\u3400-\u4dbf]"
_ASCII = r"[A-Za-z0-9]"


def _pangu(text: str) -> str:
    """Insert a space between CJK and ASCII letters/digits (Pangu spacing)."""
    text = re.sub(rf"({_CJK})({_ASCII})", r"\1 \2", text)
    text = re.sub(rf"({_ASCII})({_CJK})", r"\1 \2", text)
    return text


LABELS = {
    "en": {
        "header": "Press House Daily Digest",
        "source": "Source",
        "background": "Background",
        "discussion": "Discussion",
        "references": "References",
        "tags": "Tags",
        "selected_items": "From {total} items, {selected} important content pieces were selected",
        "full_digest": "{total} articles published in the last 24 hours",
        "sources_monitored": "{count} sources monitored",
        "empty_analyzed": "Analyzed {total} items, but none met the importance threshold.",
        "empty_body": (
            "No significant developments today. This might indicate:\n"
            "- A quiet day in your tracked sources\n"
            "- The AI score threshold is too high\n"
            "- Your information sources need expansion\n\n"
            "Consider:\n"
            "1. Lowering the `ai_score_threshold` in config.json\n"
            "2. Adding more diverse information sources\n"
            "3. Checking if the AI model is working correctly\n"
        ),
    },
    "zh": {
        "header": "Horizon 每日速递",
        "source": "来源",
        "background": "背景",
        "discussion": "社区讨论",
        "references": "参考链接",
        "tags": "标签",
        "selected_items": "从 {total} 条内容中筛选出 {selected} 条重要资讯。",
        "full_digest": "过去 24 小时共发布 {total} 篇文章",
        "sources_monitored": "监控 {count} 个信息源",
        "empty_analyzed": "已分析 {total} 条内容，但没有达到重要性阈值的条目。",
        "empty_body": (
            "今日暂无重要动态，可能原因：\n"
            "- 今天关注的信息源较平静\n"
            "- AI 评分阈值设置过高\n"
            "- 信息源种类有待扩充\n\n"
            "建议：\n"
            "1. 在 config.json 中降低 `ai_score_threshold`\n"
            "2. 添加更多多样化的信息源\n"
            "3. 检查 AI 模型是否正常工作\n"
        ),
    },
}


class DailySummarizer:
    """Generates daily Markdown summaries from pre-analyzed content items."""

    def __init__(self):
        pass

    async def generate_summary(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate daily summary in Markdown format.

        Items are rendered in score-descending order (already sorted by orchestrator).

        Args:
            items: High-scoring content items (already enriched)
            date: Date string (YYYY-MM-DD)
            total_fetched: Total number of items fetched before filtering
            language: Output language, either "en" or "zh"

        Returns:
            str: Markdown formatted summary
        """
        labels = LABELS.get(language, LABELS["en"])

        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        header = (
            f"# {labels['header']} - {date}\n\n"
            f"> {labels['selected_items'].format(total=total_fetched, selected=len(items))}\n\n"
            "---\n\n"
        )

        # TOC
        toc_entries = []
        for i, item in enumerate(items):
            _t = item.metadata.get(f"title_{language}") or item.title
            t = str(_t).replace("[", "(").replace("]", ")")
            if language == "zh":
                t = _pangu(t)
            score = item.ai_score or "?"
            toc_entries.append(f"{i + 1}. [{t}](#item-{i + 1}) \u2b50\ufe0f {score}/10")
        toc = "\n".join(toc_entries) + "\n\n---\n\n"

        parts = [self._format_item(item, labels, language, i + 1) for i, item in enumerate(items)]

        return header + toc + "".join(parts)

    async def generate_full_digest(
        self,
        items: List[ContentItem],
        date: str,
        sources_monitored: int = 0,
        language: str = "en",
    ) -> str:
        """Generate a daily digest listing EVERY fetched item, grouped by outlet.

        No AI scoring/summary required — this renders straight from fetched
        metadata (title, url, source, published time) so it can run on the
        full item set with zero extra AI calls.

        Args:
            items: All fetched items for the day (post URL-dedup, pre-AI-analysis).
            date: Date string (YYYY-MM-DD)
            sources_monitored: Number of sources configured/monitored, shown
                in the header (e.g. count of enabled RSS feeds).
            language: Output language, either "en" or "zh"

        Returns:
            str: Markdown formatted full digest, grouped by outlet.
        """
        labels = LABELS.get(language, LABELS["en"])

        # Drop articles tagged "kitchen appliances" or "cleaning"
        BLOCKED_TAGS = ["kitchen appliances", "cleaning"]
        def _blocked(item):
            tags = [str(t).lower() for t in (item.metadata.get("tags") or [])]
            return any(blk in tag for blk in BLOCKED_TAGS for tag in tags)
        items = [it for it in items if not _blocked(it)]

        if not items:
            return self._generate_empty_summary(date, 0, labels)

        # --- Header -----------------------------------------------------
        header_lines = [
            f"# {labels['header']} - {date}",
            "",
            f"> {labels['full_digest'].format(total=len(items))}",
        ]
        if sources_monitored:
            header_lines.append(f"> {labels['sources_monitored'].format(count=sources_monitored)}")
        header_lines += ["", "---", ""]
        header = "\n".join(header_lines)

        # --- Group items by outlet, preserving first-seen order --------
        groups: "OrderedDict[str, List[ContentItem]]" = OrderedDict()
        for item in items:
            outlet = self._outlet_name(item)
            groups.setdefault(outlet, []).append(item)

        # Sort each outlet's items: ranked writers first (by their rank),
        # then unranked/fallback authors, each group newest-first by time.
        for outlet_items in groups.values():
            outlet_items.sort(key=self._article_sort_key)

        # Sort outlets by configured ranking; unranked outlets go last,
        # alphabetically among themselves.
        sorted_outlets = sorted(groups.items(), key=lambda kv: self._outlet_sort_key(kv[0]))

        # --- Overview: outlet name + count, anchored for jump links ----
        overview_lines = ["## Today's Publications", ""]
        for outlet, outlet_items in sorted_outlets:
            anchor = self._slugify(outlet)
            overview_lines.append(f"- [{outlet} ({len(outlet_items)})](#source-{anchor})")
        overview_lines += ["", f"**Total Articles Today: {len(items)}**", "", "---", ""]
        overview = "\n".join(overview_lines)

        # --- Per-outlet sections: simple bullet list per article --------
        section_parts = []
        for outlet, outlet_items in sorted_outlets:
            anchor = self._slugify(outlet)
            section_parts.append(f'<a id="source-{anchor}"></a>\n')
            section_parts.append(f"## {outlet} ({len(outlet_items)})\n\n")
            for item in outlet_items:
                section_parts.append(self._format_item_simple(item, language))
                section_parts.append("\n")
            section_parts.append("\n")

       # --- 🏆 Press House Wins: today's auto-detected client articles + recent logged wins ---
        wins_parts = []
        todays = [it for it in items if find_press_club_sources(it.url)]
        today_urls = {_normalize_url(it.url) for it in todays}
        recent_wins = [w for w in get_recent_press_house_wins()
                       if _normalize_url(w["url"]) not in today_urls]
        if todays or recent_wins:
            wins_parts.append("## 🏆 Press House Wins\n\n")
            for item in todays:
                wins_parts.append(self._format_item_simple(item, language))
                wins_parts.append("\n")
            for w in recent_wins:
                designers = [d.strip() for d in (w["designer"] or "").replace(";", ",").split(",") if d.strip()]
                source_tags = " ".join(f"`⭐ {d} ⭐`" for d in designers) or "`⭐ Press Club Source ⭐`"
                meta = f"by {w['writer']} · {w['date']}" if w["writer"] else w["date"]
                wins_parts.append(
                    f"- [{w['story']}]({w['url']}) {source_tags} `{w['outlet']}` *{meta}*\n"
                )
            wins_parts.append("\n---\n\n")

        return header + "".join(wins_parts) + overview + "".join(section_parts)

    def _format_item_simple(self, item: ContentItem, language: str) -> str:
        """Render a single item as a small card: title (new-tab link), tags, author, time."""
        _title = item.metadata.get(f"title_{language}") or item.title
        title = str(_title).replace("[", "(").replace("]", ")")
        if language == "zh":
            title = _pangu(title)
        url = str(item.url)

        time_str = ""
        if item.published_at:
            if language == "zh":
                time_str = f"{item.published_at.month}月{item.published_at.day}日 {item.published_at:%H:%M}"
            else:
                day = item.published_at.strftime("%d").lstrip("0")
                time_str = item.published_at.strftime(f"%b {day}, %H:%M")

        # Title links open in a new tab (raw <a> since Markdown has no
        # target="_blank" syntax; Kramdown/Jekyll renders embedded HTML fine).
        lines = [f'- <a href="{url}" target="_blank" rel="noopener">{title}</a>']

        # Tags: from the RSS entry's own <category> tags (metadata["tags"]),
        # free, no AI call. Skipped if the feed didn't provide any.
        tags = item.metadata.get("tags") or []
        clean_tags = [str(t).strip() for t in tags if str(t).strip()]
        if clean_tags:
            tags_str = " ".join(f"`{t}`" for t in clean_tags[:6])
            lines.append(f"  {tags_str}")
        # ⭐ Press Club Source: logged win (by link), else a client designer found in the text
        _designer = get_press_house_wins().get(_normalize_url(item.url))
        if _designer:
            lines.append(f"  `⭐ Press Club Source: {_designer} ⭐`")
        elif _designer == "":
            lines.append("  `⭐ Press Club Source ⭐`")
        else:
            _found = find_press_club_sources(item.url)
            if _found:
                # Detected in a fresh scraped article but NOT in the tracker sheet -> a new discovery.
                lines.append(f"  `🆕 New find: {', '.join(_found)} · not in tracker yet`")

        # Author + time on one meta line. item.author falls back to the
        # outlet name when the feed doesn't provide a byline (rss.py), so
        # we only show it as "by X" when it looks like an actual byline
        # (i.e. different from the outlet name) to avoid "by Architectural
        # Digest" noise.
        outlet = self._outlet_name(item)
        author = (item.author or "").strip()
        meta_bits = []
        if author and author != outlet:
            meta_bits.append(author if language == "zh" else f"by {author}")
        if time_str:
            meta_bits.append(time_str)
        if meta_bits:
            lines.append(f"  *{' · '.join(meta_bits)}*")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _outlet_sort_key(outlet: str):
        """Sort key for outlets: ranked outlets first (by rank), then
        unranked outlets alphabetically."""
        idx = _OUTLET_RANK_INDEX.get(outlet.strip().lower())
        if idx is not None:
            return (0, idx, outlet.lower())
        return (1, 0, outlet.lower())

    @staticmethod
    def _article_sort_key(item: ContentItem):
        """Sort key for articles within an outlet section: ranked writers
        first (by rank), then unranked/fallback authors — each group
        newest-first by publish time."""
        author = (item.author or "").strip().lower()
        writer_idx = _WRITER_RANK_INDEX.get(author)
        pub = item.published_at or datetime.min.replace(tzinfo=timezone.utc)
        # Negative timestamp so "newest first" falls out of an ascending sort.
        neg_pub = -pub.timestamp()
        if writer_idx is not None:
            return (0, writer_idx, neg_pub)
        return (1, 0, neg_pub)

    @staticmethod
    def _outlet_name(item: ContentItem) -> str:
        """Best-effort outlet/source name for grouping (mirrors _format_item's source line)."""
        meta = item.metadata
        if meta.get("feed_name"):
            return str(meta["feed_name"])
        if meta.get("subreddit"):
            return f"r/{meta['subreddit']}"
        if item.author:
            return str(item.author)
        return item.source_type.value

    @staticmethod
    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
        return slug.strip("-") or "source"

    def generate_webhook_overview(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate a compact overview for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        if not items:
            return self._generate_empty_summary(date, total_fetched, labels)

        if language == "zh":
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> 从 {total_fetched} 条内容中筛选出 {len(items)} 条重要资讯。\n\n"
                "下面会按新闻逐条发送详情，你可以只看感兴趣的标题。\n\n"
            )
        else:
            header = (
                f"# {labels['header']} - {date}\n\n"
                f"> Selected {len(items)} important items from {total_fetched} fetched items.\n\n"
                "Details will be sent item by item so you can read only the topics you care about.\n\n"
            )

        entries = []
        for i, item in enumerate(items, start=1):
            title = str(item.metadata.get(f"title_{language}") or item.title).replace("[", "(").replace("]", ")")
            if language == "zh":
                title = _pangu(title)
            score = item.ai_score or "?"
            entries.append(f"{i}. [{title}]({item.url}) \u2b50\ufe0f {score}/10")

        return header + "\n".join(entries)

    def generate_webhook_item(
        self,
        item: ContentItem,
        language: str,
        index: int,
        total: int,
    ) -> str:
        """Generate one item message for multi-message webhook delivery."""
        labels = LABELS.get(language, LABELS["en"])
        prefix = f"第 {index}/{total} 条\n\n" if language == "zh" else f"Item {index}/{total}\n\n"
        return prefix + self._format_item(item, labels, language, index).rstrip("-\n ")

    def _format_item(self, item: ContentItem, labels: dict, language: str, index: int) -> str:
        """Format a single ContentItem into Markdown."""
        _title = item.metadata.get(f"title_{language}") or item.title
        title = str(_title).replace("[", "(").replace("]", ")")
        url = str(item.url)
        score = item.ai_score or "?"
        meta = item.metadata

        summary = (
            meta.get(f"detailed_summary_{language}")
            or meta.get("detailed_summary")
            or item.ai_summary
            or ""
        )
        background = meta.get(f"background_{language}") or meta.get("background") or ""
        discussion = (
            meta.get(f"community_discussion_{language}")
            or meta.get("community_discussion")
            or ""
        )

        if language == "zh":
            title = _pangu(title)
            summary = _pangu(summary)
            background = _pangu(background)
            discussion = _pangu(discussion)

        # Source line with parts joined by " · ", link appended at end
        source_type = item.source_type.value
        source_parts = [source_type]
        if meta.get("subreddit"):
            source_parts.append(f"r/{meta['subreddit']}")
        if meta.get("feed_name"):
            source_parts.append(meta["feed_name"])
        else:
            source_parts.append(item.author or "unknown")
        if item.published_at:
            if language == "zh":
                source_parts.append(
                    f"{item.published_at.month}月{item.published_at.day}日 "
                    f"{item.published_at:%H:%M}"
                )
            else:
                day = item.published_at.strftime("%d").lstrip("0")
                source_parts.append(item.published_at.strftime(f"%b {day}, %H:%M"))
        source_line = " \u00b7 ".join(source_parts)  # ·

        discussion_url = meta.get("discussion_url")
        if discussion_url:
            discussion_url = str(discussion_url)
            if discussion_url != url:
                source_line += f' · [{labels["discussion"]}]({discussion_url})'

        lines = [
            f'<a id="item-{index}"></a>',
            f"## [{title}]({url}) \u2b50\ufe0f {score}/10",  # ⭐️
            "",
            summary,
            "",
            source_line,
        ]

        if background:
            lines.append("")
            lines.append(f"**{labels['background']}**: {background}")

        sources = meta.get("sources") or []
        if sources:
            items_html = "".join(f'<li><a href="{s["url"]}">{s["title"]}</a></li>\n' for s in sources)
            lines += [
                "",
                f'<details><summary>{labels["references"]}</summary>\n<ul>\n{items_html}\n</ul>\n</details>',
            ]

        if discussion:
            lines.append("")
            lines.append(f"**{labels['discussion']}**: {discussion}")

        if item.ai_tags:
            tags_str = ", ".join([f"`#{t}`" for t in item.ai_tags])
            lines.append("")
            lines.append(f"**{labels['tags']}**: {tags_str}")

        lines.append("")
        lines.append("---")

        return "\n".join(lines) + "\n\n"

    def _generate_empty_summary(self, date: str, total_fetched: int, labels: dict) -> str:
        """Generate summary when no high-scoring items were found."""
        return (
            f"# {labels['header']} - {date}\n\n"
            f"> {labels['empty_analyzed'].format(total=total_fetched)}\n\n"
            + labels["empty_body"]
        )
