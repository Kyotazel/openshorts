"""ZIP packaging and the multipart delivery request."""
import asyncio
import json
import zipfile

import pytest

import automation_delivery as D
import security_utils


def _fake_clip(path, data=b"video-bytes"):
    path.write_bytes(data)
    return str(path)


def test_caption_records_map_the_dashboard_fields():
    records = D.caption_records([
        {"video_title_for_youtube_short": "Title",
         "video_description_for_tiktok": "TT #tag",
         "video_description_for_instagram": "IG #tag",
         "viral_hook_text": "Hook"},
        {},
    ])
    assert records[0] == {"index": 1, "title": "Title", "tiktok": "TT #tag",
                          "instagram": "IG #tag", "hook": "Hook"}
    assert records[1]["index"] == 2 and records[1]["title"] == ""


def test_build_zip_contains_clips_captions_and_metadata(tmp_path):
    clip = _fake_clip(tmp_path / "job_clip_1.mp4")
    meta = tmp_path / "job_metadata.json"
    meta.write_text(json.dumps({"shorts": [{"title": "x"}]}))
    dest = tmp_path / "outbox" / "job.zip"
    size = D.build_zip(str(dest), [(0, clip)],
                       [{"video_description_for_tiktok": "TT"}], str(meta))
    assert size == dest.stat().st_size
    with zipfile.ZipFile(dest) as zf:
        names = sorted(zf.namelist())
        assert names == ["captions.json", "clip_01_job_clip_1.mp4", "metadata.json"]
        captions = json.loads(zf.read("captions.json"))
        assert captions[0]["tiktok"] == "TT"


def test_sign_file_is_stable_and_secret_dependent(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello world")
    first = D.sign_file(str(path), "s1")
    assert first == D.sign_file(str(path), "s1")
    assert first != D.sign_file(str(path), "s2")


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code, self.text = status_code, text


class FakeClient:
    status_code = 200
    body = ""
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, data=None, files=None, headers=None):
        field, (name, fh, ctype) = next(iter(files.items()))
        FakeClient.last = {"url": url, "data": data, "headers": headers,
                           "field": field, "name": name, "ctype": ctype,
                           "bytes": fh.read()}
        return FakeResponse(FakeClient.status_code, FakeClient.body)


@pytest.fixture()
def zip_file(tmp_path):
    path = tmp_path / "job.zip"
    path.write_bytes(b"zip-bytes")
    return str(path)


@pytest.fixture(autouse=True)
def allow_urls(monkeypatch):
    monkeypatch.setattr(security_utils, "assert_public_url", lambda url: url)
    monkeypatch.setattr(D.httpx, "AsyncClient", FakeClient)
    FakeClient.status_code = 200
    FakeClient.body = ""
    FakeClient.last = None


def test_post_zip_sends_multipart_with_fields_headers_and_signature(zip_file):
    ok, detail = asyncio.run(D.post_zip(
        "https://api.test/hook", zip_file, file_field="zip",
        headers=[{"name": "Authorization", "value": "Bearer t"}],
        secret="shh", fields={"job_id": "j1", "clip_count": 3},
        timeout=5))
    assert ok is True and detail == ""
    sent = FakeClient.last
    assert sent["url"] == "https://api.test/hook"
    assert sent["field"] == "zip" and sent["name"] == "job.zip"
    assert sent["bytes"] == b"zip-bytes"
    assert sent["data"] == {"job_id": "j1", "clip_count": "3"}
    assert sent["headers"]["Authorization"] == "Bearer t"
    assert sent["headers"][D.SIGNATURE_HEADER].startswith("sha256=")


def test_post_zip_reports_http_failure(zip_file):
    FakeClient.status_code = 500
    FakeClient.body = "boom"
    ok, detail = asyncio.run(D.post_zip("https://api.test/hook", zip_file))
    assert ok is False and "500" in detail and "boom" in detail


def test_post_zip_without_url_is_an_error(zip_file):
    ok, detail = asyncio.run(D.post_zip("", zip_file))
    assert ok is False and "not configured" in detail


def test_post_zip_allows_a_private_target_when_opted_in(zip_file, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ALLOW_PRIVATE_TARGET", "1")
    def _boom(url):
        raise security_utils.UnsafeURLError("private host")
    monkeypatch.setattr(security_utils, "assert_public_url", _boom)
    ok, detail = asyncio.run(D.post_zip("http://127.0.0.1:9000/hook", zip_file))
    assert ok is True and detail == ""
    assert FakeClient.last["url"] == "http://127.0.0.1:9000/hook"


def test_post_zip_rejects_unsafe_target(zip_file, monkeypatch):
    # Suite ini mengimpor app.py, yang menjalankan load_dotenv(): kalau .env
    # pengembang memuat AUTOMATION_ALLOW_PRIVATE_TARGET=1 (wajib untuk
    # menguji kirim ke 127.0.0.1 di lokal), penjaga SSRF ikut mati dan tes
    # ini lulus/gagal tergantung isi .env. Hapus dulu supaya tegas.
    monkeypatch.delenv("AUTOMATION_ALLOW_PRIVATE_TARGET", raising=False)
    def _boom(url):
        raise security_utils.UnsafeURLError("private host")
    monkeypatch.setattr(security_utils, "assert_public_url", _boom)
    ok, detail = asyncio.run(D.post_zip("http://127.0.0.1/hook", zip_file))
    assert ok is False and "unsafe" in detail
