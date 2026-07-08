"""Derived stats over data/hanspaulka.db.

CLI:
  python -m analysis.stats --team "Nebeský bastardi"          # team dossier
  python -m analysis.stats --team "X" --vs "Y"                # head-to-head
  python -m analysis.stats --referee-export data/exports      # private CSVs

Hanspaulka scoring: 2 points for a win, 1 for a draw (verified against
stored standings). Seasons order chronologically as (year, jaro < podzim).
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scraper.store import DB_PATH, connect

WIN_POINTS = 2
HALF_ORDER = {"jaro": 0, "podzim": 1}


def season_sort_key(year: int, half: str) -> tuple[int, int]:
    return (year, HALF_ORDER[half])


# --- team lookup -------------------------------------------------------------


def find_team(conn: sqlite3.Connection, name: str) -> tuple[int, str]:
    """Resolve a team by exact, canonical, or unique substring match."""
    rows = conn.execute(
        "SELECT id, name FROM teams WHERE name = ? OR canonical_name = ?",
        (name, name),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT id, name FROM teams WHERE name LIKE ? ORDER BY name",
            (f"%{name}%",),
        ).fetchall()
    if not rows:
        raise LookupError(f"no team matching {name!r}")
    if len(rows) > 1:
        options = ", ".join(r[1] for r in rows[:10])
        raise LookupError(f"ambiguous team {name!r}: {options}")
    return rows[0]


# --- team stats ----------------------------------------------------------------


@dataclass(frozen=True)
class TeamMatch:
    date: str
    season: str
    tier: int
    letter: str
    round: int
    opponent: str
    venue: str  # 'home' | 'away'
    gf: int
    ga: int

    @property
    def outcome(self) -> str:
        if self.gf > self.ga:
            return "W"
        return "D" if self.gf == self.ga else "L"


def team_matches(conn, team_id: int) -> list[TeamMatch]:
    """All played matches of a team, chronological."""
    rows = conn.execute(
        """
        SELECT m.date, s.year || '-' || s.half, g.tier, g.letter, m.round,
               opp.name,
               CASE WHEN m.home_team_id = :tid THEN 'home' ELSE 'away' END,
               CASE WHEN m.home_team_id = :tid THEN m.home_goals ELSE m.away_goals END,
               CASE WHEN m.home_team_id = :tid THEN m.away_goals ELSE m.home_goals END
        FROM matches m
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams opp ON opp.id = CASE WHEN m.home_team_id = :tid
                                        THEN m.away_team_id ELSE m.home_team_id END
        WHERE (m.home_team_id = :tid OR m.away_team_id = :tid)
          AND m.home_goals IS NOT NULL
        ORDER BY m.date, m.round
        """,
        {"tid": team_id},
    ).fetchall()
    return [TeamMatch(*r) for r in rows]


def form(matches: list[TeamMatch], n: int = 5) -> str:
    """Most recent first: e.g. 'WWDLW'."""
    return "".join(m.outcome for m in reversed(matches[-n:]))


def home_away_split(matches: list[TeamMatch]) -> dict[str, dict[str, int]]:
    split = {v: {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0}
             for v in ("home", "away")}
    for m in matches:
        s = split[m.venue]
        s["P"] += 1
        s[m.outcome] += 1
        s["GF"] += m.gf
        s["GA"] += m.ga
    return split


def biggest_results(matches: list[TeamMatch], limit: int = 3
                    ) -> tuple[list[TeamMatch], list[TeamMatch]]:
    """(biggest wins, biggest losses) by goal difference then goals scored."""
    wins = sorted((m for m in matches if m.outcome == "W"),
                  key=lambda m: (m.gf - m.ga, m.gf), reverse=True)
    losses = sorted((m for m in matches if m.outcome == "L"),
                    key=lambda m: (m.ga - m.gf, m.ga), reverse=True)
    return wins[:limit], losses[:limit]


def season_history(conn, team_id: int) -> list[dict]:
    """Tier/group/final position per season — promotion/relegation trajectory.
    Uses vysledna standings when available, prubezna otherwise."""
    rows = conn.execute(
        """
        SELECT s.year, s.half, g.tier, g.letter,
               MAX(CASE WHEN st.kind = 'vysledna' THEN st.position END),
               MAX(CASE WHEN st.kind = 'prubezna' THEN st.position END)
        FROM standings st
        JOIN groups g ON g.id = st.group_id
        JOIN seasons s ON s.id = g.season_id
        WHERE st.team_id = ?
        GROUP BY g.id
        """,
        (team_id,),
    ).fetchall()
    rows.sort(key=lambda r: season_sort_key(r[0], r[1]))
    history = []
    for year, half, tier, letter, final_pos, live_pos in rows:
        history.append({
            "season": f"{year}-{half}",
            "tier": tier,
            "group": f"{tier}-{letter.upper()}",
            "position": final_pos or live_pos,
            "final": final_pos is not None,
        })
    return history


def build_career(matches: list[TeamMatch], history: list[dict]) -> list[dict]:
    """Transfermarkt-style per-season career rows from already-loaded matches
    and season_history. W/D/L, score and points are derived from results
    (points = won*WIN_POINTS + drawn, so administrative deductions are not
    reflected — the group page stays authoritative for published points).
    `move` is the tier change vs the previous season: >0 promoted (postup),
    <0 relegated (sestup)."""
    agg: dict[str, dict[str, int]] = {}
    for m in matches:
        a = agg.setdefault(
            m.season, {"won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0})
        a["won" if m.outcome == "W" else "drawn" if m.outcome == "D" else "lost"] += 1
        a["gf"] += m.gf
        a["ga"] += m.ga
    rows = []
    prev_tier = None
    for h in history:  # chronological
        a = agg.get(h["season"], {"won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0})
        played = a["won"] + a["drawn"] + a["lost"]
        rows.append({
            **h, **a, "played": played,
            "points": a["won"] * WIN_POINTS + a["drawn"],
            "move": 0 if prev_tier is None else prev_tier - h["tier"],
        })
        prev_tier = h["tier"]
    return rows


def longest_unbeaten(matches: list[TeamMatch]) -> tuple[int, int]:
    """(longest unbeaten run, current unbeaten run) across all seasons."""
    longest = current = 0
    for m in matches:
        current = 0 if m.outcome == "L" else current + 1
        longest = max(longest, current)
    return longest, current


def head_to_head(conn, team_id: int, opp_id: int) -> dict:
    opp_name = conn.execute(
        "SELECT name FROM teams WHERE id = ?", (opp_id,)).fetchone()[0]
    matches = [m for m in team_matches(conn, team_id) if m.opponent == opp_name]
    return {
        "matches": matches,
        "W": sum(1 for m in matches if m.outcome == "W"),
        "D": sum(1 for m in matches if m.outcome == "D"),
        "L": sum(1 for m in matches if m.outcome == "L"),
        "GF": sum(m.gf for m in matches),
        "GA": sum(m.ga for m in matches),
    }


def team_roster(conn, team_id: int) -> list[dict]:
    """Per player: appearances, goalkeeper apps, goals, cards, best-player
    and captain counts. Players are scoped per team (no cross-team identity)."""
    rows = conn.execute(
        """
        SELECT p.name,
               (SELECT COUNT(*) FROM appearances a WHERE a.player_id = p.id) AS apps,
               (SELECT COUNT(*) FROM appearances a
                 WHERE a.player_id = p.id AND a.role = 'goalkeeper') AS gk_apps,
               (SELECT COUNT(*) FROM goals g
                 WHERE g.player_id = p.id AND g.own_goal = 0) AS goals,
               (SELECT COUNT(*) FROM cards c
                 WHERE c.player_id = p.id AND c.color = 'yellow') AS yellow,
               (SELECT COUNT(*) FROM cards c
                 WHERE c.player_id = p.id AND c.color = 'red') AS red,
               (SELECT COUNT(*) FROM appearances a
                 WHERE a.player_id = p.id AND a.is_best = 1) AS best,
               (SELECT COUNT(*) FROM appearances a
                 WHERE a.player_id = p.id AND a.is_captain = 1) AS captain
        FROM players p
        WHERE p.team_id = ?
        ORDER BY apps DESC, goals DESC, p.name
        """,
        (team_id,),
    ).fetchall()
    keys = ("name", "apps", "gk_apps", "goals", "yellow", "red", "best", "captain")
    return [dict(zip(keys, r)) for r in rows if r[1] > 0 or r[3] > 0]


def team_discipline(conn, team_id: int) -> dict[str, int]:
    """All-time card totals for a team."""
    rows = dict(conn.execute(
        "SELECT color, COUNT(*) FROM cards WHERE team_id = ? GROUP BY color",
        (team_id,),
    ))
    return {"yellow": rows.get("yellow", 0), "red": rows.get("red", 0)}


def group_fairplay(conn, group_id: int) -> list[tuple[str, int, int]]:
    """(team, yellows, reds) within one group, most-carded first."""
    return conn.execute(
        """
        SELECT t.name,
               SUM(c.color = 'yellow') AS yc,
               SUM(c.color = 'red') AS rc
        FROM cards c
        JOIN matches m ON m.id = c.match_id
        JOIN teams t ON t.id = c.team_id
        WHERE m.group_id = ?
        GROUP BY c.team_id ORDER BY yc + 2 * rc DESC, t.name
        """,
        (group_id,),
    ).fetchall()


def team_cards_by_season(conn, team_id: int) -> dict[str, dict[str, int]]:
    """{season: {yellow, red}} for one team."""
    out: dict[str, dict[str, int]] = {}
    for season, color, n in conn.execute(
        """
        SELECT s.year || '-' || s.half, c.color, COUNT(*)
        FROM cards c
        JOIN matches m ON m.id = c.match_id
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        WHERE c.team_id = ?
        GROUP BY s.id, c.color
        """,
        (team_id,),
    ):
        out.setdefault(season, {"yellow": 0, "red": 0})[color] = n
    return out


def team_top_scorers(conn, team_id: int, limit: int = 5) -> list[tuple[str, int]]:
    return conn.execute(
        """
        SELECT p.name, COUNT(*) AS goals
        FROM goals gl JOIN players p ON p.id = gl.player_id
        WHERE gl.team_id = ? AND gl.own_goal = 0
        GROUP BY p.id ORDER BY goals DESC, p.name LIMIT ?
        """,
        (team_id, limit),
    ).fetchall()


# --- group stats ----------------------------------------------------------------


def position_by_round(conn, group_id: int) -> dict[int, list[str]]:
    """{round: [team names in table order after that round]} — chart data.

    Computed purely from match results (tiebreak: points, goal difference,
    goals for, name). Diverges from published standings when PSMF applied
    administrative point deductions (see point_deductions) or H2H tiebreaks;
    the stored standings remain authoritative for final positions.
    """
    rows = conn.execute(
        """
        SELECT m.round, th.name, ta.name, m.home_goals, m.away_goals
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.group_id = ? AND m.home_goals IS NOT NULL
        ORDER BY m.round
        """,
        (group_id,),
    ).fetchall()
    teams = {n for r in rows for n in (r[1], r[2])}
    stats = {t: {"pts": 0, "gf": 0, "ga": 0} for t in teams}
    tables: dict[int, list[str]] = {}
    max_round = max((r[0] for r in rows), default=0)
    by_round = defaultdict(list)
    for r in rows:
        by_round[r[0]].append(r)
    for rnd in range(1, max_round + 1):
        for _, home, away, hg, ag in by_round.get(rnd, []):
            stats[home]["gf"] += hg
            stats[home]["ga"] += ag
            stats[away]["gf"] += ag
            stats[away]["ga"] += hg
            if hg > ag:
                stats[home]["pts"] += WIN_POINTS
            elif hg < ag:
                stats[away]["pts"] += WIN_POINTS
            else:
                stats[home]["pts"] += 1
                stats[away]["pts"] += 1
        tables[rnd] = sorted(
            teams,
            key=lambda t: (-stats[t]["pts"], -(stats[t]["gf"] - stats[t]["ga"]),
                           -stats[t]["gf"], t),
        )
    return tables


def cross_table(conn, group_id: int) -> dict[str, dict[str, str]]:
    """Křížová tabulka: {home: {away: 'X:Y'}}."""
    grid: dict[str, dict[str, str]] = defaultdict(dict)
    for home, away, hg, ag in conn.execute(
        """
        SELECT th.name, ta.name, m.home_goals, m.away_goals
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.group_id = ? AND m.home_goals IS NOT NULL
        """,
        (group_id,),
    ):
        grid[home][away] = f"{hg}:{ag}"
    return dict(grid)


def scorer_race(conn, group_id: int, top: int = 5) -> dict[int, list[tuple[str, int]]]:
    """{round: [(player, cumulative goals)]} for the group's top scorers."""
    rows = conn.execute(
        """
        SELECT m.round, p.name || ' (' || t.name || ')'
        FROM goals gl
        JOIN matches m ON m.id = gl.match_id
        JOIN players p ON p.id = gl.player_id
        JOIN teams t ON t.id = gl.team_id
        WHERE m.group_id = ? AND gl.own_goal = 0
        """,
        (group_id,),
    ).fetchall()
    totals: dict[str, int] = defaultdict(int)
    for _, player in rows:
        totals[player] += 1
    leaders = sorted(totals, key=totals.get, reverse=True)[:top]
    max_round = max((r[0] for r in rows), default=0)
    race: dict[int, list[tuple[str, int]]] = {}
    cum: dict[str, int] = defaultdict(int)
    by_round = defaultdict(list)
    for rnd, player in rows:
        by_round[rnd].append(player)
    for rnd in range(1, max_round + 1):
        for player in by_round.get(rnd, []):
            cum[player] += 1
        race[rnd] = [(p, cum[p]) for p in leaders]
    return race


