"""HTTP tests for the AutoAudit ad-library routes."""
import asyncio
import os

import httpx
import pytest

app_module = pytest.importorskip("app")
ads = pytest.importorskip("ad_library")


def _request(method, path, **kwargs):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(_do())


def test_config_exposes_ad_library_flag():
    resp = _request("GET", "/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "adLibrary" in body
    assert body["adLibrary"] is (not app_module.BILLING_ENABLED)


def test_ad_library_404_when_billing(monkeypatch):
    monkeypatch.setattr(app_module, "BILLING_ENABLED", True)
    resp = _request("GET", "/api/ad-library")
    assert resp.status_code == 404
    resp = _request("POST", "/api/ad-library/x/activate")
    assert resp.status_code == 404


def test_ad_library_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    monkeypatch.setattr(ads, "library_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ads, "probe_duration", lambda path: 3.2)

    listed = _request("GET", "/api/ad-library")
    assert listed.status_code == 200
    assert listed.json() == {"active_id": None, "items": []}

    added = _request(
        "POST", "/api/ad-library",
        files={"file": ("autoaudit.mp4", b"fake-mp4", "video/mp4")},
    )
    assert added.status_code == 200
    item = added.json()
    assert item["original_name"] == "autoaudit.mp4"
    assert item["duration"] == 3.2
    assert os.path.isfile(os.path.join(str(tmp_path), item["filename"]))

    listed = _request("GET", "/api/ad-library")
    assert listed.json()["active_id"] == item["id"]

    second = _request(
        "POST", "/api/ad-library",
        files={"file": ("b.mp4", b"fake-mp4-2", "video/mp4")},
    )
    assert second.status_code == 200
    bid = second.json()["id"]
    listed = _request("GET", "/api/ad-library")
    assert listed.json()["active_id"] == item["id"]

    activated = _request("POST", f"/api/ad-library/{bid}/activate")
    assert activated.status_code == 200
    assert activated.json()["active_id"] == bid

    deleted = _request("DELETE", f"/api/ad-library/{bid}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["active_id"] is None
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == item["id"]
