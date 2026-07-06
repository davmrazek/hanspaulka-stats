# Fixtures — real pages saved from psmf.cz

Fetched 2026-07-06 via `scraper.fetch` (rate-limited, honest UA). Parser tests run
against these files, never against the live site.

| File | Source URL |
|---|---|
| `season_index_2025-podzim.html` | https://www.psmf.cz/souteze/2025-hanspaulska-liga-podzim/ |
| `group_2025-podzim_6-a.html` | https://www.psmf.cz/souteze/2025-hanspaulska-liga-podzim/6-a/ |
| `results_2025-podzim_6-a_round-11.json` | https://www.psmf.cz/souteze/2025-hanspaulska-liga-podzim/6-a/?cmd=results&competition=1&year=2025&season=2&league=6&group_id=1&round=11 |
| `piskani_2025-podzim_6-a.html` | https://www.psmf.cz/souteze/2025-hanspaulska-liga-podzim/6-a/piskani/ |

## Structure notes (verified 2026-07-06)

- There are **no standalone match detail pages**. Group pages render result rows
  with `data-gameid` / `data-round`; clicking loads a JSON endpoint
  `{group_url}?cmd=results&...&round=N` returning `{"html": "..."}`. That HTML
  contains, per match (`id="GameResultItem{gameid}"`): date, time, pitch,
  teams, score + half-time score (`span.period-goals`), match commentary
  (*Popis zápasu*), referee name (*Rozhodčí*), lineups (goalkeeper `&ndash;`
  separated from field players; `span.is-captain` / `is-best` /
  `is-best-and-captain`), goals with minutes (`52. OG` = own goal), and cards.
- Group page also exposes sibling `cmd` endpoints via `data-url`:
  `cmd=games` (schedule), `cmd=tables&type=actual|final|cross` (standings),
  `cmd=stats&subtype=shooters|goalkeepers|offensive|defensive`,
  `cmd=referees` (rozpis pískání data).
- robots.txt (checked 2026-07-06) disallows only `/cms/` — `/souteze/` is allowed.
- `cmd=results` and `cmd=tables` return JSON `{"html": ...}`; `cmd=stats`
  returns **raw HTML**, not JSON.
- **Kontumace (forfeit)**: the official score is the awarded one (e.g. 0:6)
  while goal events still list the on-pitch goals (gameid 301573, 2025-podzim
  6-A round 10: result 0:6, HT (7:0), 12+3 scorer entries; commentary says
  "Zápas byl kontumován"). So `home_goals`/`away_goals` may legitimately
  disagree with COUNT(goals). PSMF's own Střelci stats DO count goals from
  forfeited matches.