def match_points(conn, group_id: int) -> dict[str, int]:
    """Points per team derived purely from match results."""
    pts: dict[str, int] = defaultdict(int)
    for home, away, hg, ag in conn.execute(
        """
        SELECT th.name, ta.name, m.home_goals, m.away_goals
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.group_id = ? AND m.home_goals IS NOT NULL
        """,
        (group_id,),
    ):
        if hg > ag:
            pts[home] += WIN_POINTS
        elif hg < ag:
            pts[away] += WIN_POINTS
        else:
            pts[home] += 1
            pts[away] += 1
    return dict(pts)


def group_stat_charts(conn, group_id: int, top: int = 5) -> dict:
    """Prebuilt chart series for the group Statistiky tab:
    cumulative scorer race (top N), goals per round, home/draw/away split."""
    race = scorer_race(conn, group_id, top)
    rounds = sorted(race)
    leaders = [p for p, _ in race[rounds[-1]]] if rounds else []
    scorer_series = [
        {"player": p, "cum": [dict(race[r]).get(p, 0) for r in rounds]}
        for p in leaders
    ]

    gpr = conn.execute(
        """
        SELECT m.round, SUM(m.home_goals + m.away_goals)
        FROM matches m
        WHERE m.group_id = ? AND m.home_goals IS NOT NULL
        GROUP BY m.round ORDER BY m.round
        """,
        (group_id,),
    ).fetchall()

    home = draw = away = 0
    for hg, ag in conn.execute(
        "SELECT home_goals, away_goals FROM matches "
        "WHERE group_id = ? AND home_goals IS NOT NULL",
        (group_id,),
    ):
        if hg > ag:
            home += 1
        elif hg < ag:
            away += 1
        else:
            draw += 1

    return {
        "scorer_race": {"rounds": rounds, "series": scorer_series},
        "goals_per_round": {"rounds": [r for r, _ in gpr],
                            "goals": [g for _, g in gpr]},
        "home_away": {"home": home, "draw": draw, "away": away},
    }


