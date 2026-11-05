"""Contract v1.8 in the tier, on synthetic spans: S15 (body sizes), F3 (the
turn's plain-HTTP hops carry their bodies) and the ledger's plain-HTTP digest
alignment. Each has the v1.8 shape passing and a pre-v1.8 shape failing."""
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.live import audit, ledger
from tests.live import fleet as fm

FLEET = fm.load(Path(__file__).parent.parent / "live" / "apps" / "travel_advisor.yaml")
T = "b86c1786b41fd817f2c2c018f64ab4a1"
T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
WEATHER_REQ = ""
WEATHER_RESP = '{"city": "Tokyo", "forecast": "sunny", "high_c": 24}'
PAGE_404 = ("<!doctype html>\n<html lang=en>\n<title>404 Not Found</title>\n<h1>Not Found</h1>\n"
            "<p>The requested URL was not found on the server.</p>\n")


def _pair(exchange: str, self_id: str, peer: str, *, req: str | None, resp: str | None,
          req_size: int | None, resp_size: int | None, outcome: str = "ok", protocol: str = "http"):
    """One exchange's request and response spans, the caller's side."""
    common = {"lineage.exchange.id": exchange, "lineage.direction": "outbound", "lineage.protocol": protocol,
              "lineage.self.id": self_id, "lineage.self.namespace": "travel-advisor", "lineage.peer.host": peer,
              "lineage.parent.source": "tracestate"}
    rq = {**common, "lineage.role": "request"}
    rs = {**common, "lineage.role": "response", "lineage.outcome": outcome}
    if req_size is not None:
        rq["http.request.body.size"] = req_size
    if resp_size is not None:
        rs["http.response.body.size"] = resp_size
    if req is not None:
        rq["input.value"] = req
    if resp is not None:
        rs["output.value"] = resp
    return (audit.Span(exchange, "p" * 16, T0, T0, False, rq),
            audit.Span(exchange[::-1], exchange, T0, T0, False, rs))


def _snapshot(pairs, *, payloads=True):
    """A snapshot whose interactions are the pairs above, each leg's payload
    present exactly when its span carries a value (unless *payloads* is
    False: a derivation that dropped them)."""
    spans, inters = {}, {}
    for rq, rs in pairs:
        spans[rq.span_id], spans[rs.span_id] = rq, rs
        caller = {"id": "c", "kind": "tool", "key": f"tool:travel-advisor/{rq.a('lineage.self.id')}", "ns": "travel-advisor"}
        callee = {"id": "e", "kind": "service", "key": f"service:{rq.a('lineage.peer.host')}", "ns": None}
        legs = {"request": {"payload_hash": "h1" if payloads and rq.a("input.value") else None, "error": None,
                            "payload_exists": True},
                "response": {"payload_hash": "h2" if payloads and rs.a("output.value") else None, "error": False,
                             "payload_exists": True}}
        inters[rq.span_id] = audit.Interaction(rq.span_id, None, caller, callee,
                                               {rq.span_id: "anchor", rs.span_id: "response"}, legs)
    return audit.Snapshot(T, spans, inters, (T0, T0))


def _check(au, cid):
    return next(c for c in au.checks if c.id == cid)


# --- S15 -----------------------------------------------------------------------


def test_s15_passes_on_v18_sizes_and_exact_http_values():
    snap = _snapshot([
        _pair("aaaaaaaaaaaaaaa1", "get-weather", "weather-service:8080", req=None, resp=WEATHER_RESP,
              req_size=0, resp_size=len(WEATHER_RESP)),
        _pair("aaaaaaaaaaaaaaa2", "demo-client", "psp-mock:9091", req=None, resp=PAGE_404,
              req_size=0, resp_size=len(PAGE_404.encode())),
        # a body that was not captured (binary, not whole, capture off): legal
        _pair("aaaaaaaaaaaaaaa3", "get-flights", "minio:9000", req=None, resp=None, req_size=0, resp_size=9000),
        # a cut value is not compared with its size
        _pair("aaaaaaaaaaaaaaa4", "charge-card", "psp.external.localtest.me:9091", req="x" * 4096 + audit.TRUNCATED,
              resp=None, req_size=6000, resp_size=0),
    ])
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, snap)
    c = _check(au, "S15")
    assert c.ok, c.detail
    assert c.observed["http_values_sized"] == 2


