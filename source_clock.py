"""Clock strings and YouTube t= for source windows. Seconds everywhere else."""
import re


class BadClock(ValueError):
    """Unparseable clock or t= value."""


def parse_clock(text):
    """Dashboard clock → seconds. ``12:45`` → 765, ``1:12:45`` → 4365, ``765`` → 765."""
    raw = (text or "").strip()
    if not raw:
        raise BadClock("empty")
    if "." in raw:
        raise BadClock("use colons, not dots")
    if re.fullmatch(r"\d+", raw):
        return float(raw)
    parts = raw.split(":")
    if not (2 <= len(parts) <= 3):
        raise BadClock("use mm:ss or h:mm:ss")
    try:
        nums = [int(p, 10) for p in parts]
    except ValueError as e:
        raise BadClock("not a clock") from e
    if any(n < 0 for n in nums):
        raise BadClock("negative")
    if len(nums) == 2:
        minutes, seconds = nums
        hours = 0
    else:
        hours, minutes, seconds = nums
    if minutes >= 60 or seconds >= 60:
        raise BadClock("minute/second must be < 60")
    return float(hours * 3600 + minutes * 60 + seconds)


def parse_youtube_t(url):
    """Seconds from ``t`` / ``start`` query, or None. Does not parse ``source_end``."""
    if not url:
        return None
    m = re.search(r"[?&](?:t|start)=([^&#]+)", url)
    if not m:
        return None
    token = m.group(1).strip()
    hm = re.fullmatch(
        r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", token, flags=re.I)
    if hm and any(hm.group(i) for i in (1, 2, 3)):
        h = int(hm.group(1) or 0)
        mi = int(hm.group(2) or 0)
        s = int(hm.group(3) or 0)
        return float(h * 3600 + mi * 60 + s)
    token = token.rstrip("sS")
    try:
        return parse_clock(token)
    except BadClock:
        return None
