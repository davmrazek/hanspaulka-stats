"""Unit tests for scraper.fetch — all network calls mocked, no live requests."""

import pytest
import requests

from scraper import fetch


class FakeResponse:
    def __init__(self, text="<html>ok</html>", status=200):
        self.text = text
        self.status_code = status
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Neutralize throttle/backoff sleeps so tests run instantly."""
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)


URL = "https://www.psmf.cz/souteze/2025-hanspaulska-liga-podzim/6-a/"


def test_url_to_slug():
    slug = fetch.url_to_slug(URL)
    assert slug == "www.psmf.cz_souteze_2025-hanspaulska-liga-podzim_6-a.html"
    assert "/" not in slug


def test_get_fetches_and_caches(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse("<html>page</html>")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    html = fetch.get(URL, cache_dir=tmp_path)

    assert html == "<html>page</html>"
    assert len(calls) == 1
    assert fetch.cache_path(URL, tmp_path).read_text(encoding="utf-8") == html


def test_get_uses_cache_without_network(tmp_path, monkeypatch):
    path = fetch.cache_path(URL, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html>cached</html>", encoding="utf-8")

    def fail_get(url, **kwargs):
        raise AssertionError("network hit despite cache")

    monkeypatch.setattr(fetch.requests, "get", fail_get)
    assert fetch.get(URL, cache_dir=tmp_path) == "<html>cached</html>"


def test_force_refetches_cached_page(tmp_path, monkeypatch):
    path = fetch.cache_path(URL, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html>stale</html>", encoding="utf-8")

    monkeypatch.setattr(
        fetch.requests, "get", lambda url, **kw: FakeResponse("<html>fresh</html>")
    )
    assert fetch.get(URL, force=True, cache_dir=tmp_path) == "<html>fresh</html>"
    assert path.read_text(encoding="utf-8") == "<html>fresh</html>"


def test_honest_user_agent(tmp_path, monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs.get("headers", {}))
        return FakeResponse()

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    fetch.get(URL, cache_dir=tmp_path)

    assert seen["User-Agent"].startswith("hanspaulka-stats/")
    assert "davmrazek@seznam.cz" in seen["User-Agent"]


def test_throttle_enforces_min_interval(tmp_path, monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    monkeypatch.setattr(fetch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(fetch.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(fetch.requests, "get", lambda url, **kw: FakeResponse())
    monkeypatch.setattr(fetch, "_last_request_time", 0.0)

    fetch.get(URL, cache_dir=tmp_path)
    clock["now"] = 0.3  # only 0.3s later
    fetch.get(URL + "other/", cache_dir=tmp_path)

    assert any(s >= 0.69 for s in sleeps), f"expected ~0.7s throttle sleep, got {sleeps}"


def test_retries_then_fails_loudly(tmp_path, monkeypatch):
    calls = []

    def failing_get(url, **kwargs):
        calls.append(url)
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(fetch.requests, "get", failing_get)
    with pytest.raises(fetch.FetchError):
        fetch.get(URL, cache_dir=tmp_path)

    assert len(calls) == 1 + fetch.MAX_RETRIES  # initial + 2 retries, no more


def test_retry_succeeds_after_transient_error(tmp_path, monkeypatch):
    attempts = {"n": 0}

    def flaky_get(url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeResponse(status=503)
        return FakeResponse("<html>recovered</html>")

    monkeypatch.setattr(fetch.requests, "get", flaky_get)
    assert fetch.get(URL, cache_dir=tmp_path) == "<html>recovered</html>"
    assert attempts["n"] == 2


def test_http_error_response_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fetch.requests, "get", lambda url, **kw: FakeResponse(status=404)
    )
    with pytest.raises(fetch.FetchError):
        fetch.get(URL, cache_dir=tmp_path)
    assert not fetch.cache_path(URL, tmp_path).exists()
