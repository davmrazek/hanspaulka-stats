"""CLI entry point: discover -> fetch -> parse -> store.

Usage: python -m scraper.run --season 2025-podzim --group 6-a
Pipeline is implemented in Phase 1; this module currently only defines the CLI.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scraper.run",
        description="Scrape Hanspaulska liga results from psmf.cz into SQLite.",
    )
    parser.add_argument(
        "--season",
        metavar="YEAR-HALF",
        help="season slug, e.g. 2025-podzim or 2026-jaro",
    )
    parser.add_argument(
        "--group",
        metavar="TIER-LETTER",
        help="group slug, e.g. 6-a (default: all groups in the season)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch pages even if cached (current season only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise SystemExit(
        f"scraping pipeline not implemented yet (Phase 1); got args: {args}"
    )


if __name__ == "__main__":
    main()