def point_deductions(conn, group_id: int) -> dict[str, int]:
    """Administrative point deductions: published standings minus points
    derivable from results. PSMF deducts points e.g. for missed piskani
    duty — in 2025-podzim, 26 of 60 groups had at least one deduction.
    Negative values = deduction; {} if standings not stored."""
    published = dict(conn.execute(
        """
        SELECT t.name, st.points FROM standings st
        JOIN teams t ON t.id = st.team_id
        WHERE st.group_id = ? AND st.kind = 'prubezna'
        """,
        (group_id,),
    ))
    derived = match_points(conn, group_id)
    return {
        team: published[team] - derived.get(team, 0)
        for team in published
        if published[team] != derived.get(team, 0)
    }


# --- league-wide -----------------------------------------------------------------


def tier_pyramid(conn, season_slug: str) -> list[dict]:
    return [
        {"tier": tier, "groups": groups, "teams": teams}
        for tier, groups, teams in conn.execute(
            """
            SELECT g.tier, COUNT(DISTINCT g.id), COUNT(gt.team_id)
            FROM groups g
            JOIN seasons s ON s.id = g.season_id
            LEFT JOIN group_teams gt ON gt.group_id = g.id
            WHERE s.year || '-' || s.half = ?
            GROUP BY g.tier ORDER BY g.tier
            """,
            (season_slug,),
        )
    ]


