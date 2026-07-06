"""HTML -> dataclasses. Pure functions, no I/O.

Markup verified against fixtures in tests/fixtures/ (psmf.cz, Vizus CMS,
July 2026). If selectors stop matching, these parsers raise ParseError —
fail loudly, do not retry (see CLAUDE.md scraping rules).

Page anatomy (see tests/fixtures/README.md):
- group page embeds: last-round results table, current standings
  (table.tables-table), rozpis piskani (table.referees-table), and data-url
  attributes pointing at per-round JSON endpoints (?cmd=results&...&round=N).
- the cmd=results endpoint returns {"html": ...} with one
  div#GameResultItem{gameid} block per match: score + half-time, commentary,
  referee, lineups, goals, cards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag


class ParseError(Exception):
    """Site markup did not match expectations. Fail loudly, fix the parser."""


# --- dataclasses -----------------------------------------------------------


@dataclass(frozen=True)
class GoalEvent:
    side: str  # 'home' | 'away' — team the goal counts FOR
    minute: int | None
    player: str | None  # None for own goals ("OG" — scorer not published)
    own_goal: bool = False


@dataclass(frozen=True)
class CardEvent:
    side: str  # 'home' | 'away'
    minute: int | None
    player: str
    color: str  # 'yellow' | 'red'


@dataclass(frozen=True)
class Lineup:
    goalkeeper: str | None
    players: tuple[str, ...]  # field players, goalkeeper excluded
    captain: str | None = None
    best: str | None = None  # "hvezda zapasu" per PSMF match report


@dataclass(frozen=True)
class MatchResult:
    gameid: int
    round: int
    date: str | None  # ISO YYYY-MM-DD
    time: str | None  # "20:45"
    pitch: str | None
    home_team: str
    away_team: str
    home_goals: int | None
    away_goals: int | None
    ht_home: int | None = None
    ht_away: int | None = None
    referee: str | None = None
    commentary: str | None = None
    home_lineup: Lineup | None = None
    away_lineup: Lineup | None = None
    goals: tuple[GoalEvent, ...] = ()
    cards: tuple[CardEvent, ...] = ()


@dataclass(frozen=True)
class StandingRow:
    position: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    points: int


@dataclass(frozen=True)
class RefereeDuty:
    date: str  # ISO
    times: str  # "18:00, 19:15"
    pitch: str
    team: str


@dataclass(frozen=True)
class GroupPage:
    name: str  # e.g. "Hanspaulská liga 6A"
    results_urls: dict[int, str] = field(default_factory=dict)  # round -> relative URL
    tables_urls: dict[str, str] = field(default_factory=dict)  # 'actual'|'final'|'cross' -> URL
    standings: tuple[StandingRow, ...] = ()  # the embedded (prubezna) table
    duties: tuple[RefereeDuty, ...] = ()  # may be truncated; piskani page has all


# --- helpers ---------------------------------------------------------------

_CZ_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})")


def parse_czech_date(text: str) -> str | None:
    """'Po 8.12.25' / '8. 12. 25' -> '2025-12-08'."""
    m = _CZ_DATE.search(text.replace("\xa0", " "))
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _parse_score(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*:\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_events(cell: Tag, side: str) -> list[GoalEvent]:
    """Goal cell: lines like '9. Pavel Novanský', '48., 50. Michal Kocourek',
    '52. OG' (own goal, scorer not published)."""
    events: list[GoalEvent] = []
    for line in _cell_lines(cell):
        m = re.match(r"((?:\d+\.,?\s*)+)\s*(.*)", line)
        if not m:
            raise ParseError(f"unrecognized goal line: {line!r}")
        minutes = [int(x) for x in re.findall(r"\d+", m.group(1))]
        who = m.group(2).strip()
        own = who == "OG"
        for minute in minutes:
            events.append(
                GoalEvent(side, minute, None if own else who or None, own_goal=own)
            )
    return events


def _parse_cards(cell: Tag, side: str) -> list[CardEvent]:
    """Card cell: <span class="component__table-card is-yellow">55. Name</span>."""
    events: list[CardEvent] = []
    for span in cell.find_all("span", class_="component__table-card"):
        classes = span.get("class", [])
        color = "red" if "is-red" in classes else "yellow"
        text = _clean(span.get_text())
        m = re.match(r"(?:(\d+)\.\s*)?(.+)", text)
        if not m:
            raise ParseError(f"unrecognized card entry: {text!r}")
        minute = int(m.group(1)) if m.group(1) else None
        events.append(CardEvent(side, minute, m.group(2).strip(), color))
    return events


def _cell_lines(cell: Tag) -> list[str]:
    """Text lines of a cell, split on <br>."""
    html = cell.decode_contents()
    parts = re.split(r"<br\s*/?>", html)
    lines = []
    for part in parts:
        text = _clean(BeautifulSoup(part, "lxml").get_text())
        if text:
            lines.append(text)
    return lines


def _parse_lineup(cell: Tag) -> Lineup | None:
    """'GK – field, players, ...'; span.is-captain / is-best / is-best-and-captain."""
    text = _clean(cell.get_text())
    if not text:
        return None
    captain = best = None
    for span in cell.find_all("span"):
        classes = span.get("class", [])
        name = _clean(span.get_text())
        if "is-best-and-captain" in classes:
            captain = best = name
        elif "is-captain" in classes:
            captain = name
        elif "is-best" in classes:
            best = name
    # goalkeeper separated by en dash (or hyphen) from field players
    gk_split = re.split(r"\s+[–-]\s+", text, maxsplit=1)
    if len(gk_split) == 2:
        goalkeeper, rest = gk_split[0].strip(), gk_split[1]
    else:
        goalkeeper, rest = None, gk_split[0]
    players = tuple(p.strip() for p in rest.split(",") if p.strip())
    return Lineup(goalkeeper, players, captain, best)


# --- match detail blocks (cmd=results endpoint, or "Detaily utkani") -------


def parse_results(html: str) -> list[MatchResult]:
    """Parse the HTML payload of the ?cmd=results endpoint (one round)."""
    soup = BeautifulSoup(html, "lxml")
    blocks = soup.find_all("div", id=re.compile(r"^GameResultItem\d+$"))
    if not blocks:
        raise ParseError("no GameResultItem blocks found in results HTML")
    return [_parse_result_block(b) for b in blocks]


def _parse_result_block(block: Tag) -> MatchResult:
    gameid = int(re.sub(r"\D", "", block["id"]))
    tables = block.find_all("table", class_="is-inside")
    header = tables[0]
    row = header.find_all("tr")[1]
    cells = row.find_all("td")
    if len(cells) < 6:
        raise ParseError(f"game {gameid}: header row has {len(cells)} cells")

    date = parse_czech_date(cells[0].get_text())
    time = _clean(cells[1].get_text()) or None
    pitch = _clean(cells[2].get_text()) or None
    team_links = cells[3].find_all("a")
    if len(team_links) != 2:
        raise ParseError(f"game {gameid}: expected 2 team links")
    home_team, away_team = (_clean(a.get_text()) for a in team_links)
    round_ = int(re.sub(r"\D", "", cells[4].get_text()))

    result_td = cells[5]
    ht = None
    ht_span = result_td.find("span", class_="period-goals")
    if ht_span:
        ht = _parse_score(ht_span.get_text())
        ht_span.extract()
    score = _parse_score(result_td.get_text())

    referee = commentary = None
    home_lineup = away_lineup = None
    goals: list[GoalEvent] = []
    cards: list[CardEvent] = []

    for table in tables[1:]:
        heads = [_clean(th.get_text()) for th in table.find_all("th")]
        data_rows = [tr for tr in table.find_all("tr") if tr.find("td")]
        if not data_rows:
            continue
        tds = data_rows[0].find_all("td")
        if "Popis zápasu" in heads:
            commentary = _clean(tds[0].get_text()) or None
            referee = _clean(tds[1].get_text()) or None
        elif "Góly" in heads:
            goals += _parse_events(tds[0], "home") + _parse_events(tds[3], "away")
            cards += _parse_cards(tds[1], "home") + _parse_cards(tds[4], "away")
        elif len(tds) == 3:  # lineups: home | vs-icon | away
            home_lineup = _parse_lineup(tds[0])
            away_lineup = _parse_lineup(tds[2])

    return MatchResult(
        gameid=gameid,
        round=round_,
        date=date,
        time=time,
        pitch=pitch,
        home_team=home_team,
        away_team=away_team,
        home_goals=score[0] if score else None,
        away_goals=score[1] if score else None,
        ht_home=ht[0] if ht else None,
        ht_away=ht[1] if ht else None,
        referee=referee,
        commentary=commentary,
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        goals=tuple(goals),
        cards=tuple(cards),
    )


# --- standings -------------------------------------------------------------


def parse_standings(table: Tag) -> tuple[StandingRow, ...]:
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        texts = [_clean(td.get_text()) for td in tds]
        score = _parse_score(texts[6])
        if score is None:
            raise ParseError(f"standings: bad score column {texts[6]!r}")
        rows.append(
            StandingRow(
                position=int(re.sub(r"\D", "", texts[0])),
                team=texts[1],
                played=int(texts[2]),
                won=int(texts[3]),
                drawn=int(texts[4]),
                lost=int(texts[5]),
                gf=score[0],
                ga=score[1],
                points=int(texts[7]),
            )
        )
    if not rows:
        raise ParseError("standings table has no data rows")
    return tuple(rows)


def parse_standings_html(html: str) -> tuple[StandingRow, ...]:
    """Parse standings from a full page or a cmd=tables HTML payload."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="tables-table") or soup.find("table")
    if table is None:
        raise ParseError("no standings table found")
    return parse_standings(table)