def test_s15_fails_on_a_sidecar_without_size_facts():
    """The known-broken case: a pre-v1.8 sidecar emits no size facts at all."""
    snap = _snapshot([_pair("aaaaaaaaaaaaaaa1", "get-weather", "weather-service:8080", req=None, resp=None,
                            req_size=None, resp_size=None)])
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, snap)
    c = _check(au, "S15")
    assert not c.ok and c.verdict == "FAIL"
    assert sorted(p for ps in c.detail.values() for p in ps) == [
        "http.request.body.size absent", "http.response.body.size absent"]


@pytest.mark.parametrize("pair, problem", [
    (_pair("b1", "get-weather", "weather-service:8080", req=None, resp=WEATHER_RESP, req_size=0, resp_size=7),
     "output.value is 52 bytes, http.response.body.size 7"),
    (_pair("b2", "travel-advisor", "llm", req="[...]", resp=None, req_size=0, resp_size=0, protocol="inference"),
     "input.value present with http.request.body.size 0"),
    (_pair("b3", "demo-client", "psp-mock:9091", req=None, resp="denied body", req_size=0, resp_size=11,
           outcome="denied"),
     "a denied http response carries output.value"),
    (_pair("b4", "get-weather", "weather-service:8080", req=None, resp=None, req_size="0", resp_size=0),
     "http.request.body.size '0'"),
])
def test_s15_names_each_violation(pair, problem):
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, _snapshot([pair]))
    c = _check(au, "S15")
    assert not c.ok and [problem] in c.detail.values()


# --- F3 ------------------------------------------------------------------------


def _weather(**kw):
    args = {"req": None, "resp": WEATHER_RESP, "req_size": 0, "resp_size": len(WEATHER_RESP)}
    return _pair("ccccccccccccccc1", "get-weather", "weather-service:8080", **{**args, **kw})


def _charge(**kw):
    body = '{"pan": "4111 1111 1111 1111", "amount_cents": 320000}'
    reply = '{"auth_code": "AUTH-1", "status": "approved"}'
    args = {"req": body, "resp": reply, "req_size": len(body), "resp_size": len(reply)}
    return _pair("ccccccccccccccc2", "charge-card", "psp.external.localtest.me:9091", **{**args, **kw})


def test_f3_passes_when_the_turns_http_hops_carry_their_bodies():
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([_weather(), _charge()]), FLEET)
    c = _check(au, "F3")
    assert c.ok, c.detail
    assert c.observed["hops"] == 2


def test_f3_fails_when_the_bodies_are_missing():
    """The known-broken case: a pre-v1.8 sidecar — sizes absent, no payload."""
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([_weather(resp=None, req_size=None, resp_size=None),
                                           _charge(req=None, resp=None, req_size=None, resp_size=None)]), FLEET)
    c = _check(au, "F3")
    assert not c.ok and c.observed["violations"] == 2


def test_f3_fails_when_a_sized_body_has_no_payload():
    """Sizes present (v1.8) but the derivation or the sidecar dropped the body."""
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([_charge()], payloads=False), FLEET)
    c = _check(au, "F3")
    assert not c.ok
    assert list(c.detail.values()) == [["request: size 54, payload absent", "response: size 45, payload absent"]]


def test_f3_reads_only_declared_http_edges_of_the_turn():
    """A probe of the tier's own and an http edge outside the turn (the
    metadata probe) are not the turn's hops."""
    meta = _pair("ccccccccccccccc3", "booking-agent", "169.254.169.254", req=None, resp=None,
                 req_size=None, resp_size=None)
    snap = _snapshot([_weather(), meta])
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, snap, FLEET)
    assert _check(au, "F3").ok and _check(au, "F3").observed["hops"] == 1
    snap = _snapshot([_weather(resp=None, resp_size=None)])
    snap.probe_parents = frozenset({"p" * 16})          # the anchor's parent is a minted probe id
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, snap, FLEET)
    assert _check(au, "F3").ok and _check(au, "F3").observed["hops"] == 0


