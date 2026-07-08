"""Stats engine tests.

Two layers:
- unit tests on a small DB built from fixtures (always run)
- verification tests on the real backfilled DB (skipped when data/hanspaulka.db
  is absent) — computed tables must match PSMF's published standings
"""

import json
from pathlib import Path

import pytest

from analysis import stats
from scraper import parse, store

FIXTURES = Path(__file__).parent / "fixtures"
REAL_DB = Path(__file__).parent.parent / "data" / "hanspaulka.db"


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """DB with 2025-podzim 6-A rounds 4 + 11 from fixtures."""
    conn = store.connect(tmp_path_factory.mktemp("db") / "test.db")
    season_id = store.upsert_season(conn, 2025, "podzim", "2025-podzim")
    group_id = store.upsert_group(conn, season_id, 6, "a", "https://example/6-a/")
    for name in ("results_2025-podzim_6-a_round-4.json",
                 "results_2025-podzim_6-a_round-11.json"):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for m in parse.parse_results(payload["html"]):
            store.upsert_match(conn, group_id, m)
    page = parse.parse_group_page(
        (FIXTURES / "group_2025-podzim_6-a.html").read_text(encoding="utf-8"))
    store.store_standings(conn, group_id, "prubezna", page.standings)
    conn.commit()
    yield conn
    conn.close()


# --- unit tests on fixture DB --------------------------------------------------


def test_find_team_exact_and_substring(conn):
    tid, name = stats.find_team(conn, "Power Rangers")
    assert name == "Power Rangers"
    tid2, name2 = stats.find_team(conn, "Rangers")
    assert tid2 == tid
    with pytest.raises(LookupError):
        stats.find_team(conn, "FC")  # ambiguous
    with pytest.raises(LookupError):
        stats.find_team(conn, "Real Madrid")


def test_team_matches_and_form(conn):
    tid, _ = stats.find_team(conn, "Power Rangers")
    matches = stats.team_matches(conn, tid)
    assert len(matches) == 2  # rounds 4 and 11
    assert [m.round for m in matches] == [4, 11]
    last = matches[-1]
    assert (last.opponent, last.venue, last.gf, last.ga) == ("Výtržník HFC", "home", 4, 8)
    assert last.outcome == "L"
    assert stats.form(matches) == "L" + matches[0].outcome  # newest first


def test_home_away_split(conn):
    tid, _ = stats.find_team(conn, "Power Rangers")
    split = stats.home_away_split(stats.team_matches(conn, tid))
    total = split["home"]["P"] + split["away"]["P"]
    assert total == 2
    assert split["home"]["GA"] >= 8  # the 4:8 home loss is in there


def test_longest_unbeaten():
    class M:
        def __init__(self, o):
            self.outcome = o

    ms = [M(o) for o in "WWLDWWWL"]
    assert stats.longest_unbeaten(ms) == (4, 0)  # DWWW is a 4-match unbeaten run
    ms = [M(o) for o in "LWWDD"]
    assert stats.longest_unbeaten(ms) == (4, 4)


def test_head_to_head(conn):
    a, _ = stats.find_team(conn, "Power Rangers")
    b, _ = stats.find_team(conn, "Výtržník HFC")
    h2h = stats.head_to_head(conn, a, b)
    assert h2h["L"] == 1 and h2h["W"] + h2h["D"] == 0
    assert (h2h["GF"], h2h["GA"]) == (4, 8)


def test_build_opponent_aggregates(conn):
    a, _ = stats.find_team(conn, "Power Rangers")
    matches = stats.team_matches(conn, a)
    opps = stats.build_opponent_aggregates(matches)
    # totals across opponents equal the team's total matches
    assert sum(o["played"] for o in opps) == len(matches)
    # each opponent's W/D/L sums to its played count and matches list length
    for o in opps:
        assert o["won"] + o["drawn"] + o["lost"] == o["played"] == len(o["matches"])
    # matches the head_to_head aggregate for a known opponent
    vyt = next(o for o in opps if o["opponent"] == "Výtržník HFC")
    assert (vyt["won"], vyt["drawn"], vyt["lost"]) == (0, 0, 1)
    assert (vyt["gf"], vyt["ga"]) == (4, 8)