def all_time_scorers(conn, limit: int = 20) -> list[tuple[str, str, int]]:
    """(player, team canonical name, goals). Player identity is name-per-team —
    the site publishes no player IDs, so cross-team careers are not merged."""
    return conn.execute(
        """
        SELECT p.name, t.canonical_name, COUNT(*) AS goals
        FROM goals gl
        JOIN players p ON p.id = gl.player_id
        JOIN teams t ON t.id = gl.team_id
        WHERE gl.own_goal = 0
        GROUP BY p.id ORDER BY goals DESC, p.name LIMIT ?
        """,
        (limit,),
    ).fetchall()


def season_goals_per_round(conn, season_slug: str) -> list[dict]:
    """League-wide scoring trend: [{round, matches, goals, avg}] per round."""
    return [
        {"round": r, "matches": n, "goals": g, "avg": round(g / n, 2)}
        for r, n, g in conn.execute(
            """
            SELECT m.round, COUNT(*), SUM(m.home_goals + m.away_goals)
            FROM matches m
            JOIN groups g ON g.id = m.group_id
            JOIN seasons s ON s.id = g.season_id
            WHERE s.year || '-' || s.half = ? AND m.home_goals IS NOT NULL
            GROUP BY m.round ORDER BY m.round
            """,
            (season_slug,),
        )
    ]


