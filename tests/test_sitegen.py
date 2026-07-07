"""Site generation smoke tests on a fixture-built DB."""

import json
from pathlib import Path

import pytest

from scraper import parse, store
from sitegen import build

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    conn = store.connect(path)
    season_id = store.upsert_season(conn, 2025, "podzim", "2025-podzim")
    group_id = store.upsert_group(conn, season_id, 6, "a", "https://example/6-a/")
    for name in ("results_2025-podzim_6-a_round-4.json",
                 "results_2025-podzim_6-a_round-11.json"):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for m in parse.parse_results(payload["html"]):
            store.upsert_match(conn, group_id, m)
    page = parse.parse_group_page(
        (FIXTURES / "group_2025-podzim_6-a.html").read_text(encoding="utf-8"))
    store.store_standings(conn, group_id, "prubezna", page.standings)
    conn.commit()
    conn.close()
    return path


def test_slugify():
    assert build.slugify("Nebeský bastardi") == "nebesky-bastardi"
    assert build.slugify("A.Č.A.B. FC") == "a-c-a-b-fc"
    assert build.slugify("Habet Praha FŇ") == "habet-praha-fn"


def test_build_renders_all_page_types(db_path, tmp_path):
    out = tmp_path / "site"
    count = build.build(db_path, out, base_url="/prefix")
    assert count == 1 + 1 + 12 + 1  # home + group + 12 teams + records

    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Pyramida" in home and 'href="/prefix/style.css"' in home

    group = (out / "skupina/2025-podzim/6-a/index.html").read_text(encoding="utf-8")
    assert "Průběžná tabulka" in group
    assert "Nebeský bastardi" in group
    assert "position-chart" in group
    assert "Křížová tabulka" in group

    team = (out / "tym/power-rangers/index.html").read_text(encoding="utf-8")
    assert "Power Rangers" in team and "Historie sezón" in team

    records = (out / "rekordy/index.html").read_text(encoding="utf-8")
    assert "Nejlepší střelci" in records

    idx = json.loads((out / "teams.json").read_text(encoding="utf-8"))
    assert len(idx) == 12
    assert all(t["u"].startswith("/prefix/tym/") for t in idx)

    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<url>") == count


def test_rebuild_preserves_cname(db_path, tmp_path):
    out = tmp_path / "site"
    out.mkdir()
    (out / "CNAME").write_text("example.cz")
    (out / "stale.html").write_text("old")
    build.build(db_path, out)
    assert (out / "CNAME").read_text() == "example.cz"
    assert not (out / "stale.html").exists()
