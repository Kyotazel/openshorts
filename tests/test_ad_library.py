import os

import pytest

import ad_library as lib


@pytest.fixture
def libdir(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "library_dir", lambda: str(tmp_path))
    monkeypatch.setattr(lib, "probe_duration", lambda path: 3.2)
    return tmp_path


def _src(dirpath, name="in.mp4"):
    p = dirpath / name
    p.write_bytes(b"not-a-real-mp4")
    return str(p)


def test_first_upload_becomes_active(libdir):
    item = lib.add_item(_src(libdir, "in.mp4"), "autoaudit.mp4")
    man = lib.read_manifest()
    assert man["active_id"] == item["id"]
    assert item["duration"] == 3.2
    assert os.path.isfile(os.path.join(str(libdir), item["filename"]))


def test_second_upload_does_not_steal_active(libdir):
    a = lib.add_item(_src(libdir, "a.mp4"), "a.mp4")
    b = lib.add_item(_src(libdir, "b.mp4"), "b.mp4")
    man = lib.read_manifest()
    assert man["active_id"] == a["id"]
    assert {it["id"] for it in man["items"]} == {a["id"], b["id"]}
    lib.activate(b["id"])
    assert lib.read_manifest()["active_id"] == b["id"]
    lib.delete_item(b["id"])
    man = lib.read_manifest()
    assert man["active_id"] is None
    assert len(man["items"]) == 1
    assert man["items"][0]["id"] == a["id"]


def test_rejects_non_video_name(libdir):
    with pytest.raises(lib.BadAdFile):
        lib.add_item(_src(libdir, "notes.txt"), "notes.txt")


def test_activate_unknown_raises(libdir):
    with pytest.raises(KeyError):
        lib.activate("nope")


def test_library_dir_is_not_under_output():
    path = lib.library_dir()
    assert os.path.basename(path) == "ad_library"
    assert os.path.basename(os.path.dirname(path)) != "output"


def test_insert_ad_env_defaults():
    assert lib.insert_ad_env(None, True) == {"INSERT_AD": "1"}
    assert lib.insert_ad_env(None, False) == {"INSERT_AD": "0"}
    assert lib.insert_ad_env(False, True) == {"INSERT_AD": "0"}
    assert lib.insert_ad_env(True, False) == {"INSERT_AD": "1"}


def test_app_source_wires_ad_library():
    """Does not import FastAPI; CI without local deps still sees the routes."""
    src = open("app.py", encoding="utf-8").read()
    assert '@app.get("/api/ad-library")' in src
    assert '@app.post("/api/ad-library")' in src
    assert "insert_ad" in src
    assert '"adLibrary"' in src
    assert "BILLING_ENABLED" in src[src.index("def _ad_library_guard"):src.index("def _ad_library_guard") + 200]
