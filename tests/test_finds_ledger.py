"""The finds ledger: a champagne find stays on the digest until it is logged.

Pure-function tests for merge_finds_ledger and load_finds_ledger. No network,
no sheet, no digest run.
"""
from __future__ import annotations

import json

from src.ai.summarizer import load_finds_ledger, merge_finds_ledger


def _find(url, day, names=("Amy Peltier",), title="t", outlet="Homes & Gardens"):
    return {"designers": list(names), "title": title, "url": url, "outlet": outlet, "published": day}


def test_new_find_enters_with_first_seen_today():
    out = merge_finds_ledger([], [_find("https://a.com/x", "2026-09-10")], {}, "2026-09-10")
    assert len(out) == 1
    assert out[0]["first_seen"] == "2026-09-10"
    assert out[0]["last_seen"] == "2026-09-10"


def test_find_from_yesterday_survives_a_day_with_no_new_finds():
    prior = merge_finds_ledger([], [_find("https://a.com/x", "2026-09-09")], {}, "2026-09-09")
    out = merge_finds_ledger(prior, [], {}, "2026-09-10")
    assert [e["url"] for e in out] == ["https://a.com/x"]
    assert out[0]["first_seen"] == "2026-09-09"


def test_logged_in_the_sheet_leaves_the_ledger():
    prior = merge_finds_ledger([], [_find("https://a.com/x", "2026-09-09")], {}, "2026-09-09")
    tracked = {"a.com/x": "Amy Peltier"}  # normalized key, as get_press_house_wins returns
    out = merge_finds_ledger(prior, [], tracked, "2026-09-10")
    assert out == []


def test_same_url_seen_again_keeps_original_first_seen_and_merges_names():
    prior = merge_finds_ledger([], [_find("https://a.com/x", "2026-09-04", ("Amy Peltier",))], {}, "2026-09-04")
    out = merge_finds_ledger(prior, [_find("https://www.a.com/x/", "2026-09-10", ("Kelly Bartley",))], {}, "2026-09-10")
    assert len(out) == 1
    assert out[0]["first_seen"] == "2026-09-04"
    assert out[0]["last_seen"] == "2026-09-10"
    assert out[0]["designers"] == ["Amy Peltier", "Kelly Bartley"]


def test_cold_trail_drops_after_keep_days():
    prior = merge_finds_ledger([], [_find("https://a.com/old", "2026-06-01")], {}, "2026-06-01")
    out = merge_finds_ledger(prior, [], {}, "2026-09-10", keep_days=31)
    assert out == []
    still = merge_finds_ledger(prior, [], {}, "2026-06-20", keep_days=31)
    assert len(still) == 1


def test_newest_first():
    a = _find("https://a.com/1", "2026-09-04")
    b = _find("https://a.com/2", "2026-09-08")
    out = merge_finds_ledger([], [a, b], {}, "2026-09-10")
    assert [e["url"] for e in out] == ["https://a.com/2", "https://a.com/1"]


def test_bad_entries_never_raise():
    out = merge_finds_ledger([{"nope": 1}, {"url": ""}], [{"url": "https://a.com/z"}], {}, "not-a-date")
    assert [e["url"] for e in out] == ["https://a.com/z"]


def test_load_ledger_missing_or_corrupt_is_empty(tmp_path):
    assert load_finds_ledger(str(tmp_path / "missing.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_finds_ledger(str(bad)) == []
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"finds": [{"url": "https://a.com/1"}, {"title": "no url"}]}), encoding="utf-8")
    assert [e["url"] for e in load_finds_ledger(str(good))] == ["https://a.com/1"]