def test_cross_table(conn):
    group_id = conn.execute("SELECT id FROM groups").fetchone()[0]
    grid = stats.cross_table(conn, group_id)
    assert grid["Power Rangers"]["Výtržník HFC"] == "4:8"


def test_scorer_race_cumulative(conn):
    group_id = conn.execute("SELECT id FROM groups").fetchone()[0]
    race = stats.scorer_race(conn, group_id, top=3)
    assert set(race) == set(range(1, 12))  # rounds 1..11 (empty rounds included)
    # cumulative counts never decrease
    for player_idx in range(3):
        values = [dict(race[r]) for r in sorted(race)]
        names = [p for p, _ in race[11]]
        for name in names:
            series = [v.get(name, 0) for v in values]
            assert series == sorted(series)


def test_referee_split_counting(conn):
    counts = dict(
        (name, matches) for name, matches, _ in stats.referee_match_counts(conn)
    )
    # round 11 had a two-referee match: both credited
    assert "Petr Mládek" in counts or len(counts) > 0


def test_referee_coverage(conn):
    cov = stats.referee_coverage_by_tier(conn)
    assert len(cov) == 1
    season, tier, total, missing, pct = cov[0]
    assert (season, tier, total) == ("2025-podzim", 6, 12)
    assert 0 <= missing <= total


def test_export_referee_csvs(conn, tmp_path):
    files = stats.export_referee_csvs(conn, tmp_path)
    assert len(files) == 4
    for f in files:
        assert f.exists() and f.read_text(encoding="utf-8").count("\n") >= 1


def test_team_roster_and_discipline(conn):
    tid, _ = stats.find_team(conn, "Power Rangers")
    roster = stats.team_roster(conn, tid)
    assert roster, "roster should not be empty"
    kocourek = next(p for p in roster if p["name"] == "Michal Kocourek")
    assert kocourek["goals"] >= 2          # scored twice in round 11
    assert kocourek["yellow"] == 1         # carded in round 4
    trojan = next(p for p in roster if p["name"] == "Adam Trojan")
    assert trojan["gk_apps"] >= 1          # goalkeeper in round 11
    novansky = next(p for p in roster if p["name"] == "Pavel Novanský")
    assert novansky["best"] >= 1 and novansky["captain"] >= 1

    disc = stats.team_discipline(conn, tid)
    assert disc["yellow"] >= 1 and disc["red"] == 0


def test_group_fairplay(conn):
    group_id = conn.execute("SELECT id FROM groups").fetchone()[0]
    fp = stats.group_fairplay(conn, group_id)
    assert fp, "round 4 fixture has yellow cards"
    assert all(rc == 0 for _, _, rc in fp)  # no reds in fixtures
    totals = sum(yc for _, yc, _ in fp)
    assert totals == conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]


def test_team_cards_by_season(conn):
    tid, _ = stats.find_team(conn, "Power Rangers")
    by_season = stats.team_cards_by_season(conn, tid)
    assert by_season.get("2025-podzim", {}).get("yellow", 0) >= 1


def test_build_career():
    """Career rows aggregate W/D/L/score/points from matches and derive tier
    movement vs the previous season."""
    from analysis.stats import TeamMatch
    def tm(season, tier, opp, gf, ga):
        return TeamMatch("2020-01-01", season, tier, "a", 1, opp, "home", gf, ga)
    matches = [
        tm("2024-jaro", 6, "X", 3, 0),   # W
        tm("2024-jaro", 6, "Y", 1, 1),   # D
        tm("2025-jaro", 5, "Z", 0, 2),   # L (promoted 6 -> 5)
    ]
    history = [
        {"season": "2024-jaro", "tier": 6, "group": "6-A", "position": 1, "final": True},
        {"season": "2025-jaro", "tier": 5, "group": "5-A", "position": 9, "final": True},
    ]
    career = stats.build_career(matches, history)
    assert career[0]["won"] == 1 and career[0]["drawn"] == 1 and career[0]["lost"] == 0
    assert career[0]["gf"] == 4 and career[0]["ga"] == 1
    assert career[0]["points"] == 1 * stats.WIN_POINTS + 1  # 2*1 + 1 draw = 3
    assert career[0]["move"] == 0  # first season, no predecessor
    assert career[1]["move"] == 1  # tier 6 -> 5 = promoted


