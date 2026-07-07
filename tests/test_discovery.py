"""Season/group discovery and canonicalization tests. No live requests."""

import datetime as dt
from pathlib import Path

import pytest

from scraper import fetch, parse, store
from scraper import run

FIXTURES = Path(__file__).parent / "fixtures"


# --- season index parsing ----------------------------------------------------


def test_parse_season_index_discovers_groups():
    html = (FIXTURES / "season_index_2025-podzim.html").read_text(encoding="utf-8")
    groups = parse.parse_season_index(html, "/souteze/2025-hanspaulska-liga-podzim/")
    assert len(groups) > 30  # ~57 groups in 2025-podzim; never hardcode exact list
    tiers = {g.tier for g in groups}
    assert tiers == set(range(1, 9))
    assert parse.GroupRef(6, "a", "/souteze/2025-hanspaulska-liga-podzim/6-a/") in groups
    # tier 1 has only group A
    assert [g.letter for g in groups if g.tier == 1] == ["a"]
    # sorted by (tier, letter)
    assert [(g.tier, g.letter) for g in groups] == sorted(
        (g.tier, g.letter) for g in groups
    )


def test_parse_season_index_wrong_page_fails():
    with pytest.raises(parse.ParseError):
        parse.parse_season_index("<html></html>", "/souteze/x/")


# --- current-season rule / slug iteration ------------------------------------


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        (dt.date(2026, 7, 6), (2026, "jaro")),
        (dt.date(2026, 3, 1), (2026, "jaro")),
        (dt.date(2026, 9, 1), (2026, "podzim")),
        (dt.date(2025, 12, 31), (2025, "podzim")),
    ],
)
def test_guess_current(date, expected):
    assert run.guess_current(date) == expected


def test_iter_season_slugs_goes_back_in_time():
    it = run.iter_season_slugs(2026, "jaro")
    assert [next(it) for _ in range(4)] == [
        (2026, "jaro"), (2025, "podzim"), (2025, "jaro"), (2024, "podzim"),
    ]


# --- season probing -----------------------------------------------------------


def test_discover_seasons_tolerates_gaps_and_stops(monkeypatch):
    existing = {(2026, "jaro"), (2025, "podzim"), (2025, "jaro"), (2024, "podzim")}

    def fake_get(url, **kwargs):
        for year, half in existing:
            if f"{year}-hanspaulska-liga-{half}" in url:
                return f"<html>{year}-{half}</html>"
        raise fetch.NotFoundError(url)

    monkeypatch.setattr(run.fetch, "get", fake_get)
    monkeypatch.setattr(run, "guess_current", lambda *a: (2026, "podzim"))  # 404s

    found = run.discover_seasons(10)
    assert [(y, h) for y, h, _ in found] == [
        (2026, "jaro"), (2025, "podzim"), (2025, "jaro"), (2024, "podzim"),
    ]  # probing stopped after consecutive misses beyond the archive


def test_discover_seasons_stops_at_count(monkeypatch):
    monkeypatch.setattr(run.fetch, "get", lambda url, **kw: "<html></html>")
    monkeypatch.setattr(run, "guess_current", lambda *a: (2026, "jaro"))
    found = run.discover_seasons(3)
    assert [(y, h) for y, h, _ in found] == [
        (2026, "jaro"), (2025, "podzim"), (2025, "jaro"),
    ]


# --- canonicalization review ---------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Nebeský bastardi", "Nebesky Bastardi", True),
        ("A.Č.A.B. FC", "A Č A B FC", True),
        ("Veeam", "Veeam B", False),
        ("Power Rangers", "Power  Rangers", True),
    ],
)
def test_normalize_team_name(a, b, same):
    na, nb = store.normalize_team_name(a), store.normalize_team_name(b)
    assert (na == nb) is same


def test_find_name_conflicts(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.upsert_team(conn, "Nebeský bastardi")
    store.upsert_team(conn, "Nebesky Bastardi")
    store.upsert_team(conn, "Veeam")
    conflicts = store.find_name_conflicts(conn)
    assert len(conflicts) == 1
    assert set(conflicts[0][1]) == {"Nebeský bastardi", "Nebesky Bastardi"}
    conn.close()


# --- fetch 404 behaviour --------------------------------------------------------


def test_notfound_is_not_retried(tmp_path, monkeypatch):
    calls = []

    class R:
        status_code = 404
        text = ""
        apparent_encoding = "utf-8"

        def raise_for_status(self):
            raise AssertionError("should have raised NotFoundError before")

    def fake_get(url, **kwargs):
        calls.append(url)
        return R()

    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch.requests, "get", fake_get)
    with pytest.raises(fetch.NotFoundError):
        fetch.get("https://www.psmf.cz/souteze/1999-hanspaulska-liga-jaro/",
                  cache_dir=tmp_path)
    assert len(calls) == 1  # no retries on 4xx
