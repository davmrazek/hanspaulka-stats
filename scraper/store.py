"""Dataclasses -> SQLite (data/hanspaulka.db).

Schema follows CLAUDE.md with extensions (extend, don't rename):
- matches: + gameid, time, pitch, commentary
- goals/cards: + seq (event order within a match) so re-ingesting a match
  updates rows in place instead of growing the table — UNIQUE(match_id, seq)
- goals: + own_goal flag; player_id is NULL for own goals (scorer unpublished)
- appearances: lineups from match detail (not in the reference schema, but
  parsed per PLAN.md Phase 1 and needed for roster stats later)
- referee_duties: times/pitch columns instead of free-text note; round is
  NULL for now (rozpis piskani has no round column on the site)

Everything is idempotent: re-running the scraper on cached data must always
produce the same database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scraper.parse import MatchResult, RefereeDuty, StandingRow

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hanspaulka.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons(
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    half TEXT NOT NULL CHECK(half IN ('jaro','podzim')),
    slug TEXT NOT NULL,
    UNIQUE(year, half)
);
CREATE TABLE IF NOT EXISTS groups(
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id),
    tier INTEGER NOT NULL,
    letter TEXT NOT NULL,
    url TEXT NOT NULL,
    UNIQUE(season_id, tier, letter)
);
CREATE TABLE IF NOT EXISTS teams(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_teams(
    group_id INTEGER NOT NULL REFERENCES groups(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    UNIQUE(group_id, team_id)
);
CREATE TABLE IF NOT EXISTS matches(
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    round INTEGER NOT NULL,
    date TEXT,
    time TEXT,
    pitch TEXT,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_goals INTEGER,
    away_goals INTEGER,
    ht_home INTEGER,
    ht_away INTEGER,
    referee TEXT,
    commentary TEXT,
    detail_url TEXT,
    gameid INTEGER,
    UNIQUE(group_id, round, home_team_id, away_team_id)
);
CREATE TABLE IF NOT EXISTS players(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    UNIQUE(name, team_id)
);
CREATE TABLE IF NOT EXISTS goals(
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    seq INTEGER NOT NULL,
    player_id INTEGER REFERENCES players(id),
    minute INTEGER,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    own_goal INTEGER NOT NULL DEFAULT 0,
    UNIQUE(match_id, seq)
);
CREATE TABLE IF NOT EXISTS cards(
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    seq INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id),
    color TEXT NOT NULL CHECK(color IN ('yellow','red')),
    minute INTEGER,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    UNIQUE(match_id, seq)
);
CREATE TABLE IF NOT EXISTS appearances(
    match_id INTEGER NOT NULL REFERENCES matches(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    role TEXT NOT NULL CHECK(role IN ('goalkeeper','field')),
    is_captain INTEGER NOT NULL DEFAULT 0,
    is_best INTEGER NOT NULL DEFAULT 0,
    UNIQUE(match_id, player_id)
);
CREATE TABLE IF NOT EXISTS referee_duties(
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    round INTEGER,
    date TEXT NOT NULL,
    times TEXT NOT NULL,
    pitch TEXT NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    note TEXT,
    UNIQUE(group_id, date, pitch, team_id)
);
CREATE TABLE IF NOT EXISTS standings(
    group_id INTEGER NOT NULL REFERENCES groups(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    kind TEXT NOT NULL CHECK(kind IN ('prubezna','vysledna')),
    position INTEGER NOT NULL,
    played INTEGER NOT NULL,
    won INTEGER NOT NULL,
    drawn INTEGER NOT NULL,
    lost INTEGER NOT NULL,
    gf INTEGER NOT NULL,
    ga INTEGER NOT NULL,
    points INTEGER NOT NULL,
    UNIQUE(group_id, team_id, kind)
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _get_or_create(conn, table: str, unique: dict, extra: dict | None = None) -> int:
    cols = {**unique, **(extra or {})}
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT OR IGNORE INTO {table}({names}) VALUES({placeholders})",
        tuple(cols.values()),
    )
    where = " AND ".join(f"{k} = ?" for k in unique)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {where}", tuple(unique.values())
    ).fetchone()
    return row[0]


def upsert_season(conn, year: int, half: str, slug: str) -> int:
    return _get_or_create(conn, "seasons", {"year": year, "half": half}, {"slug": slug})


def upsert_group(conn, season_id: int, tier: int, letter: str, url: str) -> int:
    return _get_or_create(
        conn, "groups", {"season_id": season_id, "tier": tier, "letter": letter},
        {"url": url},
    )


def upsert_team(conn, name: str) -> int:
    # canonical_name == name until Phase 2 canonicalization
    return _get_or_create(conn, "teams", {"name": name}, {"canonical_name": name})


def upsert_player(conn, name: str, team_id: int) -> int:
    return _get_or_create(conn, "players", {"name": name, "team_id": team_id})


def link_group_team(conn, group_id: int, team_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO group_teams(group_id, team_id) VALUES(?, ?)",
        (group_id, team_id),
    )


def upsert_match(conn, group_id: int, m: MatchResult, detail_url: str | None = None) -> int:
    home_id = upsert_team(conn, m.home_team)
    away_id = upsert_team(conn, m.away_team)
    link_group_team(conn, group_id, home_id)
    link_group_team(conn, group_id, away_id)

    conn.execute(
        """
        INSERT INTO matches(group_id, round, date, time, pitch,
                            home_team_id, away_team_id, home_goals, away_goals,
                            ht_home, ht_away, referee, commentary, detail_url, gameid)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(group_id, round, home_team_id, away_team_id) DO UPDATE SET
            date=excluded.date, time=excluded.time, pitch=excluded.pitch,
            home_goals=excluded.home_goals, away_goals=excluded.away_goals,
            ht_home=excluded.ht_home, ht_away=excluded.ht_away,
            referee=excluded.referee, commentary=excluded.commentary,
            detail_url=excluded.detail_url, gameid=excluded.gameid
        """,
        (group_id, m.round, m.date, m.time, m.pitch, home_id, away_id,
         m.home_goals, m.away_goals, m.ht_home, m.ht_away,
         m.referee, m.commentary, detail_url, m.gameid),
    )
    match_id = conn.execute(
        "SELECT id FROM matches WHERE group_id=? AND round=? AND home_team_id=? AND away_team_id=?",
        (group_id, m.round, home_id, away_id),
    ).fetchone()[0]

    _store_events(conn, match_id, m, home_id, away_id)
    _store_lineups(conn, match_id, m, home_id, away_id)
    return match_id


def _side_team(side: str, home_id: int, away_id: int) -> int:
    return home_id if side == "home" else away_id


def _store_events(conn, match_id: int, m: MatchResult, home_id: int, away_id: int) -> None:
    for seq, g in enumerate(m.goals):
        team_id = _side_team(g.side, home_id, away_id)
        player_id = upsert_player(conn, g.player, team_id) if g.player else None
        conn.execute(
            """
            INSERT INTO goals(match_id, seq, player_id, minute, team_id, own_goal)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id, seq) DO UPDATE SET
                player_id=excluded.player_id, minute=excluded.minute,
                team_id=excluded.team_id, own_goal=excluded.own_goal
            """,
            (match_id, seq, player_id, g.minute, team_id, int(g.own_goal)),
        )
    conn.execute(
        "DELETE FROM goals WHERE match_id=? AND seq>=?", (match_id, len(m.goals))
    )

    for seq, c in enumerate(m.cards):
        team_id = _side_team(c.side, home_id, away_id)
        player_id = upsert_player(conn, c.player, team_id)
        conn.execute(
            """
            INSERT INTO cards(match_id, seq, player_id, color, minute, team_id)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id, seq) DO UPDATE SET
                player_id=excluded.player_id, color=excluded.color,
                minute=excluded.minute, team_id=excluded.team_id
            """,
            (match_id, seq, player_id, c.color, c.minute, team_id),
        )
    conn.execute(
        "DELETE FROM cards WHERE match_id=? AND seq>=?", (match_id, len(m.cards))
    )


def _store_lineups(conn, match_id: int, m: MatchResult, home_id: int, away_id: int) -> None:
    conn.execute("DELETE FROM appearances WHERE match_id=?", (match_id,))
    for lineup, team_id in ((m.home_lineup, home_id), (m.away_lineup, away_id)):
        if lineup is None:
            continue
        entries = []
        if lineup.goalkeeper:
            entries.append((lineup.goalkeeper, "goalkeeper"))
        entries += [(p, "field") for p in lineup.players]
        for name, role in entries:
            player_id = upsert_player(conn, name, team_id)
            conn.execute(
                """
                INSERT OR REPLACE INTO appearances
                    (match_id, player_id, team_id, role, is_captain, is_best)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (match_id, player_id, team_id, role,
                 int(name == lineup.captain), int(name == lineup.best)),
            )


def store_standings(conn, group_id: int, kind: str, rows: tuple[StandingRow, ...]) -> None:
    for r in rows:
        team_id = upsert_team(conn, r.team)
        link_group_team(conn, group_id, team_id)
        conn.execute(
            """
            INSERT INTO standings(group_id, team_id, kind, position, played,
                                  won, drawn, lost, gf, ga, points)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, team_id, kind) DO UPDATE SET
                position=excluded.position, played=excluded.played,
                won=excluded.won, drawn=excluded.drawn, lost=excluded.lost,
                gf=excluded.gf, ga=excluded.ga, points=excluded.points
            """,
            (group_id, team_id, kind, r.position, r.played,
             r.won, r.drawn, r.lost, r.gf, r.ga, r.points),
        )


def store_duties(conn, group_id: int, duties: tuple[RefereeDuty, ...]) -> None:
    for d in duties:
        team_id = upsert_team(conn, d.team)
        conn.execute(
            """
            INSERT INTO referee_duties(group_id, date, times, pitch, team_id)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(group_id, date, pitch, team_id) DO UPDATE SET
                times=excluded.times
            """,
            (group_id, d.date, d.times, d.pitch, team_id),
        )
