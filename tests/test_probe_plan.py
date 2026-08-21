"""Duration-probe proxy ordering: cheapest bandwidth first.

The probe runs on every managed YouTube submission. It read PROXY_URL only
until 21-aug-2026, so it kept paying the per-GB proxy for metadata long after
downloads had moved to the flat-rate static pool — invisibly, because the
PROXY_BYTES counter only sees download bytes.
"""
import pytest

metering = pytest.importorskip("cloud.metering")

plan = metering.plan_probe_proxies

STATICS = ["http://s1", "http://s2", "http://s3"]
PAID = "http://paid"


class TestOrdering:
    def test_full_chain(self):
        assert plan(True, STATICS, PAID) == [None] + STATICS + [PAID]

    def test_paid_is_last_resort(self):
        got = plan(False, STATICS, PAID)
        assert got == STATICS + [PAID]
        assert got.index(PAID) == len(got) - 1

    def test_statics_only_never_reaches_a_paid_proxy(self):
        assert plan(False, STATICS, "") == STATICS

    def test_selfhost_no_proxies_probes_direct(self):
        assert plan(False, [], "") == [None]
        assert plan(True, [], "") == [None]

    def test_no_statics_matches_legacy_paid_only_behavior(self):
        assert plan(False, [], PAID) == [PAID]
