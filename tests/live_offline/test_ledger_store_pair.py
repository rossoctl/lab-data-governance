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
    get-flights' inbound tools/call; no derived rows."""

    def __init__(self, started: datetime, store_started: datetime | None = None) -> None:
        self.started = started
        self.store_started = store_started or started

    def q(self, sql, params=()):
        if "'request'" in sql:
            return [("69c451bad5014a9e", self.store_started, self.store_started + timedelta(milliseconds=15),
                     {"lineage.self.id": "get-flights", "lineage.direction": "outbound", "lineage.protocol": "http",
                      "lineage.peer.host": "minio:9000", "http.method": "GET", "url.path": "/flights/flights.json"}),
                    ("c6f5447d11c54889", self.started - timedelta(seconds=1), self.started + timedelta(seconds=1),
                     {"lineage.self.id": "get-flights", "lineage.direction": "inbound", "lineage.protocol": "mcp",
                      "http.method": "POST", "url.path": "/mcp", "mcp.method": "tools/call"})]
        return []


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
