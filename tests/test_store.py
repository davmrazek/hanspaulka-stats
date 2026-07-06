"""Store tests: fixture data -> tmp SQLite, with idempotency checks."""

import json
from pathlib import Path

import pytest

from scraper import parse, store

FIXTURES = Path(__file__).parent / "fixtures"


def load_results(name: str) -> list[parse.MatchResult]:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return parse.parse_results(json.loads(raw)["html"])


@pytest.fixture()
def conn(tmp_path):
    conn = store.connect(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture()
def group_id(conn):
    season_id = store.upsert_season(conn, 2025, "podzim", "2025-podzim")
    return store.upsert_group(conn, season_id, 6, "a", "https://example/6-a/")


def ingest_all(conn, group_id):
    page = parse.parse_group_page(
        (FIXTURES / "group_2025-podzim_6-a.html").read_text(encoding="utf-8")
    )
    for name in ("results_2025-podzim_6-a_round-4.json",
                 "results_2025-podzim_6-a_round-11.json"):
        for m in load_results(name):
            store.upsert_match(conn, group_id, m)
    store.store_standings(conn, group_id, "prubezna", page.standings)
    duties = parse.parse_piskani(
        (FIXTURES / "piskani_2025-podzim_6-a.html").read_text(encoding="utf-8")
    )
    store.store_duties(conn, group_id, duties)
    conn.commit()


def dump(conn) -> list[str]:
    return list(conn.iterdump())


def test_ingest_fixture_rounds(conn, group_id):
    ingest_all(conn, group_id)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("matches", "goals", "cards", "appearances", "players",
                  "teams", "standings", "referee_duties")
    }
    assert counts["matches"] == 12  # 2 rounds x 6 matches
    assert counts["teams"] == 12
    assert counts["standings"] == 12
    assert counts["referee_duties"] == 28
    assert counts["goals"] > 0 and counts["cards"] > 0 and counts["appearances"] > 0


def test_goals_match_scores(conn, group_id):
    ingest_all(conn, group_id)
    rows = conn.execute(
        """
        SELECT m.id, m.home_goals + m.away_goals, COUNT(g.id)
        FROM matches m LEFT JOIN goals g ON g.match_id = m.id
        GROUP BY m.id
        """
    ).fetchall()
    for _, expected, actual in rows:
        assert expected == actual


def test_own_goal_has_null_player(conn, group_id):
    ingest_all(conn, group_id)
    og = conn.execute(
        "SELECT player_id, minute FROM goals WHERE own_goal = 1"
    ).fetchall()
    assert og and all(player_id is None for player_id, _ in og)


def test_reingest_is_idempotent(conn, group_id):
    ingest_all(conn, group_id)
    first = dump(conn)
    ingest_all(conn, group_id)
    assert dump(conn) == first


def test_players_scoped_per_team(conn, group_id):
    ingest_all(conn, group_id)
    dup = conn.execute(
        "SELECT name, team_id, COUNT(*) FROM players GROUP BY name, team_id HAVING COUNT(*) > 1"
    ).fetchall()
    assert dup == []
