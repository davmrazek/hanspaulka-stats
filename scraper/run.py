"""CLI entry point: discover -> fetch -> parse -> store.

Usage: python -m scraper.run --season 2025-podzim --group 6-a
Season/group discovery (no --group) comes in Phase 2.
"""

from __future__ import annotations

import argparse
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
    parser.add_argument(
        "--season",
        required=True,
        metavar="YEAR-HALF",
        help="season slug, e.g. 2025-podzim or 2026-jaro",
    )
    parser.add_argument(
        "--group",
        required=True,  # relaxed in Phase 2 (group discovery)
        metavar="TIER-LETTER",
        help="group slug, e.g. 6-a",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch pages even if cached (current season only)",
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


def parse_group_slug(slug: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d)-([a-z])", slug)
    if not m:
        raise SystemExit(f"bad --group {slug!r}, expected e.g. 6-a")
    return int(m.group(1)), m.group(2)


def scrape_group(season_slug: str, group_slug: str, *, force: bool = False,
                 db_path: Path = store.DB_PATH) -> None:
    year, half = parse_season_slug(season_slug)
    tier, letter = parse_group_slug(group_slug)
    season_path = f"/souteze/{year}-hanspaulska-liga-{half}/"
    group_url = f"{BASE}{season_path}{group_slug}/"

    print(f"group page: {group_url}")
    page = parse.parse_group_page(fetch.get(group_url, force=force))

    conn = store.connect(db_path)
    with conn:
        season_id = store.upsert_season(conn, year, half, season_slug)
        group_id = store.upsert_group(conn, season_id, tier, letter, group_url)

        for round_ in sorted(page.results_urls):
            url = BASE + page.results_urls[round_]
            payload = json.loads(fetch.get(url, force=force))
            matches = parse.parse_results(payload["html"])
            for m in matches:
                store.upsert_match(conn, group_id, m, detail_url=url)
            print(f"round {round_}: {len(matches)} matches")

        store.store_standings(conn, group_id, "prubezna", page.standings)
        if "final" in page.tables_urls:
            payload = json.loads(fetch.get(BASE + page.tables_urls["final"], force=force))
            try:
                final = parse.parse_standings_html(payload["html"])
            except parse.ParseError:
                print("final standings not published yet, skipping")
            else:
                store.store_standings(conn, group_id, "vysledna", final)
                print("final standings stored")

        duties = parse.parse_piskani(fetch.get(group_url + "piskani/", force=force))
        store.store_duties(conn, group_id, duties)
        print(f"piskani: {len(duties)} duties")

    conn.close()
    print(f"done -> {db_path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scrape_group(args.season, args.group, force=args.force, db_path=args.db)
    except (fetch.FetchError, parse.ParseError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
