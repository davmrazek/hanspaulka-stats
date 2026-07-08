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


def spark_svg(matches: list[dict]) -> str:
    """Inline SVG sparkline: one goal-difference bar per match (chronological),
    W/D/L colored via .s-* classes. Server-rendered — zero JS on the client."""
    if not matches:
        return ""
    from html import escape
    mid, bars = 12, []
    for i, m in enumerate(matches):
        d = m["gf"] - m["ga"]
        cls = "s-W" if d > 0 else ("s-D" if d == 0 else "s-L")
        h = min(abs(d), 5) * 2 or 2
        y = mid - h if d > 0 else (mid - 1 if d == 0 else mid)
        title = escape(f"{m['date']} {m['opponent']} {m['gf']}:{m['ga']}")
        bars.append(
            f'<rect class="{cls}" x="{i * 8 + 1}" y="{y}" width="6" height="{h}">'
            f"<title>{title}</title></rect>"
        )
    w = len(matches) * 8
    return (
        f'<svg class="spark" width="{w}" height="24" viewBox="0 0 {w} 24" '
        f'role="img" aria-label="Posledních {len(matches)} zápasů">'
        f'<line x1="0" y1="{mid}" x2="{w}" y2="{mid}"/>{"".join(bars)}</svg>'
    )


def _recap_text(h: dict | None) -> str:
    """Plain-text round recap for copy-paste into social posts (Phase 5)."""
    if not h:
        return "Právě probíhá přestávka mezi sezónami.\n"
    b, m = h["biggest"], h["most"]
    lines = [
        f"⚽ Hanspaulka — {h['season']}, {h['round']}. kolo ({h['matches']} zápasů)",
        "",
        f"Nejvyšší výhra: {b['home']} {b['hg']}:{b['ag']} {b['away']} ({b['group']})",
        f"Nejvíc gólů: {m['home']} {m['hg']}:{m['ag']} {m['away']} ({m['group']})",
    ]
    if h["scorers"]:
        tops = ", ".join(f"{n} ({t}) {g}" for n, t, g in h["scorers"])
        lines.append(f"Střelci kola: {tops}")
    return "\n".join(lines) + "\n"


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
    """Groups that actually have data — cancelled seasons (COVID 2020-jaro)
    leave empty group rows behind; those get no pages."""
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
            WHERE EXISTS (SELECT 1 FROM standings st WHERE st.group_id = g.id)
               OR EXISTS (SELECT 1 FROM matches m WHERE m.group_id = g.id)
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
    played = [r for r in matches if r[4] is not None]
    headline = max(
        played, key=lambda r: (abs(r[4] - r[5]), r[4] + r[5]), default=None
    )
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
    position_chart = {
        "rounds": sorted(positions),
        "series": [
            {"team": t, "positions": [positions[r].index(t) + 1 for r in sorted(positions)]}
            for t in team_order
        ],
    }
    charts = {"position": position_chart, **stats.group_stat_charts(conn, group["id"])}

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
        "headline": (
            {"home": headline[2], "away": headline[3],
             "score": f"{headline[4]}:{headline[5]}"}
            if headline else None
        ),
        "rounds": dict(sorted(rounds.items())),
        "cross": stats.cross_table(conn, group["id"]),
        "cross_teams": [r["team"] for r in table],
        "charts": charts,
        "scorers": scorers,
        "fairplay": stats.group_fairplay(conn, group["id"]),
        "deductions": stats.point_deductions(conn, group["id"]),
    }