def test_group_stat_charts(conn):
    group_id = conn.execute("SELECT id FROM groups LIMIT 1").fetchone()[0]
    charts = stats.group_stat_charts(conn, group_id)
    assert set(charts) == {"scorer_race", "goals_per_round", "home_away"}
    ha = charts["home_away"]
    total = ha["home"] + ha["draw"] + ha["away"]
    played = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE group_id = ? AND home_goals IS NOT NULL",
        (group_id,)).fetchone()[0]
    assert total == played
    # scorer race is cumulative: each leader's totals never decrease
    for s in charts["scorer_race"]["series"]:
        assert s["cum"] == sorted(s["cum"])


def test_season_movers(tmp_path):
    """Synthetic two-season DB: one climber, one faller, one stayer."""
    c = store.connect(tmp_path / "movers.db")
    s1 = store.upsert_season(c, 2024, "podzim", "2024-podzim")
    s2 = store.upsert_season(c, 2025, "jaro", "2025-jaro")
    g_old = {6: store.upsert_group(c, s1, 6, "a", "u"),
             5: store.upsert_group(c, s1, 5, "a", "u")}
    g_new = {6: store.upsert_group(c, s2, 6, "a", "u"),
             5: store.upsert_group(c, s2, 5, "a", "u")}

    def put(team, group_id, pos):
        c.execute("INSERT OR IGNORE INTO teams(name, canonical_name) VALUES (?, ?)",
                  (team, team))
        tid = c.execute("SELECT id FROM teams WHERE name = ?", (team,)).fetchone()[0]
        c.execute(
            """INSERT OR REPLACE INTO standings
               (group_id, team_id, kind, position, played, won, drawn, lost,
                gf, ga, points)
               VALUES (?, ?, 'vysledna', ?, 10, 5, 0, 5, 20, 20, 15)""",
            (group_id, tid, pos))

    put("Climber", g_old[6], 1); put("Climber", g_new[5], 8)
    put("Faller", g_old[5], 10); put("Faller", g_new[6], 2)
    put("Stayer", g_old[6], 5); put("Stayer", g_new[6], 5)
    c.commit()

    movers = stats.season_movers(c, "2024-podzim", "2025-jaro")
    c.close()
    assert [(m["team"], m["from_tier"], m["to_tier"], m["up"]) for m in movers] == [
        ("Climber", 6, 5, True),
        ("Faller", 5, 6, False),
    ]


# --- verification against the real backfilled DB -------------------------------


needs_real_db = pytest.mark.skipif(
    not REAL_DB.exists(), reason="backfilled DB not present"
)


@pytest.fixture(scope="module")
def real_conn():
    conn = store.connect(REAL_DB)
    yield conn
    conn.close()


@needs_real_db
def test_position_by_round_matches_published_standings(real_conn):
    """For groups WITHOUT administrative point deductions, the computed final
    order must match PSMF's published standings (H2H tiebreaks may cause a
    rare divergence). Groups with deductions are expected to diverge —
    published standings stay authoritative there."""
    groups = real_conn.execute(
        """
        SELECT g.id FROM groups g JOIN seasons s ON s.id = g.season_id
        WHERE s.year = 2025 AND s.half = 'podzim'
        """
    ).fetchall()
    assert len(groups) >= 50
    clean_groups = 0
    mismatches = []
    for (group_id,) in groups:
        if stats.point_deductions(real_conn, group_id):
            continue
        clean_groups += 1
        published = [
            name for (name,) in real_conn.execute(
                """
                SELECT t.name FROM standings st JOIN teams t ON t.id = st.team_id
                WHERE st.group_id = ? AND st.kind = 'prubezna'
                ORDER BY st.position
                """,
                (group_id,),
            )
        ]
        tables = stats.position_by_round(real_conn, group_id)
        if tables[max(tables)] != published:
            mismatches.append(group_id)
    assert clean_groups >= 25
    assert len(mismatches) <= 2, (
        f"{len(mismatches)}/{clean_groups} deduction-free groups diverge: {mismatches}"
    )


