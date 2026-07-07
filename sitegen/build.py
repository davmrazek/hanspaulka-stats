"""Render the static site: SQLite -> Jinja2 -> site/.

Usage:
  python -m sitegen.build [--db data/hanspaulka.db] [--out site] [--base-url ""]

--base-url is the path prefix for absolute links ("" locally,
"/hanspaulka-stats" on GitHub Pages project sites).

Pages: index, one per group, one per team (canonical), all-time records,
sitemap.xml, teams.json search index. UI text is Czech (see CLAUDE.md);
referee stats and player-negative stats stay out of the public site.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from analysis import stats
from scraper.store import DB_PATH, connect

TEMPLATES = Path(__file__).resolve().parent / "templates"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "site"
SITE_NAME = "Hanspaulka Stats"


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return text or "tym"


# --- data assembly -----------------------------------------------------------


def load_seasons(conn) -> list[dict]:
    rows = conn.execute("SELECT id, year, half, slug FROM seasons").fetchall()
    rows.sort(key=lambda r: stats.season_sort_key(r[1], r[2]))
    return [{"id": r[0], "year": r[1], "half": r[2], "slug": r[3]} for r in rows]


def load_groups(conn) -> list[dict]:
    return [
        {
            "id": r[0], "season_slug": r[1], "tier": r[2], "letter": r[3],
            "name": f"{r[2]}{r[3].upper()}",
            "url": f"/skupina/{r[1]}/{r[2]}-{r[3]}/",
        }
        for r in conn.execute(
            """
            SELECT g.id, s.slug, g.tier, g.letter
            FROM groups g JOIN seasons s ON s.id = g.season_id
            """
        )
    ]


def load_teams(conn) -> list[dict]:
    """Teams keyed by canonical name; slug collisions get numeric suffixes."""
    rows = conn.execute(
        "SELECT id, name, canonical_name FROM teams ORDER BY id"
    ).fetchall()
    taken: dict[str, int] = {}
    teams = []
    for team_id, name, canonical in rows:
        slug = slugify(canonical)
        if slug in taken:
            taken[slug] += 1
            slug = f"{slug}-{taken[slug]}"
        else:
            taken[slug] = 1
        teams.append({
            "id": team_id, "name": name, "canonical": canonical,
            "slug": slug, "url": f"/tym/{slug}/",
        })
    return teams


def group_context(conn, group: dict, teams_by_name: dict) -> dict:
    standings = conn.execute(
        """
        SELECT st.kind, st.position, t.name, st.played, st.won, st.drawn,
               st.lost, st.gf, st.ga, st.points
        FROM standings st JOIN teams t ON t.id = st.team_id
        WHERE st.group_id = ? ORDER BY st.position
        """,
        (group["id"],),
    ).fetchall()
    kinds = {r[0] for r in standings}
    kind = "vysledna" if "vysledna" in kinds else "prubezna"
    table = [
        {
            "position": r[1], "team": r[2], "played": r[3], "won": r[4],
            "drawn": r[5], "lost": r[6], "gf": r[7], "ga": r[8], "points": r[9],
            "team_url": teams_by_name.get(r[2], {}).get("url"),
        }
        for r in standings if r[0] == kind
    ]

    matches = conn.execute(
        """
        SELECT m.round, m.date, th.name, ta.name, m.home_goals, m.away_goals,
               m.ht_home, m.ht_away
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.group_id = ? ORDER BY m.round, m.date
        """,
        (group["id"],),
    ).fetchall()
    rounds: dict[int, list] = defaultdict(list)
    for r in matches:
        rounds[r[0]].append({
            "date": r[1], "home": r[2], "away": r[3],
            "home_url": teams_by_name.get(r[2], {}).get("url"),
            "away_url": teams_by_name.get(r[3], {}).get("url"),
            "score": f"{r[4]}:{r[5]}" if r[4] is not None else "–",
            "ht": f"({r[6]}:{r[7]})" if r[6] is not None else "",
        })

    positions = stats.position_by_round(conn, group["id"])
    team_order = positions[max(positions)] if positions else []
    chart = {
        "rounds": sorted(positions),
        "series": [
            {"team": t, "positions": [positions[r].index(t) + 1 for r in sorted(positions)]}
            for t in team_order
        ],
    }

    scorers = conn.execute(
        """
        SELECT p.name, t.name, COUNT(*) AS goals
        FROM goals gl
        JOIN matches m ON m.id = gl.match_id
        JOIN players p ON p.id = gl.player_id
        JOIN teams t ON t.id = gl.team_id
        WHERE m.group_id = ? AND gl.own_goal = 0
        GROUP BY p.id ORDER BY goals DESC, p.name LIMIT 10
        """,
        (group["id"],),
    ).fetchall()

    return {
        "group": group,
        "kind": kind,
        "table": table,
        "rounds": dict(sorted(rounds.items())),
        "cross": stats.cross_table(conn, group["id"]),
        "cross_teams": [r["team"] for r in table],
        "chart_json": json.dumps(chart, ensure_ascii=False),
        "scorers": scorers,
        "deductions": stats.point_deductions(conn, group["id"]),
    }


def team_context(conn, team: dict, groups_by_id: dict, teams_by_name: dict) -> dict:
    matches = stats.team_matches(conn, team["id"])
    history = stats.season_history(conn, team["id"])
    wins, losses = stats.biggest_results(matches)
    longest, current = stats.longest_unbeaten(matches)
    goals_by_season: dict[str, list[int]] = defaultdict(list)
    for m in matches:
        goals_by_season[m.season].append(m.gf)
    trend = {
        "seasons": [h["season"] for h in history],
        "avg_goals": [
            round(sum(goals_by_season[h["season"]]) / len(goals_by_season[h["season"]]), 2)
            if goals_by_season[h["season"]] else 0
            for h in history
        ],
    }
    return {
        "team": team,
        "matches": matches,
        "recent": [
            {**m.__dict__, "outcome": m.outcome,
             "opponent_url": teams_by_name.get(m.opponent, {}).get("url")}
            for m in matches[-10:]
        ][::-1],
        "history": history,
        "form": stats.form(matches),
        "split": stats.home_away_split(matches),
        "biggest_wins": wins,
        "biggest_losses": losses,
        "unbeaten": {"longest": longest, "current": current},
        "scorers": stats.team_top_scorers(conn, team["id"], limit=10),
        "trend_json": json.dumps(trend, ensure_ascii=False),
    }


def records_context(conn, teams_by_name: dict) -> dict:
    scorer_board = stats.all_time_scorers(conn, limit=50)

    biggest = conn.execute(
        """
        SELECT s.slug, g.tier, g.letter, th.name, ta.name,
               m.home_goals, m.away_goals
        FROM matches m
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.home_goals IS NOT NULL
        ORDER BY ABS(m.home_goals - m.away_goals) DESC,
                 m.home_goals + m.away_goals DESC
        LIMIT 10
        """
    ).fetchall()

    most_goals = conn.execute(
        """
        SELECT s.slug, g.tier, g.letter, th.name, ta.name,
               m.home_goals, m.away_goals
        FROM matches m
        JOIN groups g ON g.id = m.group_id
        JOIN seasons s ON s.id = g.season_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.home_goals IS NOT NULL
        ORDER BY m.home_goals + m.away_goals DESC LIMIT 10
        """
    ).fetchall()

    # longest unbeaten runs league-wide, one pass over all matches
    runs = []
    for team_id, name in conn.execute("SELECT id, name FROM teams"):
        matches = stats.team_matches(conn, team_id)
        if len(matches) >= 10:
            longest, _ = stats.longest_unbeaten(matches)
            runs.append((name, longest, teams_by_name.get(name, {}).get("url")))
    runs.sort(key=lambda r: -r[1])

    return {
        "scorers": scorer_board,
        "biggest": biggest,
        "most_goals": most_goals,
        "unbeaten": runs[:20],
    }


# --- build -----------------------------------------------------------------------


def build(db_path: Path = DB_PATH, out: Path = DEFAULT_OUT, base_url: str = "") -> int:
    conn = connect(db_path)
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update({
        "site_name": SITE_NAME,
        "base": base_url.rstrip("/"),
        "built_at": dt.date.today().isoformat(),
    })

    if out.exists():
        for child in out.iterdir():
            if child.name == "CNAME":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)

    seasons = load_seasons(conn)
    groups = load_groups(conn)
    teams = load_teams(conn)
    teams_by_name = {t["name"]: t for t in teams}
    groups_by_id = {g["id"]: g for g in groups}
    latest_season = seasons[-1]["slug"]

    pages: list[str] = []

    def render(template: str, url: str, **ctx) -> None:
        path = out / url.lstrip("/") / "index.html" if url != "/" else out / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**ctx), encoding="utf-8")
        pages.append(url)

    # home
    season_groups = defaultdict(list)
    for g in groups:
        season_groups[g["season_slug"]].append(g)
    for gs in season_groups.values():
        gs.sort(key=lambda g: (g["tier"], g["letter"]))
    totals = {
        "matches": conn.execute("SELECT COUNT(*) FROM matches WHERE home_goals IS NOT NULL").fetchone()[0],
        "goals": conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0],
        "teams": len(teams),
        "seasons": len(seasons),
    }
    render(
        "index.html", "/",
        pyramid=stats.tier_pyramid(conn, latest_season),
        latest_season=latest_season,
        seasons=seasons[::-1],
        season_groups=season_groups,
        totals=totals,
        title=f"{SITE_NAME} — statistiky Hanspaulské ligy",
        description="Tabulky, střelci a historie týmů Hanspaulské ligy — "
                    "statistiky, které jinde nenajdete.",
    )

    for g in groups:
        render(
            "group.html", g["url"],
            **group_context(conn, g, teams_by_name),
            title=f"Skupina {g['name']} {g['season_slug']} — {SITE_NAME}",
            description=f"Výsledky, tabulka a střelci skupiny {g['name']} "
                        f"Hanspaulské ligy {g['season_slug']}.",
        )

    for t in teams:
        render(
            "team.html", t["url"],
            **team_context(conn, t, groups_by_id, teams_by_name),
            title=f"{t['name']} — statistiky týmu — {SITE_NAME}",
            description=f"Historie, forma a výsledky týmu {t['name']} "
                        f"v Hanspaulské lize.",
        )

    render(
        "records.html", "/rekordy/",
        **records_context(conn, teams_by_name),
        title=f"Rekordy všech dob — {SITE_NAME}",
        description="Nejlepší střelci, nejdelší série bez porážky a "
                    "nejvyšší výhry v historii Hanspaulské ligy.",
    )

    # search index + static assets
    (out / "teams.json").write_text(
        json.dumps(
            [{"n": t["name"], "u": f"{base_url}{t['url']}"} for t in teams],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for asset in ("style.css", "app.js"):
        shutil.copy(TEMPLATES / asset, out / asset)

    # sitemap (absolute URLs need a real origin; base_url may be a bare path)
    origin = base_url if base_url.startswith("http") else ""
    sitemap = "\n".join(
        f"<url><loc>{origin or base_url}{p}</loc></url>" for p in pages
    )
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{sitemap}\n</urlset>\n",
        encoding="utf-8",
    )

    conn.close()
    print(f"built {len(pages)} pages -> {out}")
    return len(pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sitegen.build")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-url", default="",
                        help='path prefix, e.g. "/hanspaulka-stats" on GitHub Pages')
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 1
    build(args.db, args.out, args.base_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
