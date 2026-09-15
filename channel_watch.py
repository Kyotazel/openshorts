"""YouTube channel detection: resolve a channel, WebSub push, RSS fallback.

WebSub (PubSubHubbub) is YouTube's only real 'webhook': register the channel's
Atom feed with the hub and it POSTs the feed to us when something is published.
It is best-effort -- notifications can be late or dropped -- so the RSS feed is
also polled on a timer and both paths feed the same video_id dedup.

Network helpers take an optional client so tests can drive them with a stub
instead of reaching YouTube.
"""
import html
import re
from xml.etree import ElementTree as ET

import httpx

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
WATCH_URL = "https://www.youtube.com/watch?v={}"

# A plain bot UA gets a consent page from youtube.com; the channel page fetch
# needs to look like a browser. The hub and the feed do not care.
PAGE_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36")
FEED_USER_AGENT = "OpenShorts-Automation/1.0"

_CHANNEL_ID_RE = re.compile(r"(UC[0-9A-Za-z_-]{22})")
_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[0-9A-Za-z_-]{22})"')
_ITEMPROP_ID_RE = re.compile(
    r'itemprop="channelId"\s+content="(UC[0-9A-Za-z_-]{22})"')
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"')

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"


def _client(client=None):
    if client is not None:
        return client, False
    return httpx.Client(timeout=20, follow_redirects=True), True


def is_channel_id(text: str) -> bool:
    return bool(re.fullmatch(r"UC[0-9A-Za-z_-]{22}", (text or "").strip()))


def extract_channel_id(text: str):
    match = _CHANNEL_ID_RE.search(text or "")
    return match.group(1) if match else None


def _extract_handle(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("@"):
        return text
    match = re.search(r"youtube\.com/(@[^/?#]+)", text)
    return match.group(1) if match else ""


def _og_title_match(page: str) -> str:
    match = _OG_TITLE_RE.search(page or "")
    return html.unescape(match.group(1).strip()) if match else ""


def resolve(query: str, client=None) -> dict:
    """Turn a channel URL or @handle into {channel_id, title, handle}."""
    query = (query or "").strip()
    if not query:
        raise ValueError("channel URL or handle is required")
    direct = extract_channel_id(query)
    if direct and (is_channel_id(query) or "/channel/" in query):
        feed = fetch_feed(direct, client=client)
        return {"channel_id": direct, "title": feed["title"],
                "handle": _extract_handle(query)}

    url = query if query.startswith("http") else \
        f"https://www.youtube.com/@{query.lstrip('@')}"
    http, own = _client(client)
    try:
        resp = http.get(url, headers={"User-Agent": PAGE_USER_AGENT,
                                      "Accept-Language": "en-US,en;q=0.9"})
        resp.raise_for_status()
        page = resp.text
    finally:
        if own:
            http.close()

    channel_id = None
    for pattern in (_EXTERNAL_ID_RE, _ITEMPROP_ID_RE, _CHANNEL_ID_RE):
        match = pattern.search(page)
        if match:
            channel_id = match.group(1)
            break
    if not channel_id:
        raise ValueError("Could not find a channel on that page. "
                         "Paste the channel URL or use /channel/UC...")
    return {"channel_id": channel_id, "title": _og_title_match(page),
            "handle": _extract_handle(query)}


def feed_url(channel_id: str) -> str:
    return FEED_URL.format(channel_id)


def parse_feed(xml_text: str) -> dict:
    """Parse an Atom feed into {title, entries}.

    Entry fields: video_id, channel_id, title, published (ISO), url.
    """
    root = ET.fromstring(xml_text)
    channel_title = ""
    title_el = root.find(f"{_ATOM}title")
    if title_el is not None and title_el.text:
        channel_title = title_el.text.strip()

    entries = []
    for entry in root.findall(f"{_ATOM}entry"):
        video_id = (entry.findtext(f"{_YT}videoId") or "").strip()
        if not video_id:
            continue
        link_el = entry.find(f"{_ATOM}link")
        href = link_el.get("href") if link_el is not None else ""
        entries.append({
            "video_id": video_id,
            "channel_id": (entry.findtext(f"{_YT}channelId") or "").strip(),
            "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
            "published": (entry.findtext(f"{_ATOM}published") or "").strip(),
            "url": href or WATCH_URL.format(video_id),
        })
    return {"title": channel_title, "entries": entries}


def fetch_feed(channel_id: str, client=None) -> dict:
    http, own = _client(client)
    try:
        resp = http.get(feed_url(channel_id),
                        headers={"User-Agent": FEED_USER_AGENT})
        resp.raise_for_status()
        return parse_feed(resp.text)
    finally:
        if own:
            http.close()


def websub_subscribe(callback_url: str, channel_id: str, client=None,
                     lease_seconds: int = 432000) -> bool:
    """Register (or renew) the channel's feed with the hub. 202/204 = accepted."""
    return _hub_call("subscribe", callback_url, channel_id, client=client,
                     lease_seconds=lease_seconds)


def websub_unsubscribe(callback_url: str, channel_id: str, client=None) -> bool:
    return _hub_call("unsubscribe", callback_url, channel_id, client=client)


def _hub_call(mode: str, callback_url: str, channel_id: str, client=None,
              lease_seconds=None) -> bool:
    data = {
        "hub.mode": mode,
        "hub.topic": feed_url(channel_id),
        "hub.callback": callback_url,
        "hub.verify": "async",
    }
    if lease_seconds:
        data["hub.lease_seconds"] = str(int(lease_seconds))
    http, own = _client(client)
    try:
        resp = http.post(HUB_URL, data=data,
                         headers={"User-Agent": FEED_USER_AGENT})
        if resp.status_code in (202, 204):
            return True
        print(f"WARNING: WebSub {mode} for {channel_id}: HTTP {resp.status_code} "
              f"{resp.text[:200]}")
        return False
    finally:
        if own:
            http.close()