def season_movers(conn, prev_slug: str, season_slug: str) -> list[dict]:
    """Teams whose tier changed between two seasons (postup/sestup arrows).
    [{team, from_tier, to_tier, up}] ordered by new tier."""
    def tiers(slug: str) -> dict[str, int]:
        return dict(conn.execute(
            """
            SELECT t.name, MIN(g.tier)
            FROM standings st
            JOIN teams t ON t.id = st.team_id
            JOIN groups g ON g.id = st.group_id
            JOIN seasons s ON s.id = g.season_id
            WHERE s.year || '-' || s.half = ?
            GROUP BY st.team_id
            """,
            (slug,),
        ))

    before, after = tiers(prev_slug), tiers(season_slug)
    movers = [
        {"team": team, "from_tier": before[team], "to_tier": tier,
         "up": tier < before[team]}
        for team, tier in after.items()
        if team in before and before[team] != tier
    ]
    movers.sort(key=lambda m: (m["to_tier"], m["from_tier"], m["team"]))
    return movers


def _season_standings(conn, season_slug: str) -> list[tuple]:
    """(team, tier, letter, played, gf, ga) from every group of a season,
    preferring vysledna standings where published."""
    return conn.execute(
        """
        SELECT t.name, g.tier, g.letter, st.played, st.gf, st.ga
        FROM standings st
        JOIN groups g ON g.id = st.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams t ON t.id = st.team_id
        WHERE s.year || '-' || s.half = ?
          AND st.kind = CASE WHEN EXISTS (
                SELECT 1 FROM standings x
                WHERE x.group_id = st.group_id AND x.kind = 'vysledna')
              THEN 'vysledna' ELSE 'prubezna' END
        """,
        (season_slug,),
    ).fetchall()


def season_team_boards(conn, season_slug: str, limit: int = 10,
                       min_played: int = 5) -> dict[str, list[dict]]:
    """Best attack (most goals scored) and best defense (fewest conceded)
    across a season; min_played keeps early-season tables honest."""
    rows = [
        {"team": r[0], "group": f"{r[1]}-{r[2].upper()}",
         "group_url": f"/skupina/{season_slug}/{r[1]}-{r[2]}/",
         "played": r[3], "gf": r[4], "ga": r[5]}
        for r in _season_standings(conn, season_slug)
        if r[3] >= min_played
    ]
    return {
        "attack": sorted(rows, key=lambda r: (-r["gf"], r["team"]))[:limit],
        "defense": sorted(rows, key=lambda r: (r["ga"], r["team"]))[:limit],
    }


def season_scorers(conn, season_slug: str, limit: int = 20) -> list[tuple[str, str, int]]:
    """(player, team, goals) within one season."""
    return conn.execute(
        """
        SELECT p.name, t.name, COUNT(*) AS goals
        FROM goals gl
        JOIN matches m ON m.id = gl.match_id
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN players p ON p.id = gl.player_id
        JOIN teams t ON t.id = gl.team_id
        WHERE s.year || '-' || s.half = ? AND gl.own_goal = 0
        GROUP BY p.id ORDER BY goals DESC, p.name LIMIT ?
        """,
        (season_slug, limit),
    ).fetchall()


