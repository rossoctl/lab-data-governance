"""ledger.compare pairs a store act made over HTTP with the sidecar's span."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.live import fleet as fm
from tests.live import ledger

FLEET = fm.load(Path(__file__).parent.parent / "live" / "apps" / "travel_advisor.yaml")
T = "b86c1786b41fd817f2c2c018f64ab4a1"
T0 = datetime(2026, 10, 12, 13, 29, 38, tzinfo=timezone.utc)


class _Dg:
    """Two spans in T: get-flights → minio (the S3 read, http) and
    get-flights' inbound tools/call; no derived rows. *extra* adds spans;
    *without_store_span* drops the minio one (a kit that stopped
    instrumenting the client)."""

    def __init__(self, started: datetime, store_started: datetime | None = None, *,
                 extra: list | None = None, without_store_span: bool = False) -> None:
        self.started = started
        self.store_started = store_started or started
        self.extra = extra or []
        self.without_store_span = without_store_span

    def q(self, sql, params=()):
        if "'request'" in sql:
            rows = [("69c451bad5014a9e", self.store_started, self.store_started + timedelta(milliseconds=15),
                     {"lineage.self.id": "get-flights", "lineage.direction": "outbound", "lineage.protocol": "http",
                      "lineage.peer.host": "minio:9000", "http.method": "GET", "url.path": "/flights/flights.json"}),
                    ("c6f5447d11c54889", self.started - timedelta(seconds=1), self.started + timedelta(seconds=1),
                     {"lineage.self.id": "get-flights", "lineage.direction": "inbound", "lineage.protocol": "mcp",
                      "http.method": "POST", "url.path": "/mcp", "mcp.method": "tools/call"})]
            return (rows[1:] if self.without_store_span else rows) + self.extra
        return []


def _span(span_id, started, self_id, direction, peer):
    return (span_id, started, started + timedelta(milliseconds=10),
            {"lineage.self.id": self_id, "lineage.direction": direction, "lineage.protocol": "http",
             "lineage.peer.host": peer, "http.method": "GET", "url.path": "/x"})


def _records(ts: float) -> list[dict]:
    return [
        {"ledger": 1, "ts": ts - 0.5, "kind": "wire", "dir": "in", "event": "end", "method": "POST", "host": "get-flights:8000",
         "xid": "x1", "received_trace": T, "pod": "get-flights", "_workload": "get-flights", "t0": ts - 1.0},
        {"ledger": 1, "ts": ts, "kind": "store", "store": "s3", "op": "GetObject", "bucket": "flights",
         "endpoint": "http://minio:9000", "status": 200, "ms": 14.8, "ambient_trace": T,
         "pod": "get-flights", "_workload": "get-flights"},
    ]


def test_the_s3_read_pairs_with_its_http_span():
    c = ledger.compare(_Dg(T0), T, _records(T0.timestamp() + 0.015), FLEET)
    assert c["table_only"] == []
    assert [(p["pod"], p["host"], p["store"], p["op"], p["span_id"], p["timing_ok"]) for p in c["store_pairs"]] == [
        ("get-flights", "minio", "s3", "GetObject", "69c451bad5014a9e", True)]
    assert c["dark_hops"] == []
    assert c["timing_violations"] == []


def test_a_store_span_outside_the_record_window_is_a_timing_violation():
    c = ledger.compare(_Dg(T0, store_started=T0 + timedelta(seconds=30)), T, _records(T0.timestamp()), FLEET)
    assert len(c["store_pairs"]) == 1 and c["table_only"] == []
    assert [v["span_id"] for v in c["timing_violations"]] == ["69c451bad5014a9e"]


def test_a_store_without_a_declared_http_edge_stays_dark():
    recs = _records(T0.timestamp())
    recs[1] = {**recs[1], "store": "postgres", "op": "execute", "endpoint": None}
    c = ledger.compare(_Dg(T0), T, recs, FLEET)
    assert [d["store"] for d in c["dark_hops"]] == ["postgres"]
    assert [s["span_id"] for s in c["table_only"]] == ["69c451bad5014a9e"]


def test_a_declared_store_act_with_no_span_is_asserted_not_dark():
    """The case the pairing exists for, inverted: a kit that stops
    instrumenting the client leaves the S3 read without a span. That is a
    sidecar miss, never 'dark by construction'."""
    import pytest
    c = ledger.compare(_Dg(T0, without_store_span=True), T, _records(T0.timestamp()), FLEET)
    assert c["store_pairs"] == [] and c["dark_hops"] == []
    assert [(u["pod"], u["host"]) for u in c["store_unpaired"]] == [("get-flights", "minio")]
    with pytest.raises(AssertionError, match="store_unpaired"):
        ledger.assert_consistent(c)


def test_a_store_pairs_only_with_its_own_pod_outbound_span_on_a_declared_edge():
    """Decoys that would win without the guards: another ledgered pod's span
    to minio (pod), get-flights' inbound span whose peer is minio
    (direction), a get-flights span to a host with no declared edge (edge).
    Each starts exactly at the record's start, nearer than the real span, so
    a dropped guard pairs it. None may take the S3 record; the real span does."""
    start = T0 + timedelta(milliseconds=0.2)   # ts - ms: the record's start
    extra = [_span("aaaa000000000001", start, "get-weather", "outbound", "minio:9000"),
             _span("aaaa000000000002", start, "get-flights", "inbound", "minio:9000"),
             _span("aaaa000000000003", start, "get-flights", "outbound", "weather-service:8080")]
    recs = _records(T0.timestamp() + 0.015)
    recs.append({**recs[0], "pod": "get-weather", "_workload": "get-weather", "host": "get-weather:8000"})  # get-weather is ledgered
    recs.append({**recs[1], "endpoint": "http://weather-service:8080"})   # a store on an undeclared edge
    c = ledger.compare(_Dg(T0 + timedelta(seconds=1), extra=extra), T, recs, FLEET)
    assert [p["span_id"] for p in c["store_pairs"]] == ["69c451bad5014a9e"]
    assert sorted(s["span_id"] for s in c["table_only"]) == ["aaaa000000000001", "aaaa000000000002", "aaaa000000000003"]
    assert [d["store"] for d in c["dark_hops"]] == ["s3"] and c["store_unpaired"] == []


def test_a_malformed_store_record_is_a_count_not_a_crash():
    bad = [{"ledger": 1, "kind": "store", "store": "s3", "ts": "x"},
           {"ledger": 1, "kind": "store", "store": "s3", "ms": "14.8"},
           {"ledger": 1, "kind": "store", "store": "s3", "endpoint": 9000}]
    assert [ledger._well_formed(r) for r in bad] == [False, False, False]
    assert ledger._well_formed({"ledger": 1, "kind": "store", "store": "s3", "ts": 1.0, "ms": 2, "endpoint": None})
