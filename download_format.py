"""yt-dlp format selectors for YouTube (and other yt-dlp) downloads.

Uncapped attempts take the highest available source, matching
opensource-clipping: VP9 is allowed (YouTube's sharp 1440p/4K streams), AV1
is skipped because OpenCV/FFmpeg software fallbacks choke on it.

The paid per-GB proxy still caps at 720p — that limit is a bandwidth cost
control, not a quality preference. DOWNLOAD_SOURCE_HEIGHT pins a ceiling on
uncapped attempts (``max`` or a pixel height; 1080 is the old default).
"""
import os

_NO_AV1 = "[vcodec!*=av01]"
_MAX = f"bestvideo{_NO_AV1}+bestaudio/best{_NO_AV1}"


def download_source_height():
    """``max`` or an int height from DOWNLOAD_SOURCE_HEIGHT (default ``max``)."""
    raw = (os.environ.get("DOWNLOAD_SOURCE_HEIGHT") or "").strip()
    if not raw or raw.lower() == "max":
        return "max"
    try:
        height = int(raw)
    except ValueError:
        return "max"
    return "max" if height <= 0 else height


def youtube_download_format(capped=False, max_height="max"):
    """yt-dlp ``format`` string. ``capped`` forces 720p for the paid proxy."""
    if capped:
        max_height = 720
    if max_height == "max":
        return _MAX
    try:
        h_val = int(max_height)
    except (ValueError, TypeError):
        return _MAX
    if h_val <= 0:
        return _MAX
    # Same slash-chain as opensource-clipping: prefer native MP4+m4a at
    # standard heights, then any non-AV1 merge, then a single-file fallback.
    if h_val <= 1080:
        return (
            f"bestvideo[height<=?{h_val}][ext=mp4]{_NO_AV1}+bestaudio[ext=m4a]/"
            f"bestvideo[height<=?{h_val}]{_NO_AV1}+bestaudio/"
            f"best[height<=?{h_val}][ext=mp4]{_NO_AV1}/"
            f"best[height<=?{h_val}]{_NO_AV1}"
        )
    return (
        f"bestvideo[height<=?{h_val}]{_NO_AV1}+bestaudio/"
        f"best[height<=?{h_val}]{_NO_AV1}"
    )


_NODE_IPC_KEYS = ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE")


def scrub_node_ipc_env(env=None):
    """Drop PM2/Node IPC fds so Deno (yt-dlp EJS) can start.

    Under PM2 the job inherits ``NODE_CHANNEL_FD``. Deno then dies with
    ``fd is not from BiPipe`` and YouTube lists no video formats. A shell
    ``yt-dlp -F`` works because bash does not set that variable.
    """
    target = os.environ if env is None else env
    for key in _NODE_IPC_KEYS:
        target.pop(key, None)
    return target
