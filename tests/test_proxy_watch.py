"""Proxy balance watcher: alert on down, nag until topped up, confirm recovery.

Born from the 19-aug-2026 incident: DataImpulse ran out of traffic
(407 TRAFFIC_EXHAUSTED), every YouTube-URL job failed, and nobody was told
more than once. The watcher must keep nagging while the proxy is down and
close the loop when it answers again.
"""
import asyncio

import pytest

from cloud import alerts


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    alerts._proxy_down_since = None
    alerts._proxy_last_nag = 0.0
    alerts._last_alert.clear()
    sent = []

    async def fake_alert(subject, body):
        sent.append(subject)

    monkeypatch.setattr(alerts, "send_admin_alert", fake_alert)
    yield sent
    alerts._proxy_down_since = None
    alerts._proxy_last_nag = 0.0


def _probe_returning(value):
    async def fake_probe():
        return value
    return fake_probe


class TestProxyWatchTick:
    def test_no_proxy_configured_is_silent(self, _reset_state, monkeypatch):
        monkeypatch.setattr(alerts, "_probe_proxy", _probe_returning(None))
        _run(alerts.proxy_watch_tick())
        assert _reset_state == []
        assert alerts._proxy_down_since is None

    def test_healthy_proxy_is_silent(self, _reset_state, monkeypatch):
        monkeypatch.setattr(alerts, "_probe_proxy", _probe_returning((True, "")))
        _run(alerts.proxy_watch_tick())
        assert _reset_state == []

    def test_first_failure_alerts_once(self, _reset_state, monkeypatch):
        monkeypatch.setattr(
            alerts, "_probe_proxy",
            _probe_returning((False, "HTTP 407 TRAFFIC_EXHAUSTED")))
        _run(alerts.proxy_watch_tick())
        _run(alerts.proxy_watch_tick())  # next probe, renotify window not due
        assert len(_reset_state) == 1
        assert "DOWN" in _reset_state[0]
        assert alerts._proxy_down_since is not None

    def test_nags_again_after_renotify_window(self, _reset_state, monkeypatch):
        monkeypatch.setattr(
            alerts, "_probe_proxy", _probe_returning((False, "HTTP 407")))
        _run(alerts.proxy_watch_tick())
        # Pretend the last nag was over the renotify window ago.
        alerts._proxy_last_nag -= alerts._PROXY_RENOTIFY + 1
        _run(alerts.proxy_watch_tick())
        assert len(_reset_state) == 2
        assert "STILL down" in _reset_state[1]

    def test_recovery_confirms_and_resets(self, _reset_state, monkeypatch):
        monkeypatch.setattr(
            alerts, "_probe_proxy", _probe_returning((False, "HTTP 407")))
        _run(alerts.proxy_watch_tick())
        monkeypatch.setattr(alerts, "_probe_proxy", _probe_returning((True, "")))
        _run(alerts.proxy_watch_tick())
        assert any("recovered" in s for s in _reset_state)
        assert alerts._proxy_down_since is None
        # A later failure is a NEW incident and alerts again.
        monkeypatch.setattr(
            alerts, "_probe_proxy", _probe_returning((False, "HTTP 407")))
        _run(alerts.proxy_watch_tick())
        assert sum("DOWN" in s for s in _reset_state) == 2


class TestJobFailureOpensIncident:
    def test_proxy_job_error_opens_incident_and_alerts(self, _reset_state):
        _run(alerts.record_job_outcome(
            False,
            "yt_dlp.utils.DownloadError: Unable to connect to proxy "
            "('Tunnel connection failed: 407 TRAFFIC_EXHAUSTED')"))
        assert alerts._proxy_down_since is not None
        assert len(_reset_state) == 1

    def test_non_proxy_error_leaves_incident_closed(self, _reset_state):
        _run(alerts.record_job_outcome(False, "ffmpeg exploded"))
        assert alerts._proxy_down_since is None
