"""Topical relevance filter for the Press House Daily Digest.

Alex's world is interior design. The big lifestyle feeds are not: Better
Homes & Gardens ships pot roast recipes, Real Simple ships Amazon bra
roundups, Southern Living ships cornstarch skincare tips. All of it is
noise in a press digest for designers.

Two signals are used, in order:

1. **The article's own category tags.** Most feeds tag generously
   ("Recipes by Ingredient", "Skincare", "Fashion Accessory Reviews").
   This is the precise signal and it is preferred wherever it exists.

   One trap: every Dotdash feed also stamps each item with the channel's
   branding tag - "Real Simple: Home Decor Ideas, Recipes, DIY & Beauty
   Tips" - which contains "Recipes" and "Beauty" and would therefore
   delete the entire feed. Any tag appearing on 70% or more of a feed's
   items is treated as branding and ignored. That threshold is what makes
   category filtering safe here.

2. **The title**, as a fallback. Word-boundary matches only, so "thrift
   shop" survives a "shop" rule ... a thrifting piece is design content.
   Feeds with no categories at all (the ones we generate ourselves) can
   instead set `require_titles`: an allow-list where the title or URL
   must match something design or property related.

Rules live in data/topics.json so they can be tuned without touching
code. Everything is per-outlet overridable, because "celebrity" is noise
in House Beautiful and is the entire point in Martha Stewart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_RULES = None
BRANDING_THRESHOLD = 0.7


def _load() -> dict:
    global _RULES
    if _RULES is None:
        p = Path(__file__).resolve().parents[2] / "data" / "topics.json"
        try:
            _RULES = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _RULES = {"_default": {}, "outlets": {}}
    return _RULES


def rules_for(outlet: str) -> dict:
    r = _load()
    base = dict(r.get("_default") or {})
    over = (r.get("outlets") or {}).get(outlet) or {}

    cats = list(base.get("deny_categories") or [])
    cats += list(over.get("deny_categories_add") or [])
    for x in over.get("deny_categories_remove") or []:
        if x in cats:
            cats.remove(x)

    titles = list(base.get("deny_titles") or [])
    titles += list(over.get("deny_titles_add") or [])
    for x in over.get("deny_titles_remove") or []:
        if x in titles:
            titles.remove(x)

    return {
        "deny_categories": cats,
        "deny_titles": titles,
        "require_titles": list(over.get("require_titles") or []),
        "rescue_titles": list(base.get("rescue_titles") or [])
                         + list(over.get("rescue_titles_add") or []),
    }


def branding_tags(all_item_categories: list) -> set:
    """Tags on >=70% of a feed's items are the channel's own branding, not topics."""
    n = len(all_item_categories)
    if not n:
        return set()
    counts = {}
    for cats in all_item_categories:
        for c in set(cats):
            counts[c] = counts.get(c, 0) + 1
    return {c for c, k in counts.items() if k >= BRANDING_THRESHOLD * n}


def check(outlet: str, title: str, categories: list, url: str = "",
          branding: set = None) -> tuple:
    """Return (keep, reason). reason is None when kept."""
    r = rules_for(outlet)
    branding = branding or set()
    title = title or ""

    for c in categories or []:
        if c in branding:
            continue
        cl = c.lower()
        for d in r["deny_categories"]:
            if d in cl:
                return False, "category '%s'" % c

    for pat in r["deny_titles"]:
        if re.search(pat, title, re.I):
            # A deal-shaped headline is still design press when it is about
            # furniture, decor, a patio, a kitchen. Alex's designers get
            # quoted in exactly these product roundups, so a blanket
            # "amazon" or "under $50" rule would throw away real wins.
            # Category evidence is stronger and is NOT rescued.
            if any(re.search(p, title, re.I) for p in r["rescue_titles"]):
                break
            return False, "title matched %s" % pat

    req = r["require_titles"]
    if req:
        hay = title + " " + (url or "")
        if not any(re.search(p, hay, re.I) for p in req):
            return False, "no design or property term in title"

    return True, None
