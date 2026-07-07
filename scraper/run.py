"""CLI entry point: discover -> fetch -> parse -> store.

Usage:
  python -m scraper.run --season 2025-podzim --group 6-a   # one group
  python -m scraper.run --season 2025-podzim               # whole season
  python -m scraper.run --backfill 5                       # last 5 seasons
  python -m scraper.run --current                          # current season, re-fetch
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from scraper import fetch, parse, store

BASE = "https://www.psmf.cz"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scraper.run",
        description="Scrape Hanspaulska liga results from psmf.cz into SQLite.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--season",
        metavar="YEAR-HALF",
        help="season slug, e.g. 2025-podzim or 2026-jaro",
    )
    mode.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        help="ingest the N most recent seasons (probes which exist)",
    )
    mode.add_argument(
        "--current",
        action="store_true",
        help="scrape the current season, re-fetching cached pages",
    )
    parser.add_argument(
        "--group",
        metavar="TIER-LETTER",
        help="single group slug, e.g. 6-a (only with --season; default: all groups)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch pages even if cached (implied by --current)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=store.DB_PATH,
        help=f"SQLite database path (default: {store.DB_PATH})",
    )
    return parser


def parse_season_slug(slug: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d{4})-(jaro|podzim)", slug)
    if not m:
        raise SystemExit(f"bad --season {slug!r}, expected e.g. 2025-podzim")
    return int(m.group(1)), m.group(2)


def season_path(year: int, half: str) -> str:
    return f"/souteze/{year}-hanspaulska-liga-{half}/"


def iter_season_slugs(year: int, half: str):
    """Yield (year, half) going back in time from the given season."""
    while True:
        yield year, half
        year, half = (year, "jaro") if half == "podzim" else (year - 1, "podzim")


def guess_current(today: dt.date | None = None) -> tuple[int, str]:
    """jaro runs roughly Feb-Jun, podzim Sep-Dec."""
    today = today or dt.date.today()
    return today.year, ("jaro" if today.month <= 7 else "podzim")


def discover_seasons(count: int, *, start: tuple[int, str] | None = None,
                     misses_allowed: int = 2) -> list[tuple[int, str, str]]:
    """Probe season index pages going back in time until `count` exist.

    Returns list of (year, half, index_html). A miss (404) is tolerated
    misses_allowed times in a row (e.g. podzim not yet published); more
    misses means we ran off the end of the archive.
    """
    found: list[tuple[int, str, str]] = []
    misses = 0
    for year, half in iter_season_slugs(*(start or guess_current())):
        try:
            html = fetch.get(BASE + season_path(year, half))
        except fetch.NotFoundError:
            misses += 1
            if misses > misses_allowed:
                break
            continue
        misses = 0
        found.append((year, half, html))
        if len(found) >= count:
            break
    if len(found) < count:
        print(f"warning: only {len(found)} seasons found (asked for {count})")
    return found


def scrape_group(conn, group_id: int, group_url: str, *, force: bool = False) -> None:
    page = parse.parse_group_page(fetch.get(group_url, force=force))

    if not page.results_urls:
        if all(r.played == 0 for r in page.standings):
            # cancelled season (e.g. COVID 2020-jaro): schedule exists but
            # nothing was played and no detail records — store nothing
            print(f"  skipping {group_url}: season cancelled, 0 matches played")
            return
        # played matches but no detail endpoints — unknown legacy layout;
        # store standings + piskani so the season isn't lost, but say so
        print(f"  WARNING {group_url}: no results endpoints, "
              "storing standings only", file=sys.stderr)

    for round_ in sorted(page.results_urls):
        url = BASE + page.results_urls[round_]
        payload = json.loads(fetch.get(url, force=force))
        for m in parse.parse_results(payload["html"]):
            store.upsert_match(conn, group_id, m, detail_url=url)

    store.store_standings(conn, group_id, "prubezna", page.standings)
    if "final" in page.tables_urls:
        payload = json.loads(fetch.get(BASE + page.tables_urls["final"], force=force))
        try:
            final = parse.parse_standings_html(payload["html"])
        except parse.ParseError:
            pass  # final table not published until the season ends
        else:
            store.store_standings(conn, group_id, "vysledna", final)

    duties = parse.parse_piskani(fetch.get(group_url + "piskani/", force=force))
    store.store_duties(conn, group_id, duties)


def scrape_season(conn, year: int, half: str, index_html: str, *,
                  only_group: str | None = None, force: bool = False) -> None:
    slug = f"{year}-{half}"
    path = season_path(year, half)
    groups = parse.parse_season_index(index_html, path)
    if only_group:
        m = re.fullmatch(r"(\d+)-([a-z])", only_group)
        if not m:
            raise SystemExit(f"bad --group {only_group!r}, expected e.g. 6-a")
        want = (int(m.group(1)), m.group(2))
        groups = tuple(g for g in groups if (g.tier, g.letter) == want)
        if not groups:
            raise SystemExit(f"group {only_group} not found in season {slug}")

    season_id = store.upsert_season(conn, year, half, slug)
    print(f"season {slug}: {len(groups)} groups")
    for i, g in enumerate(groups, 1):
        group_url = BASE + g.url
        group_id = store.upsert_group(conn, season_id, g.tier, g.letter, group_url)
        try:
            scrape_group(conn, group_id, group_url, force=force)
        except (fetch.FetchError, parse.ParseError):
            # fail loudly and stop; cache makes the re-run resumable
            print(f"FAILED at {group_url}", file=sys.stderr)
            raise
        conn.commit()
        print(f"  [{i}/{len(groups)}] {g.tier}-{g.letter} ok")


def report_name_conflicts(conn) -> None:
    conflicts = store.find_name_conflicts(conn)
    if conflicts:
        print(f"{len(conflicts)} team-name conflicts need manual review:")
        for norm, names in conflicts:
            print(f"  {norm!r}: {names}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.group and not args.season:
        raise SystemExit("--group requires --season")

    conn = store.connect(args.db)
    try:
        if args.season:
            year, half = parse_season_slug(args.season)
            html = fetch.get(BASE + season_path(year, half), force=args.force)
            scrape_season(conn, year, half, html,
                          only_group=args.group, force=args.force)
        elif args.backfill:
            for year, half, html in discover_seasons(args.backfill):
                scrape_season(conn, year, half, html)
        else:  # --current
            year, half = guess_current()
            try:
                html = fetch.get(BASE + season_path(year, half), force=True)
            except fetch.NotFoundError:
                # e.g. August before podzim is published -> finish jaro instead
                year, half = (year, "jaro") if half == "podzim" else (year - 1, "podzim")
                html = fetch.get(BASE + season_path(year, half), force=True)
            scrape_season(conn, year, half, html, force=True)

        report_name_conflicts(conn)
    except parse.ParseError as exc:
        # site markup changed — do NOT auto-restart, fix the parser (exit 2)
        print(f"FATAL (parse): {exc}", file=sys.stderr)
        return 2
    except fetch.FetchError as exc:
        # transient network/server failure — safe to re-run, cache resumes (exit 1)
        print(f"FATAL (fetch): {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"done -> {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
