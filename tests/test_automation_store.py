"""File store for the autopilot: settings, dedup, schedule and the outbox."""
import pytest

import automation as A


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "AUTOMATION_DIR", str(tmp_path / "automation"))
    return A


def test_defaults_and_roundtrip(store):
    assert store.get_settings()["run_at"] == "08:00"
    assert store.get_settings()["run_until"] == ""      # habis hari
    assert store.get_settings()["enabled"] is False
    saved = store.save_settings({"enabled": True, "run_at": "07:15",
                                 "timezone": "Asia/Jakarta"})
    assert saved["run_at"] == "07:15"
    assert store.get_settings()["enabled"] is True


def test_settings_validation(store):
    for bad in ({"run_at": "25:00"}, {"run_at": "x"}, {"timezone": "Mars/Olympus"},
                {"delivery": {"headers": [{"name": "X: Y", "value": "v"}]}},
                {"delivery": {"headers": "nope"}}, {"nope": 1}):
        with pytest.raises(ValueError):
            store.save_settings(bad)


def test_delivery_headers_saved(store):
    saved = store.save_settings({"delivery": {
        "url": "https://api.test/hook",
        "headers": [{"name": "Authorization", "value": "Bearer t"},
                    {"name": "", "value": "dropped"}],
        "secret": "shh", "file_field": "zip"}})
    delivery = saved["delivery"]
    assert delivery["url"] == "https://api.test/hook"
    assert delivery["headers"] == [{"name": "Authorization", "value": "Bearer t"}]
    assert delivery["secret"] == "shh" and delivery["file_field"] == "zip"


def test_subscription_dedup_and_removal(store):
    sub = store.add_subscription("UC" + "a" * 22, title="Chan")
    again = store.add_subscription("UC" + "a" * 22, title="Other")
    assert again["id"] == sub["id"] and len(store.list_subscriptions()) == 1
    store.add_pending({"video_id": "v1", "channel_id": sub["channel_id"]})
    assert store.remove_subscription(sub["id"])["id"] == sub["id"]
    assert store.list_subscriptions() == [] and store.list_pending() == []


def test_pending_dedup_and_keeps_finished_jobs_on_unsubscribe(store):
    sub = store.add_subscription("UC" + "e" * 22)
    item, created = store.add_pending({"video_id": "v1", "channel_id": sub["channel_id"]})
    assert created is True
    again, created2 = store.add_pending({"video_id": "v1"})
    assert created2 is False and again["video_id"] == item["video_id"]
    assert len(store.list_pending()) == 1
    store.mark_queued("v1", "job-1")
    store.remove_subscription(sub["id"])
    assert len(store.list_pending()) == 1  # History owns it now


def test_failure_ladder_reaches_failed_then_resets(store):
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "360p only")
    assert store.find_pending("v1")["status"] == "retry"
    assert store.find_pending("v1")["attempts"] == 1
    store.update_pending("v1", next_attempt_at=0)
    assert [p["video_id"] for p in store.pending_due_retries()] == ["v1"]
    store.mark_pending_failure("v1", "again")
    store.mark_pending_failure("v1", "third")
    assert store.find_pending("v1")["status"] == "failed"
    store.reset_pending("v1")
    assert store.find_pending("v1")["status"] == "new"


def test_permanent_failure_skips(store):
    store.add_pending({"video_id": "v1"})
    store.mark_pending_failure("v1", "too short", permanent=True)
    assert store.find_pending("v1")["status"] == "skip"


def test_job_finished_marks_done(store):
    store.add_pending({"video_id": "v1"})
    store.mark_queued("v1", "job-9")
    store.mark_job_finished("job-9", True)
    assert store.find_pending("v1")["status"] == "done"


# is_due() dihapus di Tahap 5: penjadwalannya berubah dari "sekali sehari"
# menjadi "kuras antrean satu per satu selama jam operasional". Tesnya
# pindah ke tests/test_automation_window.py, bersama tes in_window() dan
# next_to_start() yang menggantikannya.


def test_outbox_lifecycle(store):
    state = {"job_id": "job-1", "status": "pending", "attempts": 0,
             "next_attempt_at": 0, "created_at": "2026-09-11T00:00:00+00:00"}
    store.outbox_put("job-1", state)
    assert [s["job_id"] for s in store.outbox_list()] == ["job-1"]
    assert [s["job_id"] for s in store.outbox_due()] == ["job-1"]
    state.update(status="sent", attempts=1)
    store.outbox_put("job-1", state)
    assert store.outbox_due() == []
    assert store.outbox_remove("job-1") is True
    assert store.outbox_list() == []


def test_outbox_gives_up_after_three_attempts(store):
    store.outbox_put("job-1", {"job_id": "job-1", "status": "pending",
                               "attempts": 3, "next_attempt_at": 0})
    assert store.outbox_due() == []


def test_corrupt_file_is_set_aside(store):
    store.ensure()
    with open(store._path("pending.json"), "w") as f:
        f.write("{not json")
    assert store.list_pending() == []
    import glob
    assert glob.glob(store._path("pending.json") + ".corrupt-*")
