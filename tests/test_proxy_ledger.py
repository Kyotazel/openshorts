"""Where the paid proxy bytes come from, and the trail that survives a deploy.

DataImpulse showed ~0.2 $/day of traffic with the static pool healthy, and
$14 on 28-aug-2026 that nothing on the server could explain (in-memory
counter, hourly log rotation). These pin the two leaks found and the ledger.
"""
import asyncio
import json

import pytest
import yt_dlp

from cloud import metering, proxy_ledger


# --- probe policy ----------------------------------------------------------

class TestStaticFailureWarrantsPaid:
    def test_ip_specific_failures_do(self):
        for err in ("ERROR: Sign in to confirm you're not a bot",
                    "HTTP Error 403: Forbidden", "HTTP Error 429: Too Many Requests",
                    "ProxyError: Tunnel connection failed: 407",
                    "Unable to download webpage: The read operation timed out",
                    "The uploader has not made this video available in your country"):
            assert metering.static_failure_warrants_paid(err), err

    def test_content_failures_do_not(self):
        for err in ("ERROR: Private video. Sign in if you've been granted access",
                    "ERROR: Video unavailable", "This video has been removed by the uploader",
                    "Join this channel to get access to members-only content",
                    "no duration in metadata", "Premieres in 3 hours",
                    "Unsupported URL: https://shopee.vn/product", "Sign in to confirm your age"):
            assert not metering.static_failure_warrants_paid(err), err

    def test_unknown_errors_stay_free(self):
        assert not metering.static_failure_warrants_paid("something odd happened")
        assert not metering.static_failure_warrants_paid("")


def test_non_youtube_urls_are_recognised():
    assert metering.is_youtube_url("https://www.youtube.com/watch?v=x")
    assert metering.is_youtube_url("https://youtu.be/x")
    assert metering.is_youtube_url("https://m.youtube.com/shorts/x")
    for u in ("https://www.twitch.tv/videos/1", "https://kick.com/x", "https://rumble.com/v",
              "https://drive.google.com/file/d/x", "https://www.openshorts.app/videos/x.mp4"):
        assert not metering.is_youtube_url(u), u


def test_probe_never_reaches_paid_for_non_youtube(monkeypatch):
    """Twitch/Kick/Rumble/product pages were all showing up on the paid proxy."""
    monkeypatch.setenv("PROXY_URL", "http://paid")
    monkeypatch.setenv("STATIC_PROXY_URLS", "http://s1")
    monkeypatch.delenv("DIRECT_FIRST", raising=False)
    monkeypatch.setenv("BGUTIL_SCRIPT_PATH", "")
    monkeypatch.setenv("BGUTIL_BASE_URL", "")
    seen = []

    class _FakeYDL:
        def __init__(self, opts): seen.append(opts.get("proxy"))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(metering, "_ffprobe_url_seconds", lambda url, timeout=30: 0.0)
    with pytest.raises(ValueError):
        metering.probe_url_minutes("https://www.twitch.tv/videos/123")
    assert "http://paid" not in seen
    assert metering.pop_paid_probe_events() == []