# --- the ledger: plain-HTTP pairs are compared exactly ------------------------


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


CARD = '{"pan": "4111 1111 1111 1111", "amount_cents": 320000, "merchant": "Atlas Air"}'
REPLY = '{"auth_code": "AUTH-0A1B2C", "charge_id": "CHG-0011", "status": "approved"}'


class _Dg:
    """charge-card → the PSP (http) in T, with or without the bodies captured."""

    def __init__(self, *, captured: bool) -> None:
        self.captured = captured

    def q(self, sql, params=()):
        if "'request'" in sql:
            attrs = {"lineage.self.id": "charge-card", "lineage.direction": "outbound", "lineage.protocol": "http",
                     "lineage.peer.host": "psp.external.localtest.me:9091", "http.method": "POST",
                     "url.path": "/charge"}
            if self.captured:
                attrs["input.value"] = CARD
            return [("dddddddddddddd01", T0, T0 + timedelta(milliseconds=20), attrs)]
        if "'response'" in sql:
            return [("dddddddddddddd01", REPLY if self.captured else None)]
        return []


def _records(*, req_bytes: int | None = None, resp_bytes: int | None = None):
    req_bytes = len(CARD) if req_bytes is None else req_bytes
    resp_bytes = len(REPLY) if resp_bytes is None else resp_bytes
    ts = T0.timestamp()
    common = {"ledger": 1, "kind": "wire", "dir": "out", "xid": "x1", "proto": "http", "method": "POST",
              "host": "psp.external.localtest.me:9091", "path": "/charge", "t0": ts - 0.01,
              "pod": "charge-card", "_workload": "charge-card"}
    return [{**common, "event": "start", "ts": ts, "sent_trace": T, "status": 200,
             "req": {"sha": _sha(CARD), "bytes": req_bytes}},
            {**common, "event": "end", "ts": ts + 0.03, "status": 200, "ms": 30.0,
             "resp": {"sha": _sha(REPLY), "bytes": resp_bytes}}]


def test_a_plain_http_pair_with_its_bodies_captured_is_equal_both_ways():
    c = ledger.compare(_Dg(captured=True), T, _records(), FLEET)
    assert c["http_pairs"] == [("charge-card", "out", "psp.external.localtest.me:9091",
                                {"req": "equal", "resp": "equal"})]
    assert c["digest_mismatch"] == [] and c["http_truncated"] == []


def test_a_plain_http_pair_without_its_bodies_is_a_mismatch():
    """The known-broken case: a pre-v1.8 sidecar captures no plain-HTTP body,
    so both legs read ``uncaptured`` — asserted, never 'n/a'."""
    c = ledger.compare(_Dg(captured=False), T, _records(), FLEET)
    assert [p["http_legs"] for p in c["digest_mismatch"]] == [{"req": "uncaptured", "resp": "uncaptured"}]
    with pytest.raises(AssertionError, match="digest_mismatch"):
        ledger.assert_consistent(c)


def test_a_plain_http_body_over_the_cap_is_recorded_not_compared():
    c = ledger.compare(_Dg(captured=False), T, _records(resp_bytes=ledger.MAX_PAYLOAD_BYTES + 1), FLEET)
    assert c["http_truncated"] == [("charge-card", "out", "psp.external.localtest.me:9091", "resp",
                                    ledger.MAX_PAYLOAD_BYTES + 1)]
    assert [p["http_legs"] for p in c["digest_mismatch"]] == [{"req": "uncaptured"}]


def test_an_empty_plain_http_body_has_nothing_to_compare():
    c = ledger.compare(_Dg(captured=False), T, _records(req_bytes=0, resp_bytes=0), FLEET)
    assert c["http_pairs"] == [] and c["digest_mismatch"] == []


# --- the rules pinned exactly (mutants a first walk left alive) ----------------