def team_context(conn, team: dict, groups_by_id: dict, teams_by_name: dict) -> dict:
    matches = stats.team_matches(conn, team["id"])
    history = stats.season_history(conn, team["id"])
    wins, losses = stats.biggest_results(matches)
    longest, current = stats.longest_unbeaten(matches)
    goals_by_season: dict[str, list[int]] = defaultdict(list)
    conceded_by_season: dict[str, list[int]] = defaultdict(list)
    for m in matches:
        goals_by_season[m.season].append(m.gf)
        conceded_by_season[m.season].append(m.ga)
    cards_by_season = stats.team_cards_by_season(conn, team["id"])

    def per_game(bucket: dict[str, list[int]], season: str) -> float:
        vals = bucket[season]
        return round(sum(vals) / len(vals), 2) if vals else 0

    trend = {
        "seasons": [h["season"] for h in history],
        "avg_goals": [per_game(goals_by_season, h["season"]) for h in history],
        "avg_conceded": [per_game(conceded_by_season, h["season"]) for h in history],
        "yellow": [cards_by_season.get(h["season"], {}).get("yellow", 0)
                   for h in history],
        "red": [cards_by_season.get(h["season"], {}).get("red", 0)
                for h in history],
        "tier": [h["tier"] for h in history],
    }
    career = stats.build_career(matches, history)
    opponents = stats.build_opponent_aggregates(matches)
    for o in opponents:
        opp = teams_by_name.get(o["opponent"], {})
        o["url"] = opp.get("url")
        o["slug"] = opp.get("slug")
    def match_row(m):
        return {**m.__dict__, "outcome": m.outcome,
                "opponent_url": teams_by_name.get(m.opponent, {}).get("url")}

    by_season: dict[str, list] = defaultdict(list)
    for m in matches:
        by_season[m.season].append(m)
    matches_by_season = [
        (season, [match_row(m) for m in reversed(ms)])
        for season, ms in sorted(
            by_season.items(),
            key=lambda kv: stats.season_sort_key(
                int(kv[0].split("-")[0]), kv[0].split("-")[1]),
            reverse=True,
        )
    ]

    recent = [match_row(m) for m in matches[-10:]][::-1]
    return {
        "team": team,
        "matches": matches,
        "matches_by_season": matches_by_season,
        "recent": recent,
        "spark": spark_svg(recent[::-1]),
        "history": history,
        "form": stats.form(matches),
        "split": stats.home_away_split(matches),
        "biggest_wins": wins,
        "biggest_losses": losses,
        "unbeaten": {"longest": longest, "current": current},
        "scorers": stats.team_top_scorers(conn, team["id"], limit=10),
        "roster": stats.team_roster(conn, team["id"]),
        "discipline": stats.team_discipline(conn, team["id"]),
        "career": career,
        "opponents": opponents,
        "trend": trend,
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

    fairplay = stats.all_time_fairplay(conn, limit=20)
    for r in fairplay:
        r["url"] = teams_by_name.get(r["team"], {}).get("url")
    loyalty = [
        (name, seasons, teams_by_name.get(name, {}).get("url"))
        for name, seasons in stats.most_seasons(conn, limit=20)
    ]
    return {
        "scorers": scorer_board,
        "biggest": biggest,
        "most_goals": most_goals,
        "unbeaten": runs[:20],
        "fairplay": fairplay,
        "loyalty": loyalty,
        "goalkeepers": stats.top_goalkeepers(conn, limit=20),
    }


# --- build -----------------------------------------------------------------------


def build(db_path: Path = DB_PATH, out: Path = DEFAULT_OUT, base_url: str = "") -> int:
    """Render the site atomically. On any failure the half-built staging dir is
    removed and the existing `out` is left untouched."""
    staging = out.parent / f".{out.name}.staging"
    try:
        return _build(db_path, out, base_url)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build(db_path: Path = DB_PATH, out: Path = DEFAULT_OUT, base_url: str = "") -> int:
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

    # Build into a fresh staging dir and swap it in at the very end, so a
    # failed or overlapping build never leaves `out` half-written. `out` is
    # rebound to staging for the whole render; `dest` is the final location.
    dest = out
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = dest.parent / f".{dest.name}.staging"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    if (dest / "CNAME").exists():
        shutil.copy(dest / "CNAME", out / "CNAME")  # deploy artifact, preserve

    groups = load_groups(conn)
    populated = {g["season_slug"] for g in groups}
    seasons = [s for s in load_seasons(conn) if s["slug"] in populated]
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
    liga_dnes = stats.latest_round_highlights(conn, latest_season)
    render(
        "index.html", "/",
        pyramid=stats.tier_pyramid(conn, latest_season),
        latest_season=latest_season,
        seasons=seasons[::-1],
        season_groups=season_groups,
        totals=totals,
        liga_dnes=liga_dnes,
        title=f"{SITE_NAME} — statistiky Hanspaulské ligy",
        description="Tabulky, střelci a historie týmů Hanspaulské ligy — "
                    "statistiky, které jinde nenajdete.",
    )
    (out / "recap.txt").write_text(_recap_text(liga_dnes), encoding="utf-8")

    for g in groups:
        ctx = group_context(conn, g, teams_by_name)
        render(
            "group.html", g["url"],
            **ctx,
            title=f"Skupina {g['name']} {g['season_slug']} — {SITE_NAME}",
            description=f"Výsledky, tabulka a střelci skupiny {g['name']} "
                        f"Hanspaulské ligy {g['season_slug']}.",
        )
        (out / g["url"].lstrip("/") / "data.json").write_text(
            json.dumps({
                "label": f"{g['season_slug']} {g['name']}",
                "url": f"{base_url}{g['url']}",
                "table": [
                    {"position": r["position"], "team": r["team"],
                     "played": r["played"], "points": r["points"],
                     "gf": r["gf"], "ga": r["ga"]}
                    for r in ctx["table"]
                ],
                "scorers": [
                    {"name": n, "team": t2, "goals": goals}
                    for n, t2, goals in ctx["scorers"]
                ],
                "fairplay": [
                    {"team": t2, "yellow": yc, "red": rc}
                    for t2, yc, rc in ctx["fairplay"]
                ],
                "charts": ctx["charts"],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    for t in teams:
        ctx = team_context(conn, t, groups_by_id, teams_by_name)
        render(
            "team.html", t["url"],
            **ctx,
            title=f"{t['name']} — statistiky týmu — {SITE_NAME}",
            description=f"Historie, forma a výsledky týmu {t['name']} "
                        f"v Hanspaulské lize.",
        )
        # compact per-team JSON consumed by the customizable homepage
        (out / t["url"].lstrip("/") / "data.json").write_text(
            json.dumps({
                "name": t["name"],
                "url": f"{base_url}{t['url']}",
                "form": ctx["form"],
                "spark": ctx["spark"],
                "history": ctx["history"],
                "trend": json.loads(ctx["trend_json"]),
                "recent": [
                    {"date": m["date"], "opponent": m["opponent"],
                     "venue": m["venue"], "gf": m["gf"], "ga": m["ga"],
                     "outcome": m["outcome"]}
                    for m in ctx["recent"][:5]
                ],
                "split": ctx["split"],
                "unbeaten": ctx["unbeaten"],
                "discipline": ctx["discipline"],
                "opponents": ctx["opponents"],
                "roster": ctx["roster"][:8],
                "biggest_win": ({"opponent": w.opponent, "gf": w.gf, "ga": w.ga,
                                 "season": w.season}
                                if (w := (ctx["biggest_wins"][0] if ctx["biggest_wins"] else None))
                                else None),
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    render(
        "records.html", "/rekordy/",
        **records_context(conn, teams_by_name),
        seasons=seasons[::-1],
        teams_by_name=teams_by_name,
        title=f"Rekordy všech dob — {SITE_NAME}",
        description="Nejlepší střelci, nejdelší série bez porážky a "
                    "nejvyšší výhry v historii Hanspaulské ligy.",
    )

    render(
        "srovnani.html", "/srovnani/",
        title=f"Srovnání týmů — {SITE_NAME}",
        description="Porovnejte dva týmy Hanspaulské ligy vedle sebe — forma, "
                    "bilance, vzájemné zápasy a vývoj napříč sezónami.",
    )

    # season hubs + per-season records (seasons is chronological)
    prev = None
    for s in seasons:
        movers = stats.season_movers(conn, prev["slug"], s["slug"]) if prev else []
        for mv in movers:
            mv["url"] = teams_by_name.get(mv["team"], {}).get("url")
        gpr = stats.season_goals_per_round(conn, s["slug"])
        chart = {"rounds": [r["round"] for r in gpr],
                 "avg": [r["avg"] for r in gpr]}
        render(
            "season.html", f"/sezona/{s['slug']}/",
            season=s,
            groups=season_groups[s["slug"]],
            pyramid=stats.tier_pyramid(conn, s["slug"]),
            movers=movers,
            chart=chart,
            chart_json=json.dumps(chart, ensure_ascii=False),
            title=f"Sezóna {s['slug']} — {SITE_NAME}",
            description=f"Skupiny, postupy a sestupy a rekordy sezóny "
                        f"{s['slug']} Hanspaulské ligy.",
        )
        render(
            "season_records.html", f"/rekordy/{s['slug']}/",
            season=s,
            scorers=stats.season_scorers(conn, s["slug"]),
            boards=stats.season_team_boards(conn, s["slug"]),
            fairplay=stats.season_fairplay(conn, s["slug"]),
            teams_by_name=teams_by_name,
            title=f"Rekordy sezóny {s['slug']} — {SITE_NAME}",
            description=f"Nejlepší střelci, útok, obrana a fair play "
                        f"sezóny {s['slug']} Hanspaulské ligy.",
        )
        prev = s

    # search index + static assets
    (out / "teams.json").write_text(
        json.dumps(
            [{"n": t["name"], "u": f"{base_url}{t['url']}"} for t in teams],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (out / "groups.json").write_text(
        json.dumps(
            [
                {"l": f"{g['season_slug']} {g['name']}", "u": f"{base_url}{g['url']}"}
                for g in sorted(
                    groups,
                    key=lambda g: (g["season_slug"] != latest_season,
                                   g["season_slug"], g["tier"], g["letter"]),
                )
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # players.json: name-per-team identity (same name in two teams = two rows,
    # see CLAUDE.md). Team name+slug are normalised into a shared `teams` array
    # and referenced by index, keeping the ~25k-player index small over the
    # wire. Each player links to the team's Hráči tab.
    team_ref = {t["id"]: i for i, t in enumerate(teams)}
    (out / "players.json").write_text(
        json.dumps(
            {
                "teams": [[t["name"], t["slug"]] for t in teams],
                "players": [
                    [name, team_ref[team_id]]
                    for name, team_id in conn.execute(
                        "SELECT name, team_id FROM players ORDER BY name")
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for asset in ("style.css", "app.js", "home.js", "tabs.js", "sort.js", "srovnani.js"):
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

    # swap staging into place: move the old tree aside, rename staging, then
    # drop the old tree. `out` is only ever a complete build.
    old = dest.parent / f".{dest.name}.old"
    if old.exists():
        shutil.rmtree(old)
    if dest.exists():
        dest.rename(old)
    out.rename(dest)
    if old.exists():
        shutil.rmtree(old)

    print(f"built {len(pages)} pages -> {dest}")
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