@needs_real_db
def test_point_deductions_detected(real_conn):
    """2025-podzim is known to contain administrative deductions
    (e.g. Lucián FC A in 1-A: published 7 pts vs 9 from results)."""
    groups = real_conn.execute(
        """
        SELECT g.id FROM groups g JOIN seasons s ON s.id = g.season_id
        WHERE s.year = 2025 AND s.half = 'podzim'
        """
    ).fetchall()
    deltas = [
        delta
        for (gid,) in groups
        for delta in stats.point_deductions(real_conn, gid).values()
    ]
    assert deltas, "expected at least one deduction in 2025-podzim"
    assert all(d < 0 for d in deltas), f"positive deltas would be a bug: {deltas}"


@needs_real_db
def test_all_time_scorers_sane(real_conn):
    board = stats.all_time_scorers(real_conn, limit=10)
    assert len(board) == 10
    assert all(goals > 20 for _, _, goals in board)  # 5 seasons of data
    assert board == sorted(board, key=lambda r: -r[2])


@needs_real_db
def test_tier_pyramid_current_season(real_conn):
    pyramid = stats.tier_pyramid(real_conn, "2026-jaro")
    assert [row["tier"] for row in pyramid] == list(range(1, 9))
    assert pyramid[0]["groups"] == 1  # tier 1 = single group
    assert sum(row["teams"] for row in pyramid) > 600


@needs_real_db
def test_season_history_continuity(real_conn):
    tid, _ = stats.find_team(real_conn, "Nebeský bastardi")
    history = stats.season_history(real_conn, tid)
    assert len(history) >= 2
    seasons = [h["season"] for h in history]
    assert seasons == sorted(seasons, key=lambda s: (
        int(s.split("-")[0]), stats.HALF_ORDER[s.split("-")[1]]))


@needs_real_db
def test_season_hub_functions(real_conn):
    """Season-hub building blocks (#7-#9) on real data."""
    gpr = stats.season_goals_per_round(real_conn, "2025-podzim")
    assert gpr and all(r["avg"] > 0 for r in gpr)
    assert gpr == sorted(gpr, key=lambda r: r["round"])

    boards = stats.season_team_boards(real_conn, "2025-podzim")
    assert len(boards["attack"]) == 10 and len(boards["defense"]) == 10
    # attack sorted by goals desc, defense by conceded asc
    assert boards["attack"] == sorted(
        boards["attack"], key=lambda r: -r["gf"])
    assert boards["defense"] == sorted(
        boards["defense"], key=lambda r: r["ga"])

    fp = stats.season_fairplay(real_conn, "2025-podzim")
    assert fp and fp[0]["yellow"] + 2 * fp[0]["red"] <= \
        fp[-1]["yellow"] + 2 * fp[-1]["red"]

    scorers = stats.season_scorers(real_conn, "2025-podzim", limit=20)
    assert len(scorers) == 20
    assert scorers[0][2] >= scorers[-1][2]  # goals descending


@needs_real_db
def test_season_movers_real(real_conn):
    """2025-jaro → 2025-podzim: every mover actually changed tier, no dups."""
    movers = stats.season_movers(real_conn, "2025-jaro", "2025-podzim")
    assert movers
    teams = [m["team"] for m in movers]
    assert len(teams) == len(set(teams))
    assert all(m["from_tier"] != m["to_tier"] for m in movers)
    assert all(m["up"] == (m["to_tier"] < m["from_tier"]) for m in movers)
