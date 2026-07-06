# CLAUDE.md — Hanspaulka Stats

## What this project is

A statistics website for **Hanspaulská liga** (the largest amateur small-sided football league in the Czech Republic, run by PSMF — Pražský svaz malého fotbalu). We scrape public results from psmf.cz into SQLite, compute stats the official site doesn't offer (form tables, historical trends, head-to-head, scorer histories, referee activity), and publish them as a fast static website.

Owner: David Mrázek (FIT ČVUT student, solo founder of HansRef.cz — a referee marketplace for this same league). This project is both a public stats site AND market research for HansRef. Referee-related data (rozpis pískání, referee names per match) is first-class data here, not an afterthought.

## Language conventions

- Code, comments, commit messages, README: **English**.
- Site UI text: **Czech** (the audience is Czech team captains and players). Keep Czech domain terms as-is in data: *kolo* (round), *pískání* (refereeing duty), *střelci* (scorers), *brankáři* (goalkeepers), *soupiska* (roster).
- When talking to David in chat: brief and direct. No fluff, no long summaries of what you just did. He may invoke "caveman mode" — comply.

## Tech stack (do not deviate without asking)

- Python 3.11+, `requests`, `beautifulsoup4`, `lxml`
- Storage: **SQLite** (single file `data/hanspaulka.db`). No Postgres, no ORM — plain `sqlite3` with explicit SQL. Keep it simple.
- Site: **static site generation** (Python script renders Jinja2 templates → `site/` directory → GitHub Pages). No backend server in v1. Charts client-side with Chart.js from CDN.
- Tests: `pytest`. Parser tests run against **saved HTML fixtures** in `tests/fixtures/`, never against the live site.
- No heavy frameworks (no Django/Flask/React) unless PLAN.md phase explicitly calls for it.

## Scraping rules — NON-NEGOTIABLE

psmf.cz is a small nonprofit sports association. We want them as a future partner, not an enemy.

1. Check and respect `robots.txt` before any crawl.
2. **Max 1 request per second** (enforce with a delay in the fetch layer, not ad hoc `sleep` calls scattered around).
3. Set a honest User-Agent: `hanspaulka-stats/<version> (contact: davmrazek@seznam.cz)`.
4. **Cache every fetched page** as raw HTML on disk (`cache/` directory, keyed by URL slug). Never re-fetch a page that's already cached unless it's the current (unfinished) season. Historical seasons are immutable — fetch once, forever.
5. Scheduled scraping: current season only, **once per week** (results are complete by Monday evening; scrape Tuesday).
6. If the site structure changes and parsing fails, fail loudly and stop — do not hammer the site with retries. Max 2 retries per URL with backoff.
7. Scrape only public competition data. No personal data beyond what PSMF already publishes (player/referee names on public pages). No scraping of the discussion forum, no emails.

## Target site structure (verified July 2026)

- Server-rendered HTML, Vizus CMS, no login wall, no JS rendering needed.
- Season URL pattern: `https://www.psmf.cz/souteze/{year}-hanspaulska-liga-{jaro|podzim}/`
- Group URL pattern: append `{tier}-{group}/` — e.g. `.../2025-hanspaulska-liga-podzim/6-a/`
- Tiers: 1–8. Group counts vary by tier (tier 1 has group A only; tier 8 has up to M). **Discover groups by parsing the season index page — never hardcode the group list.**
- Each group page contains/links to: per-round results (with half-time scores), standings (Průběžná / Výsledná / Křížová), Střelci, Brankáři, referee assignment schedule (rozpis pískání), match detail pages (lineups, goal minutes, cards, referee names, commentary), and a season summary text.
- There are also parallel competitions (Veteránská liga etc.) under similar URLs — **out of scope for v1**, but don't structurally preclude them (keep `competition` as a column, not an assumption).
- Exact CSS selectors are NOT documented here. **First task of any parsing work: fetch one group page and one match detail page, save them as fixtures, and inspect the real markup.** Do not guess selectors.

## Database schema (reference — extend, don't rename)

```
seasons(id, year, half TEXT CHECK(half IN ('jaro','podzim')), slug, UNIQUE(year, half))
groups(id, season_id → seasons, tier INT, letter TEXT, url, UNIQUE(season_id, tier, letter))
teams(id, name, canonical_name)            -- names drift between seasons; keep mapping
group_teams(group_id, team_id)             -- membership per season/group
matches(id, group_id, round INT, date, home_team_id, away_team_id,
        home_goals, away_goals, ht_home, ht_away, referee TEXT, detail_url,
        UNIQUE(group_id, round, home_team_id, away_team_id))
players(id, name, team_id)                  -- best-effort; names only, per-team scope
goals(id, match_id, player_id, minute, team_id)
cards(id, match_id, player_id, color)
referee_duties(id, group_id, round, date, team_id, note)   -- from rozpis pískání
standings(group_id, team_id, kind TEXT CHECK(kind IN ('prubezna','vysledna')),
          position, played, won, drawn, lost, gf, ga, points)
```

All inserts idempotent (INSERT OR REPLACE / ON CONFLICT). Re-running the scraper on cached data must always be safe.

## Project layout

```
hanspaulka-stats/
├── CLAUDE.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── scraper/
│   ├── fetch.py        # rate-limited, cached HTTP layer (ONLY place requests is imported)
│   ├── parse.py        # HTML → dataclasses (pure functions, no I/O)
│   ├── store.py        # dataclasses → SQLite
│   └── run.py          # CLI: discover → fetch → parse → store
├── analysis/
│   └── stats.py        # SQL/Python computing derived stats
├── sitegen/
│   ├── build.py        # renders templates → site/
│   └── templates/
├── site/               # generated output (gitignored except CNAME)
├── cache/              # raw HTML cache (gitignored)
├── data/               # hanspaulka.db (gitignored)
└── tests/
    └── fixtures/       # saved real HTML pages for parser tests
```

## Working style

- Small commits, conventional messages (`feat:`, `fix:`, `chore:`).
- Parser code must be pure (HTML string in, dataclasses out) so it's testable from fixtures.
- Before writing a parser for a new page type: save a fixture first, write the test against it, then the parser.
- When a task is ambiguous, check PLAN.md for the current phase; do the smallest thing that completes the phase's Definition of Done. Ask before jumping phases.
- Never commit `cache/`, `data/`, or generated `site/` HTML (except deploy artifacts if PLAN.md Phase 4 says so).

## Things NOT to do

- No LinkedIn/Facebook/social scraping. Ever.
- No paid APIs, no cloud databases, no servers with monthly costs. Target running cost: **0 CZK** (GitHub Pages + GitHub Actions free tier).
- Don't scrape hanspaulska-liga.cz (the unofficial mirror site) — psmf.cz is the canonical source.
- Don't publish anything that could embarrass individual amateur players (e.g. "worst player" rankings). Negative stats only at team level. Referee stats: neutral activity/volume only, no quality rankings in the public site.
