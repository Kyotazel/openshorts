"""Which stretches of a rendered clip use which layout, kept next to the clip.

reframe_v2 decides a layout per scene (TRACK, GENERAL, SPLIT, ...) and renders
it, and nothing downstream used to know. Captions need to: on a SPLIT scene
the two speakers sit one above the other and the seam between the halves is
the emptiest place in the frame, so that is where the captions go (OpusClip
does the same). On every other layout they stay at the bottom.

The ranges travel as a sidecar ``<clip>.layout.json`` written by the render,
because the render happens in four different places (the job pipeline, the
recut/rerender, the manual reframe and the v1 fallback, which writes none)
and a return value would have to be threaded through all of them. Times are
in the CLIP's own timeline, which is also the timeline captions are laid on.
Whoever updates a clip's metadata copies them into ``layout_ranges`` so
/api/subtitle can find them after the sidecar is gone.
"""
import json
import os

SIDECAR_SUFFIX = ".layout.json"


def sidecar_path(video_path):
    return video_path + SIDECAR_SUFFIX


def write(video_path, ranges):
    """``ranges``: iterable of (start_s, end_s, strategy). Best effort: a
    failure here must never fail a render that already succeeded."""
    payload = [{"start": round(float(s), 3), "end": round(float(e), 3),
                "layout": str(layout).lower()} for s, e, layout in ranges]
    try:
        with open(sidecar_path(video_path), "w") as f:
            json.dump({"ranges": payload}, f)
    except OSError:
        pass
    return payload


def read(video_path):
    """The ranges recorded for this file, or [] (no sidecar, v1 render, or
    a file that was never reframed)."""
    try:
        with open(sidecar_path(video_path)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return normalise(data.get("ranges"))


def normalise(ranges):
    """Accept the sidecar/metadata shape or bare tuples; drop anything odd
    (an old metadata file, a hand-edited value) instead of raising."""
    out = []
    for r in ranges or []:
        try:
            if isinstance(r, dict):
                s, e, layout = r["start"], r["end"], r.get("layout", "")
            else:
                s, e, layout = r
            s, e = float(s), float(e)
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            out.append({"start": s, "end": e, "layout": str(layout).lower()})
    return out


def split_ranges(ranges):
    """(start, end) pairs of the stretches where captions belong on the seam."""
    return [(r["start"], r["end"]) for r in normalise(ranges) if r["layout"] == "split"]


def in_split(t, splits):
    return any(s <= t < e for s, e in splits or [])