def test_probe_reaches_paid_only_for_ip_specific_failures(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://paid")
    monkeypatch.setenv("STATIC_PROXY_URLS", "http://s1")
    monkeypatch.delenv("DIRECT_FIRST", raising=False)
    monkeypatch.setenv("BGUTIL_SCRIPT_PATH", "")
    monkeypatch.setenv("BGUTIL_BASE_URL", "")

    def run(static_error):
        seen = []

        class _FakeYDL:
            def __init__(self, opts): self.proxy = opts.get("proxy"); seen.append(self.proxy)
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                if self.proxy == "http://paid":
                    return {"duration": 600}
                raise RuntimeError(static_error)

        monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
        monkeypatch.setattr(metering, "_ffprobe_url_seconds", lambda url, timeout=30: 0.0)
        metering.pop_paid_probe_events()
        try:
            minutes = metering.probe_url_minutes("https://www.youtube.com/watch?v=abc")
        except ValueError:
            minutes = None
        return minutes, seen, metering.pop_paid_probe_events()

    minutes, seen, events = run("Sign in to confirm you're not a bot")
    assert minutes == 10.0 and "http://paid" in seen
    assert len(events) == 1 and "static1" in events[0]["static_errors"]

    minutes, seen, events = run("ERROR: Private video")
    assert minutes is None and "http://paid" not in seen and events == []


# --- the trail -------------------------------------------------------------

def _route(winner, attempts):
    return {"winner": winner, "paid_bytes": sum(a["bytes"] for a in attempts if a.get("paid")),
            "attempts": attempts}


def test_route_line_round_trip():
    route = _route("fallback", [
        {"label": "HD-static1", "ok": False, "bytes": 0, "paid": False, "error": "HTTP Error 403"},
        {"label": "HD", "ok": False, "bytes": 31_000_000, "paid": True, "error": "read timed out"},
        {"label": "fallback", "ok": True, "bytes": 70_000_000, "paid": True},
    ])
    parsed = proxy_ledger.parse_route_line("PROXY_ROUTE=" + json.dumps(route))
    assert parsed == route
    assert proxy_ledger.parse_route_line("PROXY_ROUTE=not json") is None
    assert proxy_ledger.parse_route_line("other") is None


def test_paid_bytes_count_failed_paid_attempts_too():
    route = _route("fallback", [
        {"label": "HD", "ok": False, "bytes": 31_000_000, "paid": True, "error": "x"},
        {"label": "fallback", "ok": True, "bytes": 70_000_000, "paid": True},
    ])
    assert proxy_ledger.paid_bytes_of(route) == 101_000_000
    assert proxy_ledger.used_paid(route)


def test_free_download_is_not_paid():
    route = _route("HD-static1", [{"label": "HD-static1", "ok": True, "bytes": 90_000_000, "paid": False}])
    assert not proxy_ledger.used_paid(route)
    assert proxy_ledger.describe_failures(route) == ""


def test_failure_summary_names_the_free_routes_that_failed():
    route = _route("HD", [
        {"label": "HD-static1", "ok": False, "bytes": 0, "paid": False, "error": "Sign in to confirm you're not a bot"},
        {"label": "HD-static2", "ok": False, "bytes": 0, "paid": False, "error": "HTTP Error 429"},
        {"label": "HD", "ok": True, "bytes": 50_000_000, "paid": True},
    ])
    text = proxy_ledger.describe_failures(route)
    assert "HD-static1: Sign in" in text and "HD-static2: HTTP Error 429" in text
    assert "HD:" not in text


def test_alert_folds_a_burst_into_one_message(monkeypatch):
    sent = []

    async def fake_send(text):
        sent.append(text)

    import cloud.alerts as alerts
    monkeypatch.setattr(alerts, "send_telegram", fake_send)
    monkeypatch.setattr(proxy_ledger, "_persist", lambda *a, **k: asyncio.sleep(0))
    proxy_ledger._pending.update(count=0, bytes=0, lines=[], last_sent=0.0)

    async def go():
        for i in range(4):
            await proxy_ledger.record_download(f"job{i}xxxx", _route("fallback", [
                {"label": "HD-static1", "ok": False, "bytes": 0, "paid": False, "error": "HTTP Error 403"},
                {"label": "fallback", "ok": True, "bytes": 10_000_000, "paid": True}]),
                "https://www.youtube.com/watch?v=x")
    asyncio.run(go())
    assert len(sent) == 1                      # the other three wait for the cooldown
    assert "DataImpulse" in sent[0] and "10.0 MB" in sent[0] and "HD-static1: HTTP Error 403" in sent[0]
    assert proxy_ledger._pending["count"] == 3


def test_host_of_strips_credentials_and_paths():
    assert proxy_ledger.host_of("https://user:pw@www.youtube.com/watch?v=1") == "www.youtube.com"
    assert proxy_ledger.host_of(None) is None
