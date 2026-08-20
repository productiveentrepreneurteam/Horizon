"""Daily summary generation — pure programmatic rendering."""

import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List, Dict

from ..models import ContentItem


# Outlet display order - Alyssa's ranking, 2026-07-27.
#
# Built by combining BOTH frequency columns of the "Ranked Outlets" tab:
# an outlet's rank by 2026 story count and its rank by all-time story
# count, averaged. 2026 alone over-weights a hot year and buries an
# outlet like Apartment Therapy (130 lifetime stories); all-time alone
# buries whatever is working right now.
#
# The first TIER_1_SIZE outlets render under "Priority outlets"; the rest
# fall under "More outlets" so the top of the page is always the ones
# that matter. Outlets not in this list sort last, alphabetically.
# Matching is case-insensitive and ignores surrounding whitespace, but
# otherwise must match the outlet's display name exactly (the same name
# used as `feed_name` / config.json's RSS source `name`).
OUTLET_RANKING = [
    "The Spruce",
    "Homes & Gardens",
    "Good Housekeeping",
    "Better Homes & Gardens",
    "Livingetc",
    "Real Simple",
    "House Beautiful",
    "Apartment Therapy",
    "Mansion Global",
    "Martha Stewart",
    "Southern Living",
    "Architectural Digest",
    "Sunset",
    "Veranda",
    "Parade Home & Garden",
    "Forbes",
    "Wall Street Journal",
    "Elle Decor USA",
    "C+B Print",
    "Country Living",
    "Modern Luxury",
    "Realtor.com",
    "HGTV",
    "Mountain Living",
    "RUE",
    "Business of Home",
    "Cubby",
    "Dengarden",
    "American Farmhouse",
    "Real Homes",
    "MyDomaine",
    "Florida Design",
    "USA Today",
    "5280",
    "Ranch & Coast",
    "The Kitchn",
    "AOL.com",
    "Boston Home Magazine",
    "Hunker",
    "Colorado Homes",
    "Daily Mail",
    "New York Times",
    "House & Home",
    "Kitchen Bath Design",
    "Lakeshore Living",
    "Luxe Magazine",
    "Morris&Essex",
    "Northshore Magazine",
    "Sonoma Magazine",
    "Sophisticated Living Magazine",
    "Style at Home",
    "The Atlanta Magazine",
    "The Philadelphia Inquirer",
    "Vogue",
]

# How many outlets lead the page. The top 15 account for roughly 87% of
# Alyssa's 2026 press; the top 20 for about 92%. Everything below still
# gets scraped and still gets scanned for her designers - the tail is
# where an untracked find shows up - it just renders lower down.
TIER_1_SIZE = 15

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
import sys as _sys
import time as _time
import os as _os
import urllib.parse as _urlparse
import urllib.request as _urlreq
import html as _html
import re as _re

# Private feed: a token-gated Apps Script web app bound to the PRC Master Press
# Tracker. Returns {"students": [names...], "wins": [{story,outlet,writer,
# sources,url,date}...]} read LIVE from the sheet. URL + token come from repo
# secrets, so the sheet is never published publicly.
PRESS_FEED_URL = _os.getenv("PRESS_FEED_URL", "")
PRESS_FEED_TOKEN = _os.getenv("PRESS_FEED_TOKEN", "")
_FEED_CACHE = None


PRESS_FEED_HOST_HINT = "the press tracker feed (Apps Script)"
_FEED_FAILED = []


def feed_failed() -> bool:
    """True when this run could not read the roster/wins feed at all."""
    return bool(_FEED_FAILED)


