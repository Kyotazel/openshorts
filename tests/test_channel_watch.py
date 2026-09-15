"""YouTube detection: channel resolution, Atom parsing and hub calls."""
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


def test_resolve_channel_url_skips_the_page():
    stub = Stub()
    cid = "UC" + "b" * 22
    info = C.resolve(f"https://www.youtube.com/channel/{cid}", client=stub)
    assert info["channel_id"] == cid and info["title"] == "My Channel"
    assert all("feeds/videos.xml" in call for call in stub.calls)


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


def test_hub_rejection_is_false_not_an_exception():
    assert C.websub_subscribe("https://app.test/cb", "UC" + "b" * 22,
                              client=Stub(hub_status=500)) is False
