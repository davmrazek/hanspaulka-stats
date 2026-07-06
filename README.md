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

## Scraping etiquette

Max 1 request/second, honest User-Agent with contact email, every page cached
in `cache/` and never re-fetched (except the current season). Details in
`CLAUDE.md` — these rules are non-negotiable.