def get_feed() -> dict:
    """Fetch the private press feed once (students + wins). Cached per run."""
    global _FEED_CACHE
    if _FEED_CACHE is not None:
        return _FEED_CACHE
    data = {"students": [], "wins": [], "ok": False}
    if PRESS_FEED_URL and PRESS_FEED_TOKEN:
        sep = "&" if "?" in PRESS_FEED_URL else "?"
        url = f"{PRESS_FEED_URL}{sep}token={_urlparse.quote(PRESS_FEED_TOKEN)}"
        last = None
        # Retry, because one blip here silently blanks the entire Press House
        # Wins section: no roster means no designer can be detected in any
        # article, and no wins means the section is not rendered at all. That
        # is what happened on 2026-08-12 - a digest that looked normal and
        # quietly reported zero wins on a day that had them.
        for attempt in range(4):
            try:
                req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with _urlreq.urlopen(req, timeout=30) as resp:
                    payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(payload, dict) and isinstance(payload.get("students"), list):
                    data = {"students": payload.get("students", []),
                            "wins": payload.get("wins", []),
                            "stats": payload.get("stats", {}),
                            "ok": True}
                    break
                last = "feed returned an unexpected shape"
            except Exception as e:
                last = e
            if attempt < 3:
                _time.sleep(2 ** attempt * 5)
        if not data.get("ok"):
            # Loud, and non-zero exit, so a blackout shows up as a red run
            # instead of a quiet digest with the wins section missing.
            print(f"::error title=Press feed unreachable::{PRESS_FEED_HOST_HINT} "
                  f"failed 4 times, last error: {last}. Press House Wins will be "
                  f"empty for this run.", file=_sys.stderr)
            _FEED_FAILED.append(str(last))
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


_WIN_HEADLINE_CACHE = {}


def _real_headline(url: str) -> str:
    """The article's own headline, or "" if it cannot be read.

    Wins are logged by hand in the press tracker, and the Story column is often a
    shorthand note rather than the headline: "Dark bedrooms" for an article
    actually titled "25 Chic Dark Bedroom Ideas for a Cozy Escape". The digest is
    a client-facing document, so it should carry the real headline and keep the
    typed note only as a fallback.
    """
    if url in _WIN_HEADLINE_CACHE:
        return _WIN_HEADLINE_CACHE[url]
    title = ""
    why = ""
    try:
        # A bare "Mozilla/5.0" is not enough for several of these outlets; they
        # want a request that looks like a browser or they hand back a challenge
        # page. The first version of this shipped with a plain UA and silently
        # fell back on every single win.
        req = _urlreq.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with _urlreq.urlopen(req, timeout=15) as resp:
            html = resp.read(300_000).decode("utf-8", errors="replace")
        # <title> first: it is what the outlet shows in the browser tab and it
        # matches what a person sees. og:title is often an A/B-tested variant.
        m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.I | _re.S)
        if not m:
            m = _re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                html, _re.I)
        if m:
            title = _html.unescape(_re.sub(r"\s+", " ", m.group(1))).strip()
        else:
            why = "no <title> or og:title in the response"
    except Exception as e:
        why = str(e)
    if not title:
        # Loud on purpose. The fallback below is correct but invisible, and an
        # invisible fallback is how the shorthand titles survived unnoticed.
        print(f"::warning title=Win headline unread::{url} -> {why or 'empty title'}. "
              f"Falling back to the tracker's typed note.", file=_sys.stderr)
    _WIN_HEADLINE_CACHE[url] = title
    return title


def _strip_outlet_suffix(title: str, outlet: str) -> str:
    """Drop a trailing " | Outlet" / " - Outlet" that the site appended.

    Only strips when the tail actually looks like the outlet's name, so a
    headline that legitimately contains a dash keeps all of its words.
    """
    if not title or not outlet:
        return title
    m = _re.search(r"^(.*?)\s*[|\-–—]\s*([^|\-–—]{2,60})$", title)
    if not m:
        return title
    head, tail = m.group(1).strip(), m.group(2).strip().lower()
    o = outlet.strip().lower()
    words = [w for w in _re.split(r"\W+", o) if len(w) > 2]
    looks_like_outlet = tail == o or (words and all(w in tail for w in words))
    return head if (head and looks_like_outlet) else title