def season_fairplay(conn, season_slug: str, limit: int = 20,
                    min_played: int = 5) -> list[dict]:
    """Fewest cards per team in one season; card-free teams included.
    Neutral team-level stat only (see CLAUDE.md)."""
    cards = {
        team: (yc, rc)
        for team, yc, rc in conn.execute(
            """
            SELECT t.name,
                   SUM(c.color = 'yellow') AS yc,
                   SUM(c.color = 'red') AS rc
            FROM cards c
            JOIN matches m ON m.id = c.match_id
            JOIN groups g ON g.id = m.group_id
            JOIN seasons s ON s.id = g.season_id
            JOIN teams t ON t.id = c.team_id
            WHERE s.year || '-' || s.half = ?
            GROUP BY c.team_id
            """,
            (season_slug,),
        )
    }
    rows = [
        {"team": r[0], "group": f"{r[1]}-{r[2].upper()}",
         "group_url": f"/skupina/{season_slug}/{r[1]}-{r[2]}/",
         "played": r[3],
         "yellow": cards.get(r[0], (0, 0))[0],
         "red": cards.get(r[0], (0, 0))[1]}
        for r in _season_standings(conn, season_slug)
        if r[3] >= min_played
    ]
    rows.sort(key=lambda r: (r["yellow"] + 2 * r["red"], r["team"]))
    return rows[:limit]


def most_improved(conn, season_a: str, season_b: str, limit: int = 10) -> list[dict]:
    """Teams with the biggest climb from season_a to season_b.
    Score = tier drop * 100 + position gain (tiers matter much more)."""
    def season_table(slug):
        return {
            team: (tier, pos)
            for team, tier, pos in conn.execute(
                """
                SELECT t.canonical_name, g.tier,
                       COALESCE(MAX(CASE WHEN st.kind='vysledna' THEN st.position END),
                                MAX(CASE WHEN st.kind='prubezna' THEN st.position END))
                FROM standings st
                JOIN teams t ON t.id = st.team_id
                JOIN groups g ON g.id = st.group_id
                JOIN seasons s ON s.id = g.season_id
                WHERE s.year || '-' || s.half = ?
                GROUP BY st.team_id
                """,
                (slug,),
            )
        }

    before, after = season_table(season_a), season_table(season_b)
    improvements = []
    for team, (tier_b, pos_b) in after.items():
        if team not in before:
            continue
        tier_a, pos_a = before[team]
        score = (tier_a - tier_b) * 100 + (pos_a - pos_b)
        improvements.append({
            "team": team, "from": f"tier {tier_a} pos {pos_a}",
            "to": f"tier {tier_b} pos {pos_b}", "score": score,
        })
    improvements.sort(key=lambda d: -d["score"])
    return improvements[:limit]


# --- referee / piskani (private analysis, NOT for the public site) -----------------


def referee_match_counts(conn) -> list[tuple[str, int, int]]:
    """(referee name, matches, distinct seasons). matches.referee sometimes
    holds two comma-separated names — each gets credited."""
    counts: dict[str, set] = defaultdict(set)
    for match_id, referee, season in conn.execute(
        """
        SELECT m.id, m.referee, s.year || '-' || s.half
        FROM matches m
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        WHERE m.referee IS NOT NULL AND m.referee != ''
        """,
    ):
        for name in (n.strip() for n in referee.split(",")):
            if name:
                counts[name].add((match_id, season))
    return sorted(
        ((name, len({m for m, _ in v}), len({s for _, s in v}))
         for name, v in counts.items()),
        key=lambda r: -r[1],
    )


def referee_coverage_by_tier(conn) -> list[tuple[str, int, int, int, float]]:
    """(season, tier, matches, matches without referee, missing %)."""
    return [
        (season, tier, total, missing, round(100.0 * missing / total, 1))
        for season, tier, total, missing in conn.execute(
            """
            SELECT s.year || '-' || s.half, g.tier, COUNT(*),
                   SUM(CASE WHEN m.referee IS NULL OR m.referee = '' THEN 1 ELSE 0 END)
            FROM matches m
            JOIN groups g ON g.id = m.group_id
            JOIN seasons s ON s.id = g.season_id
            WHERE m.home_goals IS NOT NULL
            GROUP BY s.id, g.tier ORDER BY s.year, s.half, g.tier
            """,
        )
    ]


