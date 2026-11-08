"""ledger.compare: a bypassed probe never takes the span of the call after it.

The app opens its weather-service client on first use with GET /healthz
(stores.py), a path the lineage plugin bypasses by contract — no span. The
real GET /weather follows milliseconds later on the same pod, peer and method,
with one span. The probe's record must stay ledger-only (declared dark), and
the real call must pair with its span."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.live import fleet as fm
from tests.live import ledger

FLEET = fm.load(Path(__file__).parent.parent / "live" / "apps" / "travel_advisor.yaml")
T = "fffaa9154e452f9e14fa91db721a2539"
T0 = datetime(2026, 10, 13, 7, 35, 46, 979489, tzinfo=timezone.utc)
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class _Dg:
    """One request span in T: get-weather → weather-service GET /weather,
    started 13 ms after the probe's t0 and 1 ms after the real call's."""

    def q(self, sql, params=()):
        if "'request'" in sql:
            started = T0 + timedelta(milliseconds=13)
            return [("5ca8484f92f5e7b3", started, started + timedelta(milliseconds=3),
                     {"lineage.self.id": "get-weather", "lineage.direction": "outbound",
                      "lineage.protocol": "http", "lineage.peer.host": "weather-service:9000",
                      "http.method": "GET", "url.path": "/weather"})]
        return []


def _out(xid, path, t0, ts):
    start = {"ledger": 1, "ts": ts, "t0": t0, "kind": "wire", "dir": "out", "event": "start", "method": "GET",
             "host": "weather-service:9000", "path": path, "proto": "http", "xid": xid, "sent_trace": T,
             "ambient_trace": T, "req": {"sha": EMPTY_SHA, "bytes": 0}, "pod": "get-weather",
             "_workload": "get-weather"}
    end = {**start, "event": "end", "ts": ts + 0.001, "status": 200, "req": None}
    return [start, end]


def _records():
    base = T0.timestamp()
    return _out("a302a7e449544fa3", "/healthz", base, base + 0.009) + \
        _out("55c0165ddf1f42ad", "/weather", base + 0.0116, base + 0.0156)


def test_the_probe_stays_dark_and_the_real_call_pairs():
    c = ledger.compare(_Dg(), T, _records(), FLEET)
    assert [(p["pod"], p["host"], p["span_id"]) for p in c["pairs"]] == [
        ("get-weather", "weather-service:9000", "5ca8484f92f5e7b3")]
    assert [(x["path"], x["declared_dark"]) for x in c["ledger_only"]] == [("/healthz", True)]
    assert c["ledger_only_undeclared"] == []
    assert c["table_only"] == []


def test_without_a_path_on_the_span_nothing_changes():
    """A span that carries no url.path (older producer) pairs as before: by
    key and nearest in time — the probe's record, processed first, would take
    it; that is the pre-existing behaviour on path-less spans, not this fix's."""
    assert ledger._same_path("/healthz", None) is True
    assert ledger._same_path(None, "/weather") is True
    assert ledger._same_path("/weather?location=Tokyo", "/weather") is True
    assert ledger._same_path("/healthz", "/weather") is False
