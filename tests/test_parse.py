"""Parser tests against saved fixtures (tests/fixtures/), never the live site."""

import json
from pathlib import Path

import pytest

from scraper import parse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_results(name: str) -> str:
    return json.loads(load(name))["html"]


# --- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Po\xa08.12.25", "2025-12-08"),
        ("8. 12. 25", "2025-12-08"),
        ("Út 2.9.25", "2025-09-02"),
        ("nonsense", None),
    ],
)
def test_parse_czech_date(text, expected):
    assert parse.parse_czech_date(text) == expected


# --- match results (round 11 fixture: no cards, has OG) --------------------


@pytest.fixture(scope="module")
def round11():
    return parse.parse_results(load_results("results_2025-podzim_6-a_round-11.json"))


def test_round11_has_six_matches(round11):
    assert len(round11) == 6
    assert all(m.round == 11 for m in round11)


def test_round11_first_match_basics(round11):
    m = round11[0]
    assert m.gameid == 301575
    assert m.date == "2025-12-08"
    assert m.time == "20:45"
    assert m.pitch == "CESMI"
    assert m.home_team == "Power Rangers"
    assert m.away_team == "Výtržník HFC"
    assert (m.home_goals, m.away_goals) == (4, 8)
    assert (m.ht_home, m.ht_away) == (1, 5)
    assert m.referee == "Yevhen Myroniak"
    assert m.commentary.startswith("Byl to dobrý zápas")


def test_round11_goals_including_own_goal(round11):
    m = round11[0]
    home_goals = [g for g in m.goals if g.side == "home"]
    away_goals = [g for g in m.goals if g.side == "away"]
    assert len(home_goals) == m.home_goals == 4
    assert len(away_goals) == m.away_goals == 8

    assert home_goals[0] == parse.GoalEvent("home", 9, "Pavel Novanský")
    # "48., 50. Michal Kocourek" expands to two events
    kocourek = [g for g in home_goals if g.player == "Michal Kocourek"]
    assert [g.minute for g in kocourek] == [48, 50]
    # "52. OG" — own goal, no scorer published
    og = [g for g in home_goals if g.own_goal]
    assert len(og) == 1 and og[0].minute == 52 and og[0].player is None


def test_round11_lineups(round11):
    m = round11[0]
    assert m.home_lineup.goalkeeper == "Adam Trojan"
    assert "Michal Kocourek" in m.home_lineup.players
    assert m.home_lineup.captain == "Pavel Novanský"  # is-best-and-captain
    assert m.home_lineup.best == "Pavel Novanský"
    assert m.away_lineup.goalkeeper == "Filip Šmídek"
    assert m.away_lineup.captain == "Jan Bohata"
    assert m.away_lineup.best == "Patrik Šulek"


def test_round11_every_match_score_matches_goal_events(round11):
    for m in round11:
        assert len([g for g in m.goals if g.side == "home"]) == m.home_goals
        assert len([g for g in m.goals if g.side == "away"]) == m.away_goals


# --- match results (round 4 fixture: has yellow cards) ---------------------


@pytest.fixture(scope="module")
def round4():
    return parse.parse_results(load_results("results_2025-podzim_6-a_round-4.json"))


def test_round4_cards(round4):
    cards = [c for m in round4 for c in m.cards]
    assert cards, "round 4 fixture should contain cards"
    assert all(c.color == "yellow" for c in cards)
    kocourek = [c for c in cards if c.player == "Michal Kocourek"]
    assert len(kocourek) == 1 and kocourek[0].minute == 55


# --- group page -------------------------------------------------------------


@pytest.fixture(scope="module")
def group():
    return parse.parse_group_page(load("group_2025-podzim_6-a.html"))


def test_group_name(group):
    assert group.name == "Hanspaulská liga 6A"


def test_group_results_urls_cover_all_rounds(group):
    assert sorted(group.results_urls) == list(range(1, 12))
    assert group.results_urls[3].startswith(
        "/souteze/2025-hanspaulska-liga-podzim/6-a/?cmd=results"
    )
    assert "round=3" in group.results_urls[3]


def test_group_tables_urls(group):
    assert {"actual", "final", "cross"} <= set(group.tables_urls)


def test_group_standings(group):
    assert len(group.standings) == 12
    top = group.standings[0]
    assert top == parse.StandingRow(
        position=1, team="Nebeský bastardi", played=11,
        won=7, drawn=3, lost=1, gf=46, ga=34, points=17,
    )
    # sanity: every team played 11, positions are 1..12
    assert [r.position for r in group.standings] == list(range(1, 13))
    assert all(r.played == 11 for r in group.standings)
    assert all(r.won + r.drawn + r.lost == r.played for r in group.standings)


def test_group_embedded_duties(group):
    assert group.duties  # truncated version of the piskani page
    assert group.duties[0].team == "Veeam"


# --- rozpis piskani ----------------------------------------------------------


def test_piskani_page():
    duties = parse.parse_piskani(load("piskani_2025-podzim_6-a.html"))
    assert len(duties) == 28
    first = duties[0]
    assert first == parse.RefereeDuty(
        date="2025-09-02", times="18:00, 19:15", pitch="HRAB2", team="Veeam"
    )
    # duty teams are teams of this group
    assert {d.team for d in duties} <= {
        "Veeam", "Power Rangers", "NeReal Žižkov FC", "Pražští lvi",
        "Nebeský bastardi", "Výtržník HFC", "Sanitka AS", "A.Č.A.B. FC",
        "Střílíme veverky FC", "Habet Praha FŇ", "Akademie Flirtu", "Řepští sršni",
    }


# --- failure modes -----------------------------------------------------------


def test_group_page_without_results_endpoints_is_ok():
    # cancelled seasons (COVID 2020-jaro) have cmd=games but no cmd=results
    html = """<html><body><h1>Hanspaulská liga 1A</h1>
      <a href="#" data-url="/souteze/x/1-a/?cmd=games&type=old&round=1"></a>
      <table class="tables-table"><tr><th>h</th></tr>
      <tr><td>1.</td><td>Tým</td><td>0</td><td>0</td><td>0</td><td>0</td>
      <td>0:0</td><td>0</td></tr></table></body></html>"""
    page = parse.parse_group_page(html)
    assert page.results_urls == {}
    assert page.standings[0].played == 0


def test_piskani_empty_rozpis_is_ok():
    # top tiers have PSMF referees, no duty rozpis (e.g. 2026-jaro 1-A)
    html = "<html><body><h2>Rozpis pískání</h2><p>Nenalezen žádná záznam.</p></body></html>"
    assert parse.parse_piskani(html) == ()


def test_parsers_fail_loudly_on_wrong_page():
    with pytest.raises(parse.ParseError):
        parse.parse_results("<html><body>redesigned</body></html>")
    with pytest.raises(parse.ParseError):
        parse.parse_group_page("<html><body>redesigned</body></html>")
    with pytest.raises(parse.ParseError):
        parse.parse_piskani("<html><body>redesigned</body></html>")
