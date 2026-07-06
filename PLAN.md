# PLAN.md — Hanspaulka Stats: Roadmap

Goal: the best statistics site for Hanspaulská liga. Free public value first, small monetization second, HansRef.cz funnel and market data throughout. Total running cost target: 0 CZK/month.

Each phase has a **Definition of Done (DoD)**. Do not start a phase before the previous DoD is met. Estimated effort assumes evenings/weekends work with Claude Code.

---

## Phase 0 — Skeleton (½ day)

- Repo init, `pyproject.toml`, project layout per CLAUDE.md, pytest wired up.
- `fetch.py`: rate-limited (1 req/s), disk-cached GET with honest User-Agent; unit tests with mocked responses.
- Save fixtures: one season index page, one group page, one match detail page, one rozpis pískání page (fetch manually, commit to `tests/fixtures/`).

**DoD:** `pytest` green; fixtures committed; `python -m scraper.run --help` prints CLI usage.

## Phase 1 — Parser for one group (1–2 days)

- Inspect fixtures; implement `parse.py` for: group page (rounds + results + standings), match detail (goals, minutes, cards, lineups, referee name), rozpis pískání.
- Pure functions returning dataclasses. Test coverage against fixtures for every parser.
- `store.py`: SQLite schema from CLAUDE.md, idempotent upserts.
- End-to-end: `python -m scraper.run --season 2025-podzim --group 6-a` fills the DB from cache/live.

**DoD:** one full group ingested; `sqlite3` spot-checks match the live site; re-running produces identical DB.

## Phase 2 — Full backfill (1 day + unattended runtime)

- Season/group discovery from season index pages (no hardcoded lists).
- Team name canonicalization (same team, drifting names across seasons) — maintain a mapping table, log unresolved conflicts for manual review.
- Backfill: current season + at least 4 historical seasons (more if URL pattern holds further back — probe carefully, 1 req/s). At ~70 groups × ~15 pages/group × several seasons this runs for hours — that's fine, it's cached and resumable.
- GitHub Actions workflow: weekly cron (Tuesday 06:00), scrapes current season only, commits updated DB artifact.

**DoD:** ≥5 seasons in DB; weekly cron green; README documents how to re-run backfill.

## Phase 3 — Stats engine (1–2 days)

Derived stats in `analysis/stats.py` (SQL views or Python), computed per season and all-time:

- **Team pages:** form (last 5), home/away splits, biggest wins/losses, promotion/relegation history across seasons, head-to-head vs any opponent, goals per round trend.
- **Group pages:** live standings incl. Křížová view, round-by-round position chart data, scorer race.
- **League-wide:** tier pyramid overview, most improved teams season-over-season, all-time scorer boards, longest unbeaten runs.
- **Referee/pískání data (private analysis, not public v1):** matches per referee, coverage gaps (matches with missing/no-show referees if detectable), duty distribution per team. → Export as a separate notebook/CSV. **This is HansRef market-sizing gold: real officiated-match volume for the whole league.**

**DoD:** `python -m analysis.stats --team "<name>"` prints a sensible team dossier; stats functions unit-tested on the backfilled DB.

## Phase 4 — Static site (2–3 days)

- `sitegen/build.py`: Jinja2 → static HTML into `site/`. Pages: home (tier pyramid + latest results), one page per group, one page per team, all-time records page. Czech UI.
- Chart.js (CDN) for: position-by-round line chart, goals trend, form sparklines.
- Mobile-first, fast, no build toolchain (no npm). Simple search (client-side JSON index of team names).
- Deploy: GitHub Pages via Actions (build after weekly scrape). Custom domain later (e.g. hanspaulkastats.cz, ~250 Kč/yr — the ONLY allowed cost, optional).
- SEO basics: per-team `<title>`/meta, sitemap.xml. Team names are what people google — every team page is a landing page.

**DoD:** site live on GitHub Pages, updates automatically weekly, Lighthouse ≥90 on mobile.

## Phase 5 — Launch & audience (ongoing, low effort)

- Post in Hanspaulka-adjacent Facebook groups / forum (where allowed) — "made a stats site for our league, feedback welcome".
- Weekly auto-generated "round recap" snippet (top results, scorer race movement) — copy-paste content for social posts.
- Feedback form (free tier of Tally/Google Forms). Track what captains ask for.
- Add a modest footer note: "Vytvořil David Mrázek · HansRef.cz — rozhodčí pro váš tým". This is the funnel. Keep it tasteful.

**DoD:** ≥100 unique weekly visitors (Plausible/GoatCounter free tier or GitHub Pages analytics-lite via Cloudflare) OR 10 pieces of captain feedback — whichever first. If neither after 6 weeks, revisit content strategy before building more features.

## Phase 6 — Monetization (only after Phase 5 DoD)

Ordered by realism, not by revenue ceiling:

1. **HansRef funnel (indirect, highest strategic value).** Team pages show "Sháníte rozhodčího na příští zápas? → HansRef.cz". The stats site's audience IS HansRef's customer. Even 0 CZK direct revenue is fine if it feeds signups.
2. **Premium captain features (~49 Kč/měs or 199 Kč/season, via Stripe Payment Links — no backend needed for v1, gate content with a simple token).** Candidates, based on Phase 5 feedback: opponent scouting dossier (H2H, form, danger scorers) before each match; email/WhatsApp round digest for your team; printable season report.
3. **Season report PDFs (one-off 149 Kč).** Auto-generated end-of-season team report (charts, records, roster stats) — teams buy these as keepsakes. Reuses the stats engine; near-zero marginal cost.
4. **Sponzoring/ads (last resort).** Local sports shops, pitch rental venues, Prague sports brands — a single tasteful sponsor banner. Avoid programmatic ads (pennies, ruins the site).

Realistic expectation: hundreds to low thousands of CZK per month at best. The real payoff is #1 plus the referee-market dataset for HansRef plus the portfolio story ("built and monetized a data product end-to-end").

**DoD:** first paid transaction OR documented decision to keep the site free as a pure HansRef funnel.

---

## Risks & mitigations

- **Site redesign breaks parsers.** Fixtures + loud failures + cached history means we never lose data; fixing a parser is a contained task.
- **PSMF objects to scraping.** Mitigate by good etiquette (see CLAUDE.md), and proactively: once the site looks good (Phase 4), email PSMF (vedouci@psmf.cz) showing the site and offering attribution/cooperation. Being invited in beats sneaking around — and a PSMF blessing is a moat.
- **Team name ambiguity poisons all-time stats.** Canonicalization table with manual review queue; mark low-confidence merges and exclude them from records pages.
- **Nobody visits.** Phase 5 DoD is an explicit kill/pivot gate before more effort is sunk.
- **Legal:** facts (scores, standings) are not copyrightable; do NOT republish PSMF's editorial texts (season summaries/commentary) verbatim — link to them instead. Player names appear only as already published by PSMF; honor any takedown request immediately.

## Explicit non-goals (v1)

- Veteránská/Superveteránská/futsal competitions (schema supports later, UI doesn't).
- User accounts, login, comments.
- Live scores, mobile app.
- Public referee quality rankings (only neutral volume stats, and only in later versions if PSMF is on board).

## Success = 

1. Site live, auto-updating, 0 CZK/month running cost.
2. A dataset (multi-season SQLite) that quantifies the referee market for HansRef.
3. A portfolio piece: "scraped, modeled, analyzed and published data for the largest amateur football league in CZ" — scraping, SQL, data viz, product thinking in one repo.
