"""YouTube detection: channel resolution, Atom parsing and hub calls."""
import json

import pytest

import channel_watch as C

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>My Channel</title>
 <entry>
  <yt:videoId>abc123</yt:videoId>
  <yt:channelId>UCcccccccccccccccccccccc</yt:channelId>
  <title>Hello &amp; welcome</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
  <published>2026-09-11T02:00:00+00:00</published>
 </entry>
</feed>"""


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class Stub:
    def __init__(self, page="", feed=FEED, hub_status=202):
        self.page, self.feed, self.hub_status = page, feed, hub_status
        self.calls = []

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        if "feeds/videos.xml" in url:
            return Resp(self.feed)
        return Resp(self.page)

    def post(self, url, data=None, headers=None, **kw):
        self.calls.append((url, data))
        return Resp("", self.hub_status)


def test_parse_feed():
    parsed = C.parse_feed(FEED)
    assert parsed["title"] == "My Channel"
    entry = parsed["entries"][0]
    assert entry["video_id"] == "abc123"
    assert entry["title"] == "Hello & welcome"
    assert entry["channel_id"] == "UCcccccccccccccccccccccc"
    assert entry["url"].endswith("abc123")


def test_channel_id_helpers():
    cid = "UC" + "c" * 22
    assert C.is_channel_id(cid)
    assert not C.is_channel_id("@handle")
    assert C.extract_channel_id(f"https://www.youtube.com/channel/{cid}") == cid
    assert C.extract_channel_id("@nope") is None


def test_resolve_handle_reads_external_id_and_title():
    page = ('<html><meta property="og:title" content="Resolved &amp; Co">'
            '<script>"externalId":"UC' + "d" * 22 + '"</script></html>')
    stub = Stub(page=page)
    info = C.resolve("@somehandle", client=stub)
    assert info["channel_id"] == "UC" + "d" * 22
    assert info["title"] == "Resolved & Co"
    assert info["handle"] == "@somehandle"


def test_resolve_channel_url_reads_the_page():
    """Dulu jalur ini cukup membaca feed dan tidak menyentuh halaman sama
    sekali. Feed-nya sudah tidak ada, jadi judulnya kini dari halaman."""
    cid = "UC" + "b" * 22
    page = ('<html><meta property="og:title" content="My Channel">'
            f'<script>"externalId":"{cid}"</script></html>')
    stub = Stub(page=page)
    info = C.resolve(f"https://www.youtube.com/channel/{cid}", client=stub)
    assert info["channel_id"] == cid and info["title"] == "My Channel"
    assert not any("feeds/videos.xml" in call for call in stub.calls)


def test_resolve_channel_url_survives_an_unreachable_page():
    """ID-nya sudah dipegang, jadi gagal ambil judul bukan alasan menolak."""
    cid = "UC" + "b" * 22

    class Boom(Stub):
        def get(self, url, headers=None, **kw):
            raise RuntimeError("jaringan mati")

    info = C.resolve(f"https://www.youtube.com/channel/{cid}", client=Boom())
    assert info["channel_id"] == cid and info["title"] == ""


def test_resolve_rejects_a_page_without_a_channel():
    with pytest.raises(ValueError):
        C.resolve("@nope", client=Stub(page="<html>nothing</html>"))


def test_hub_subscribe_and_unsubscribe():
    stub = Stub()
    cid = "UC" + "b" * 22
    assert C.websub_subscribe("https://app.test/cb", cid, client=stub) is True
    assert C.websub_unsubscribe("https://app.test/cb", cid, client=stub) is True
    _, data = stub.calls[-1]
    assert data["hub.mode"] == "unsubscribe"
    assert data["hub.topic"].endswith(cid)


def test_topic_url_is_the_websub_topic_not_the_dead_feed():
    """Topik WebSub ada di /xml/feeds/, bukan /feeds/. Yang kedua 404."""
    cid = "UC" + "b" * 22
    url = C.topic_url(cid)
    assert "/xml/feeds/videos.xml" in url
    assert url.endswith(cid)


def _proc(stdout, stderr=""):
    class P:
        pass
    p = P()
    p.stdout, p.stderr = stdout, stderr
    return p


def test_list_uploads_parses_ytdlp_json(monkeypatch):
    baris = json.dumps({"id": "VFbZ1NmDIKs", "title": "Testing",
                        "timestamp": 1789541400,
                        "url": "https://www.youtube.com/watch?v=VFbZ1NmDIKs"})
    monkeypatch.setattr(C.subprocess, "run",
                        lambda *a, **k: _proc(baris + "\n", "WARNING: noise\n"))
    cid = "UC" + "b" * 22
    entry = C.list_uploads(cid)["entries"][0]
    assert entry["video_id"] == "VFbZ1NmDIKs"
    assert entry["title"] == "Testing"
    assert entry["published"].startswith("2026-09-16")
    assert entry["channel_id"] == cid


def test_list_uploads_without_a_date_still_returns_the_entry(monkeypatch):
    """approximate_date tidak selalu tersedia; entri tetap harus terbaca."""
    monkeypatch.setattr(C.subprocess, "run",
                        lambda *a, **k: _proc(json.dumps(
                            {"id": "abc", "title": "T"}) + "\n"))
    entry = C.list_uploads("UC" + "b" * 22)["entries"][0]
    assert entry["video_id"] == "abc" and entry["published"] == ""


def test_list_uploads_survives_a_missing_ytdlp(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("yt-dlp tidak ada")

    monkeypatch.setattr(C.subprocess, "run", boom)
    assert C.list_uploads("UC" + "b" * 22) == {"title": "", "entries": []}


def test_hub_retries_a_transient_503(monkeypatch):
    """Hub menjawab 503 lebih sering daripada 202; sekali coba tidak cukup."""
    monkeypatch.setattr(C.time, "sleep", lambda _s: None)
    percobaan = []

    class Flaky(Stub):
        def post(self, url, data=None, headers=None, **kw):
            percobaan.append(1)
            return Resp("Transient error", 503 if len(percobaan) < 2 else 202)

    assert C.websub_subscribe("https://app.test/cb", "UC" + "b" * 22,
                              client=Flaky()) is True
    assert len(percobaan) == 2


def test_hub_rejection_is_false_not_an_exception():
    assert C.websub_subscribe("https://app.test/cb", "UC" + "b" * 22,
                              client=Stub(hub_status=500)) is False