def get_recent_press_house_wins(limit: int = 0) -> list:
    """Most recent confirmed wins (tracked outlets, real link), newest first.

    limit=0 (the default) means NO cap: every win the feed provides is
    rendered. The old default of 12 meant the digest could only ever show
    the newest handful of logged wins, however many were in the tracker -
    Alyssa's 2026-08-13 "i thought we had more". NOTE: the sheet-side Apps
    Script doGet feed was built with its own "newest 12" slice (2026-07-22);
    if the live page still shows ~12 after this ships, the remaining cap is
    that slice in the Apps Script project, not this function.
    """
    global _RECENT_WINS_CACHE
    if _RECENT_WINS_CACHE is not None:
        return _RECENT_WINS_CACHE
    out = []
    wins = get_feed().get("wins", [])
    if limit:
        wins = wins[:limit]
    for w in wins:
        link = str(w.get("url") or "").strip()
        if not link.lower().startswith("http"):
            continue
        writer = str(w.get("writer") or "").strip()
        if "choose writer" in writer.lower():
            writer = ""
        logged = str(w.get("story") or "").strip()
        outlet = str(w.get("outlet") or "").strip()
        headline = _strip_outlet_suffix(_real_headline(link), outlet)
        out.append({
            "story": headline or logged or outlet,
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


# A designer name found ONLY outside the article body, in a related-articles
# sidebar, a nav promo, an image alt attribute, a byline or a script payload,
# is discarded. Set to False to go back to "report anything found anywhere",
# which is the behaviour that produced Liz Ferriera's 2026-08-06 false
# positives. Do not set it to False without a reason written down.
STRICT_ARTICLE_BODY_ONLY = True

# Whole elements that never contain the article's own text.
_NON_ARTICLE_TAGS = (
    "script", "style", "noscript", "template", "svg", "iframe", "object",
    "nav", "header", "footer", "aside", "form", "figcaption", "picture",
    "source", "img", "video", "audio", "button", "select", "option",
)

# Containers whose class/id says "this is other articles, not this one".
_NON_ARTICLE_HINTS = (
    "related", "recirc", "recommend", "more-from", "morefrom", "most-read",
    "mostread", "popular", "trending", "promo", "sidebar", "side-bar",
    "newsletter", "subscribe", "signup", "comment", "footer", "header",
    "nav", "menu", "social", "share", "widget", "carousel", "teaser",
    "taboola", "outbrain", "advert", "ad-", "-ad", "sponsor", "breadcrumb",
    "up-next", "upnext", "you-may", "youmay", "read-next", "readnext",
    "latest", "editors-pick", "editorspick", "playlist", "gallery-nav",
    "tags", "topic-list", "cookie", "modal", "popup", "masthead",
)

# Containers that are the byline, not the story.
_BYLINE_HINTS = (
    "byline", "by-line", "by_line", "author", "contributor", "writer",
    "credit", "reporter",
)

# Words that, immediately before a name, mean "this is a credit line".
_BYLINE_CUES = ("by", "words", "story", "text", "reporting", "photography",
                "photographs", "photos", "written", "edited")

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _flatten(text: str) -> str:
    """Lowercase, collapse every non-alphanumeric run to one space, pad ends.

    Padding lets a plain `in` test behave like a word-boundary match, so
    "Sarah Storms" no longer matches inside "Sarah Stormsworth", and a
    possessive, a non-breaking space or a curly apostrophe still match.
    """
    return " " + _NON_WORD.sub(" ", str(text or "").lower()).strip() + " "


def _hinted(tag, hints) -> bool:
    """True when a tag's class/id/testid/role matches one of the hints.

    Returns False for a tag that has already been decomposed: find_all()
    hands back a snapshot, and decomposing a parent leaves its children in
    that list with their attrs stripped to None.
    """
    if getattr(tag, "decomposed", False) or tag.attrs is None:
        return False
    classes = tag.get("class") or []
    if not isinstance(classes, list):
        classes = [classes]
    bag = " ".join(
        [" ".join(str(c) for c in classes), str(tag.get("id") or ""),
         str(tag.get("data-testid") or ""), str(tag.get("role") or "")]
    ).lower()
    return any(h in bag for h in hints)


def _split_article_text(html: str):
    """Return (article_text, byline_text) for one fetched page.

    article_text is the story's own visible words. Every attribute value,
    script payload, nav, sidebar and recirculation module is gone, because
    get_text() reads text nodes only and the rest is decomposed first.
    byline_text is kept separately so a writer can be told apart from a
    designer quoted in the story.

    Falls back to a tag-stripping regex if BeautifulSoup is missing. The
    fallback still drops every attribute value, which is where the Business
    of Home false positive lived, but it cannot drop recirculation modules.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:
        stripped = re.sub(
            r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1\s*>", " ", html
        )
        return re.sub(r"(?s)<[^>]*>", " ", stripped), ""

    soup = BeautifulSoup(html, "html.parser")

    byline_bits = []
    for tag in list(soup.find_all(True)):
        if _hinted(tag, _BYLINE_HINTS):
            byline_bits.append(tag.get_text(" ", strip=True))
    for meta in soup.find_all("meta"):
        if str(meta.get("name") or meta.get("property") or "").lower().endswith("author"):
            byline_bits.append(str(meta.get("content") or ""))

    for tag in list(soup.find_all(_NON_ARTICLE_TAGS)):
        if not getattr(tag, "decomposed", False):
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if getattr(tag, "decomposed", False):
            continue
        if _hinted(tag, _NON_ARTICLE_HINTS) or _hinted(tag, _BYLINE_HINTS):
            tag.decompose()

    # The LARGEST surviving <article>, not the first one. Business of Home
    # wraps each related-story card in its own <article>, so "the first one"
    # can be a teaser for a different story.
    articles = [a for a in soup.find_all("article") if not getattr(a, "decomposed", False)]
    root = None
    if articles:
        root = max(articles, key=lambda a: len(a.get_text(" ", strip=True)))
    if root is None or len(root.get_text(" ", strip=True)) < 200:
        root = soup.find("main") or soup.body or soup
    return root.get_text(" ", strip=True), " ".join(byline_bits)


def _mentioned_outside_a_credit(body: str, pad: str) -> bool:
    """True if the name occurs at least once not directly after a credit cue."""
    start = 0
    while True:
        i = body.find(pad, start)
        if i < 0:
            return False
        before = body[:i + 1].split()
        if not before or before[-1] not in _BYLINE_CUES:
            return True
        start = i + 1


def find_press_club_sources(url: str, author: str = "") -> list:
    """Return client designer full names named in the article's own body text.

    Only the story's visible words count. A name that appears solely in a
    related-articles module, an image alt attribute, a nav promo, a script
    payload or the byline is NOT a source. Reporting one as a source is the
    defect Liz Ferriera found on 2026-08-06: "Nureed Saeed" was in the alt
    text of a thumbnail for a different Business of Home article, and the old
    substring test read the whole raw HTML file as if it were the story.

    Pass the item's author so a writer who shares a client designer's name is
    not filed as a win. The name is still reported when it also appears in the
    story itself, which is the case where she really was interviewed.
    """
    url = str(url or "")
    if not url.lower().startswith("http"):
        return []
    key = (url, _flatten(author))
    if key in _ARTICLE_SOURCE_CACHE:
        return _ARTICLE_SOURCE_CACHE[key]
    found = []
    sources = get_clean_sources()
    if sources:
        try:
            req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            if STRICT_ARTICLE_BODY_ONLY:
                article, byline = _split_article_text(html)
            else:
                article, byline = html, ""
            body = _flatten(article)
            byline_flat = _flatten(byline) + _flatten(author)
            seen = set()
            for name in sources:
                low = _flatten(name).strip()
                if not low or low in seen:
                    continue
                pad = " " + low + " "
                if pad not in body:
                    continue
                # If this name is also the writer's, keep it only when it
                # appears somewhere that is not a credit line. A byline in
                # markup we did not recognise reads "... by Lauren Smith ...";
                # a real mention reads "... says designer Lauren Smith ...".
                if pad in byline_flat and not _mentioned_outside_a_credit(body, pad):
                    continue
                seen.add(low)
                found.append(name)
        except Exception:
            found = []
    _ARTICLE_SOURCE_CACHE[key] = found
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
        overview_lines = ["## Today's Publications", "", "**Priority outlets**", ""]
        _shown_more = False
        for outlet, outlet_items in sorted_outlets:
            if not self._is_tier_1(outlet) and not _shown_more:
                overview_lines += ["", "**More outlets**", ""]
                _shown_more = True
            anchor = self._slugify(outlet)
            overview_lines.append(f"- [{outlet} ({len(outlet_items)})](#source-{anchor})")
        overview_lines += ["", f"**Total Articles Today: {len(items)}**", "", "---", ""]
        overview = "\n".join(overview_lines)

        # --- Per-outlet sections: simple bullet list per article --------
        section_parts = []
        _shown_more_sections = False
        for outlet, outlet_items in sorted_outlets:
            if not self._is_tier_1(outlet) and not _shown_more_sections:
                section_parts.append("\n---\n\n### More outlets\n\n")
                _shown_more_sections = True
            anchor = self._slugify(outlet)
            section_parts.append(f'<a id="source-{anchor}"></a>\n')
            section_parts.append(f"## {outlet} ({len(outlet_items)})\n\n")
            for item in outlet_items:
                section_parts.append(self._format_item_simple(item, language))
                section_parts.append("\n")
            section_parts.append("\n")

       # --- 🏆 Press House Wins: today's auto-detected client articles + recent logged wins ---
        wins_parts = []
        todays = [it for it in items if find_press_club_sources(it.url, it.author)]
        today_urls = {_normalize_url(it.url) for it in todays}
        recent_wins = [w for w in get_recent_press_house_wins()
                       if _normalize_url(w["url"]) not in today_urls]
        if feed_failed():
            # Never let a feed outage look like a quiet day with no press.
            wins_parts.append(
                "## 🏆 Press House Wins\n\n"
                "> ⚠️ **Wins could not be checked today.** The press tracker feed did not "
                "respond, so no designer detection ran on this digest. Today's articles are "
                "below and are complete; only the wins layer is missing. This is a systems "
                "problem, not a quiet news day.\n\n---\n\n"
            )
        elif todays or recent_wins:
            wins_parts.append("## 🏆 Press House Wins\n\n")
            for item in todays:
                wins_parts.append(self._format_item_simple(item, language, wins_mode=True))
                wins_parts.append("\n")
            for w in recent_wins:
                designers = [d.strip() for d in (w["designer"] or "").replace(";", ",").split(",") if d.strip()]
                source_tags = " ".join(f"`⭐ {d} ⭐`" for d in designers) or "`⭐ Press Club Source ⭐`"
                meta = f"by {w['writer']} · {w['date']}" if w["writer"] else w["date"]
                wins_parts.append(
                    f"- [{w['story']}]({w['url']}) {source_tags} `{w['outlet']}` *{meta}*\n"
                )
            wins_parts.append("\n---\n\n")

        # --- KPI data for the header cards (read by the layout JS) ---
        _sheet_wins = get_press_house_wins()
        _designers = set()
        _to_file = 0
        for _it in items:
            _nu = _normalize_url(_it.url)
            if _nu in _sheet_wins:
                _designers.update(d.strip() for d in (_sheet_wins[_nu] or "").replace(";", ",").split(",") if d.strip())
            else:
                _f = find_press_club_sources(_it.url, _it.author)
                if _f:
                    _designers.update(_f)
                    _to_file += 1
        _stats = get_feed().get("stats", {}) or {}
        kpi_data = (
            '<div id="kpi-data" style="display:none"'
            f' data-designers-today="{len(_designers)}"'
            f' data-designers-month="{str(_stats.get("designers_this_month", "") or "").strip()}"'
            f' data-record="{str(_stats.get("all_time", "") or "").strip()}"></div>\n\n'
        )
        return header + kpi_data + "".join(wins_parts) + overview + "".join(section_parts)

    def _format_item_simple(self, item: ContentItem, language: str, wins_mode: bool = False) -> str:
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
                time_str = item.published_at.strftime(f"%b {day}")

        # Title links open in a new tab (raw <a> since Markdown has no
        # target="_blank" syntax; Kramdown/Jekyll renders embedded HTML fine).
        lines = [f'- <a href="{url}" target="_blank" rel="noopener">{title}</a>']

        # Tags: from the RSS entry's own <category> tags (metadata["tags"]),
        # free, no AI call. Skipped if the feed didn't provide any.
        if wins_mode:
            # Press House Wins rows: only the outlet tag (the source tag is added below).
            lines.append(f"  `{self._outlet_name(item)}`")
        else:
            tags = item.metadata.get("tags") or []
            clean_tags = [str(t).strip() for t in tags if str(t).strip()]
            if clean_tags:
                tags_str = " ".join(f"`{t}`" for t in clean_tags[:6])
                lines.append(f"  {tags_str}")
        # ⭐ Press Club Source: logged win (by link), else a client designer found in the text
        _designer = get_press_house_wins().get(_normalize_url(item.url))
        if _designer:
            lines.append(f"  `⭐ {_designer} ⭐`")
        elif _designer == "":
            lines.append("  `⭐ Press Club Source ⭐`")
        else:
            _found = find_press_club_sources(item.url, item.author)
            if _found:
                # Detected in a fresh scraped article but NOT in the tracker sheet -> champagne marker.
                lines.append(f"  `🍾 Press Club Source: {', '.join(_found)} 🍾`")

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
    def _is_tier_1(outlet: str) -> bool:
        """True for the top TIER_1_SIZE outlets in Alyssa's combined ranking."""
        idx = _OUTLET_RANK_INDEX.get(outlet.strip().lower())
        return idx is not None and idx < TIER_1_SIZE

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