# --- rozpis piskani --------------------------------------------------------


def parse_piskani(html: str) -> tuple[RefereeDuty, ...]:
    """Parse referee duty schedule (table.referees-table) from any page."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="referees-table")
    if table is None:
        raise ParseError("no referees-table found")
    duties = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        date = parse_czech_date(tds[0].get_text())
        if date is None:
            raise ParseError(f"piskani: bad date {tds[0].get_text()!r}")
        duties.append(
            RefereeDuty(
                date=date,
                times=_clean(tds[1].get_text()),
                pitch=_clean(tds[2].get_text()),
                team=_clean(tds[3].get_text()),
            )
        )
    if not duties:
        raise ParseError("referees-table has no data rows")
    return tuple(duties)


# --- group page ------------------------------------------------------------


def parse_group_page(html: str) -> GroupPage:
    soup = BeautifulSoup(html, "lxml")
    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    name = h1s[-1] if h1s else ""

    results_urls: dict[int, str] = {}
    tables_urls: dict[str, str] = {}
    for a in soup.find_all(attrs={"data-url": True}):
        url = a["data-url"]
        query = parse_qs(urlparse(url).query)
        cmd = query.get("cmd", [None])[0]
        if cmd == "results" and "round" in query:
            results_urls[int(query["round"][0])] = url
        elif cmd == "tables" and "type" in query:
            tables_urls[query["type"][0]] = url
    if not results_urls:
        raise ParseError("group page: no cmd=results round URLs found")

    standings_table = soup.find("table", class_="tables-table")
    if standings_table is None:
        raise ParseError("group page: no tables-table found")

    duties: tuple[RefereeDuty, ...] = ()
    if soup.find("table", class_="referees-table"):
        duties = parse_piskani(html)

    return GroupPage(
        name=name,
        results_urls=results_urls,
        tables_urls=tables_urls,
        standings=parse_standings(standings_table),
        duties=duties,
    )
