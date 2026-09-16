"""YouTube channel detection: resolve a channel, WebSub push, yt-dlp poll.

WebSub (PubSubHubbub) is YouTube's only real 'webhook': register the channel's
topic with the hub and it POSTs the Atom feed to us when something is
published. It is best-effort -- the hub answers 503 often enough that every
call has to be retried -- so the channel is also polled on a timer and both
paths feed the same video_id dedup.

KENAPA POLLING-NYA PAKAI yt-dlp, BUKAN RSS: feed channel YouTube
(youtube.com/feeds/videos.xml?channel_id=...) SUDAH TIDAK ADA. Diperiksa
16 Sep 2026: ia menjawab 404 untuk SEMUA channel -- termasuk channel milik
YouTube sendiri -- dari tiga jaringan berbeda. Yang tersisa hanya URL topik
WebSub (/xml/feeds/videos.xml), dan itu berkas statis berisi keterangan,
bukan feed. yt-dlp sudah jadi dependensi repo ini dan tidak butuh kunci API,
jadi ia yang mengambil alih. parse_feed() tetap ada karena hub masih
mengirim Atom ke callback kita.

Network helpers take an optional client so tests can drive them with a stub
instead of reaching YouTube.
"""
import html
import json
import re
import subprocess
import sys
import time
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
# URL TOPIK untuk hub, bukan feed yang bisa dibaca. YouTube sendiri yang
# menyebutkannya di berkas statis /xml/feeds/videos.xml: "The corresponding
# feed (identified in the <link rel="self"/> tag) should be used as a topic
# on the http://pubsubhubbub.appspot.com hub".
TOPIC_URL = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={}"
WATCH_URL = "https://www.youtube.com/watch?v={}"

# Hub menjawab 503 "Transient error" jauh lebih sering daripada 202 saat
# diukur 16 Sep 2026 (3 dari 4 percobaan), jadi sekali coba tidak cukup.
HUB_ATTEMPTS = 3
HUB_RETRY_DELAY = 2

YTDLP_LIMIT = 5
YTDLP_TIMEOUT = 60

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
    punya_id = bool(direct) and (is_channel_id(query) or "/channel/" in query)
    if punya_id:
        # Dulu di sini cukup membaca feed; judulnya diambil dari sana. Feed
        # itu sudah tidak ada, dan halaman channel satu-satunya sumber judul
        # yang tersisa - jadi jalur ini sekarang ikut mengambil halaman.
        url = f"https://www.youtube.com/channel/{direct}"
    else:
        url = query if query.startswith("http") else \
            f"https://www.youtube.com/@{query.lstrip('@')}"

    http, own = _client(client)
    try:
        resp = http.get(url, headers={"User-Agent": PAGE_USER_AGENT,
                                      "Accept-Language": "en-US,en;q=0.9"})
        resp.raise_for_status()
        page = resp.text
    except Exception:
        # ID-nya sudah kita pegang, jadi gagal ambil judul bukan alasan untuk
        # menolak channelnya. Jalur handle tetap harus punya halaman.
        if not punya_id:
            raise
        return {"channel_id": direct, "title": "", "handle": _extract_handle(query)}
    finally:
        if own:
            http.close()

    channel_id = direct if punya_id else None
    for pattern in (_EXTERNAL_ID_RE, _ITEMPROP_ID_RE, _CHANNEL_ID_RE):
        if channel_id:
            break
        match = pattern.search(page)
        if match:
            channel_id = match.group(1)
    if not channel_id:
        raise ValueError("Could not find a channel on that page. "
                         "Paste the channel URL or use /channel/UC...")
    return {"channel_id": channel_id, "title": _og_title_match(page),
            "handle": _extract_handle(query)}


def topic_url(channel_id: str) -> str:
    """URL topik untuk hub. Bukan alamat yang bisa dibaca sebagai feed."""
    return TOPIC_URL.format(channel_id)


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


def _iso_from_timestamp(value) -> str:
    """Unix detik -> ISO UTC. Kosong kalau tidak ada, supaya penyaring tanggal
    memperlakukan video tanpa tanggal sebagai "tidak diketahui" alih-alih
    membuangnya karena kesalahan parsing."""
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def list_uploads(channel_id: str, limit: int = YTDLP_LIMIT,
                 timeout: int = YTDLP_TIMEOUT) -> dict:
    """Ambil upload terbaru sebuah channel lewat yt-dlp.

    Mengembalikan bentuk yang sama dengan parse_feed: {title, entries}, supaya
    pemanggilnya tidak perlu tahu dari mana daftarnya datang.

    approximate_date diminta eksplisit: tanpa itu tab channel tidak
    menyertakan tanggal sama sekali (timestamp=None), dan penyaring "hanya
    video yang terbit setelah langganan" kehilangan bahannya.
    """
    cmd = [sys.executable, "-m", "yt_dlp",
           "--flat-playlist", "--playlist-end", str(int(limit)),
           "--dump-json", "--no-warnings", "--ignore-errors",
           "--extractor-args", "youtubetab:approximate_date",
           f"https://www.youtube.com/channel/{channel_id}/videos"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"WARNING: yt-dlp gagal untuk {channel_id}: {e}")
        return {"title": "", "entries": []}

    entries = []
    for baris in (proc.stdout or "").splitlines():
        baris = baris.strip()
        if not baris.startswith("{"):
            continue
        try:
            data = json.loads(baris)
        except ValueError:
            continue
        video_id = str(data.get("id") or "").strip()
        if not video_id:
            continue
        entries.append({
            "video_id": video_id,
            "channel_id": channel_id,
            "title": str(data.get("title") or "").strip(),
            "published": _iso_from_timestamp(data.get("timestamp")),
            "url": str(data.get("url") or WATCH_URL.format(video_id)),
        })
    if not entries:
        ekor = (proc.stderr or "").strip().splitlines()
        print(f"WARNING: yt-dlp tidak menemukan video untuk {channel_id}"
              + (f": {ekor[-1][:160]}" if ekor else ""))
    return {"title": "", "entries": entries}


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
        "hub.topic": topic_url(channel_id),
        "hub.callback": callback_url,
        "hub.verify": "async",
    }
    if lease_seconds:
        data["hub.lease_seconds"] = str(int(lease_seconds))
    http, own = _client(client)
    try:
        resp = None
        for percobaan in range(HUB_ATTEMPTS):
            resp = http.post(HUB_URL, data=data,
                             headers={"User-Agent": FEED_USER_AGENT})
            if resp.status_code in (202, 204):
                return True
            if resp.status_code not in (429, 503):
                break
            if percobaan < HUB_ATTEMPTS - 1:
                time.sleep(HUB_RETRY_DELAY)
        print(f"WARNING: WebSub {mode} for {channel_id}: HTTP {resp.status_code} "
              f"{resp.text[:200]}")
        return False
    finally:
        if own:
            http.close()