def test_s15_counts_utf8_bytes_not_characters():
    body = '{"city": "Tōkyō", "note": "東京"}'
    ok = _pair("e1", "get-weather", "weather-service:8080", req=None, resp=body, req_size=0,
               resp_size=len(body.encode("utf-8")))
    chars = _pair("e2", "get-weather", "weather-service:8080", req=None, resp=body, req_size=0, resp_size=len(body))
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, _snapshot([ok]))
    assert _check(au, "S15").ok
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, _snapshot([chars]))
    assert not _check(au, "S15").ok


@pytest.mark.parametrize("size", [-1, True])
def test_s15_refuses_a_size_that_is_not_a_count(size):
    au = audit.Audit(T, FLEET)
    audit.body_sizes(au, _snapshot([_pair("e3", "get-weather", "weather-service:8080", req=None, resp=None,
                                          req_size=size, resp_size=0)]))
    assert not _check(au, "S15").ok


def test_f3_skips_a_denied_response_and_still_reads_its_request():
    denied = _charge(resp=None, outcome="denied")
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([denied]), FLEET)
    assert _check(au, "F3").ok
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([_charge(req=None, resp=None, outcome="denied")]), FLEET)
    assert list(_check(au, "F3").detail.values()) == [["request: size 54, payload absent"]]


def test_f3_a_request_payload_with_size_0_is_a_violation():
    au = audit.Audit(T, FLEET)
    audit.plain_http_bodies(au, _snapshot([_weather(req='{"q": "Tokyo"}')]), FLEET)
    assert list(_check(au, "F3").detail.values()) == [["request: size 0, payload present"]]


def test_s15_and_f3_run_inside_the_audit_axes():
    """audit.run reaches them through soundness() and fidelity(): a dropped
    call would leave the live tier without either check."""
    snap = _snapshot([_weather()])
    au = audit.Audit(T, FLEET)
    audit.soundness(au, snap, FLEET)
    audit.fidelity(au, snap, FLEET, markers=[], reply=None)
    assert {"S15", "F3"} <= {c.id for c in au.checks}


@pytest.mark.parametrize("n, compared", [(ledger.MAX_PAYLOAD_BYTES, True), (ledger.MAX_PAYLOAD_BYTES + 1, False)])
def test_the_cap_boundary_is_inclusive(n, compared):
    c = ledger.compare(_Dg(captured=False), T, _records(req_bytes=0, resp_bytes=n), FLEET)
    assert bool(c["digest_mismatch"]) is compared
    assert bool(c["http_truncated"]) is not compared


def test_a_streamed_response_is_recorded_not_compared():
    recs = _records()
    recs[1]["streamed"] = True
    c = ledger.compare(_Dg(captured=False), T, recs, FLEET)
    assert ("charge-card", "out", "psp.external.localtest.me:9091", "resp", "streamed") in c["http_truncated"]
    assert [p["http_legs"] for p in c["digest_mismatch"]] == [{"req": "uncaptured"}]


def test_a_plain_http_pair_off_the_declared_edges_is_not_compared():
    recs = [{**r, "host": "elsewhere.example:9091"} for r in _records()]

    class _Elsewhere(_Dg):
        def q(self, sql, params=()):
            rows = super().q(sql, params)
            if "'request'" in sql:
                rows = [(a, b, c, {**d, "lineage.peer.host": "elsewhere.example:9091"}) for a, b, c, d in rows]
            return rows
    c = ledger.compare(_Elsewhere(captured=False), T, recs, FLEET)
    assert c["http_pairs"] == [] and c["digest_mismatch"] == []


def test_plain_http_pairs_stay_out_of_the_unaligned_count():
    """A captured plain-HTTP body that differs is a mismatch, never 'unaligned'."""
    recs = _records()
    recs[0]["req"] = {"sha": _sha("something else"), "bytes": len(CARD)}
    c = ledger.compare(_Dg(captured=True), T, recs, FLEET)
    assert c["digest_unaligned"] == []
    assert [p["http_legs"] for p in c["digest_mismatch"]] == [{"req": "different", "resp": "equal"}]