def duty_distribution(conn) -> list[tuple[str, str, int]]:
    """(season, team, duty count) from rozpis piskani."""
    return conn.execute(
        """
        SELECT s.year || '-' || s.half, t.canonical_name, COUNT(*)
        FROM referee_duties d
        JOIN groups g ON g.id = d.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams t ON t.id = d.team_id
        GROUP BY s.id, d.team_id ORDER BY s.year, s.half, COUNT(*) DESC
        """,
    ).fetchall()


def export_referee_csvs(conn, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def write(name: str, header: list[str], rows) -> None:
        path = out_dir / name
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        written.append(path)

    write("referee_match_counts.csv",
          ["referee", "matches", "seasons_active"], referee_match_counts(conn))
    write("referee_coverage_by_tier.csv",
          ["season", "tier", "matches", "missing_referee", "missing_pct"],
          referee_coverage_by_tier(conn))
    write("duty_distribution.csv",
          ["season", "team", "duties"], duty_distribution(conn))

    deduction_rows = []
    for group_id, season, tier, letter in conn.execute(
        """
        SELECT g.id, s.year || '-' || s.half, g.tier, g.letter
        FROM groups g JOIN seasons s ON s.id = g.season_id
        """
    ):
        for team, delta in point_deductions(conn, group_id).items():
            deduction_rows.append((season, f"{tier}-{letter}", team, delta))
    write("point_deductions.csv",
          ["season", "group", "team", "points_delta"], deduction_rows)
    return written


# --- CLI -------------------------------------------------------------------------


def print_dossier(conn, name: str, vs: str | None = None) -> None:
    team_id, team_name = find_team(conn, name)
    matches = team_matches(conn, team_id)
    print(f"=== {team_name} ===")
    print(f"played {len(matches)} matches in DB")

    if vs:
        opp_id, opp_name = find_team(conn, vs)
        h2h = head_to_head(conn, team_id, opp_id)
        print(f"\nhead-to-head vs {opp_name}: "
              f"{h2h['W']}W {h2h['D']}D {h2h['L']}L, score {h2h['GF']}:{h2h['GA']}")
        for m in h2h["matches"]:
            print(f"  {m.date} {m.season} [{m.venue}] {m.gf}:{m.ga}")
        return

    print("\nseason history:")
    for h in season_history(conn, team_id):
        marker = "" if h["final"] else " (live)"
        print(f"  {h['season']}: {h['group']}, position {h['position']}{marker}")

    print(f"\nform (last 5, newest first): {form(matches)}")
    longest, current = longest_unbeaten(matches)
    print(f"unbeaten runs: longest {longest}, current {current}")

    print("\nhome/away splits (all seasons):")
    for venue, s in home_away_split(matches).items():
        print(f"  {venue:5s} P{s['P']:3d}  {s['W']}W {s['D']}D {s['L']}L  "
              f"score {s['GF']}:{s['GA']}")

    wins, losses = biggest_results(matches)
    print("\nbiggest wins:")
    for m in wins:
        print(f"  {m.gf}:{m.ga} vs {m.opponent} ({m.season}, {m.venue})")
    print("biggest losses:")
    for m in losses:
        print(f"  {m.gf}:{m.ga} vs {m.opponent} ({m.season}, {m.venue})")

    print("\ntop scorers:")
    for player, goals in team_top_scorers(conn, team_id):
        print(f"  {goals:3d}  {player}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analysis.stats",
        description="Derived stats over the Hanspaulka DB.",
    )
    parser.add_argument("--team", help="print a team dossier")
    parser.add_argument("--vs", help="opponent for head-to-head (with --team)")
    parser.add_argument("--referee-export", type=Path, metavar="DIR",
                        help="write private referee-market CSVs to DIR")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args(argv)

    if not args.team and not args.referee_export:
        parser.error("nothing to do: pass --team and/or --referee-export")
    if not args.db.exists():
        parser.error(f"database not found: {args.db} (run the scraper first)")

    conn = connect(args.db)
    try:
        if args.team:
            try:
                print_dossier(conn, args.team, vs=args.vs)
            except LookupError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        if args.referee_export:
            for path in export_referee_csvs(conn, args.referee_export):
                print(f"wrote {path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
