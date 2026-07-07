# hanspaulka-stats

Statistics site for **Hanspaulská liga** (PSMF small-sided football, Prague).
Scrapes public results from psmf.cz into SQLite and publishes derived stats as
a static website. See `CLAUDE.md` for conventions and `PLAN.md` for the roadmap.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Usage

```sh
.venv/bin/python -m pytest            # run tests (parsers test against tests/fixtures/)
.venv/bin/python -m scraper.run --help
```

## Backfill

```sh
.venv/bin/python -m scraper.run --backfill 5    # 5 most recent seasons
.venv/bin/python -m scraper.run --season 2024-jaro          # one season
.venv/bin/python -m scraper.run --season 2024-jaro --group 6-a  # one group
```

Backfill probes season index URLs going back from the current season
(`{year}-hanspaulska-liga-{jaro|podzim}`) and ingests every group it
discovers — no hardcoded lists. At 1 request/second a full season takes
~20 minutes; historical pages are cached in `cache/` so an interrupted
run resumes where it stopped (re-run the same command). Parsing failures
abort the run loudly by design — fix the parser, re-run.

The weekly GitHub Actions workflow (`.github/workflows/scrape.yml`,
Tuesday mornings) scrapes only the current season with `--current`
(re-fetches its pages) and publishes `hanspaulka.db` as the `db-latest`
release asset. The DB is never committed to the repo.

## Team name canonicalization

Team names drift between seasons. `teams.canonical_name` starts equal to
`name`; the scraper prints likely duplicates (accent/case/punctuation
variants) after each run. Review and merge manually:

```sh
sqlite3 data/hanspaulka.db "UPDATE teams SET canonical_name='X' WHERE name IN ('X','X FC')"
```

## Scraping etiquette

Max 1 request/second, honest User-Agent with contact email, every page cached
in `cache/` and never re-fetched (except the current season). Details in
`CLAUDE.md` — these rules are non-negotiable.
