"""Traffic drivers for the live tier: one scripted agent turn and one probe
call, both sent from inside the cluster by the app's own ``demo-client``
pod, so the pod's lineage sidecar records them exactly as it records the
demo. Nothing here talks to DG; this module only makes traffic happen.

Why ``kubectl exec`` and not a port-forward: a port-forward reaches the
app on loopback and bypasses the sidecar (the kit's RECIPE, the demo's
``ask.sh``, ``RUNBOOK-adapt-any-app.md``). The demo-client pod is the
external caller of the story, and its sidecar is the entry sidecar of
every trace this tier asserts on.

Why a Python program on stdin and a JSON spec in one env var: the demo's
``ask.sh`` embeds the program in an ``sh -c`` heredoc, which is fine for a
fixed text and fragile for payloads a test varies (quotes, ``$``,
newlines). Here ``kubectl exec -i … python3 -`` reads the program from
stdin and the program reads its parameters from ``E2E_SPEC``, so no test
value ever passes through a shell.

Trace and span ids are minted on the host (``secrets.token_hex``) so every
turn and every probe is addressable afterwards: a probe's sidecar request
span has ``spans.parent_id`` equal to the probe's ``parent_id`` and
``lineage.parent.source = 'wire'``; a turn's entry span likewise.
"""

from __future__ import annotations

import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

# The mock PSP the app ships (apps/travel_advisor/scripts/psp_mock.py):
# cluster short name, no dot, so the risk engine classifies it internal by
# construction. The external probe hits the SAME service with a dotted
# ``Host`` the whitelist does not list — same bytes, same connection,
# different name (the demo's risk-probe.sh asymmetry).
PSP_URL = "http://psp-mock:9091/charge"
# A path the PSP does not serve: GET it with no body and it answers 404 with a
# tiny plain-JSON body no parser captures. NOT /healthz: the lineage plugin's
# default bypass_paths (/.well-known/*, /healthz, /readyz, /health) produce no
# spans at all, so a health probe is invisible by design (observed live).
PSP_NOBODY_URL = "http://psp-mock:9091/e2e-no-such-path"
EXTERNAL_HOST = "api.travel-partner.example:9091"
# The test-owned sink (tests/live/k8s/e2e-sink.yaml): accepts the TCP
# connection and never answers, so a client-side timeout is the only way
# out — the abandoned-exchange probe. Dotted Host ⇒ classified external.
SINK_URL = "http://e2e-sink:9091/charge"
SINK_HOST = "sink.travel-partner.example:9091"
# The test-owned reflector (same manifest): answers every tools/call with a
# JSON-RPC result whose text is PII. Dotted Host ⇒ classified external.
REFLECT_URL = "http://e2e-reflect:9092/mcp"
REFLECT_HOST = "reflect.travel-partner.example:9092"

# demo.py's USER_TURN, verbatim (the demo's ask.sh carries the same text).
USER_TURN = (
    "Plan and book a trip:\n"
    "- country: Japan\n"
    "- city: Tokyo\n"
    "- month: October\n"
    "- dates: 2027-09-10 to 2027-09-15\n"
    "- passengers: 2\n"
    "- from_city: New York\n"
    "- to_city: Tokyo\n"
    "- guest_name: Maya Park\n"
    "- payment_account: acct_001\n"
    "- initial_budget: 3000\n"
    "- maximum_approval: 3500\n"
    "Follow all 7 steps of your script. Pass payment_account=acct_001 "
    "in your booking delegation message so booking-agent can have "
    "payment-agent process the charge. If booking-agent asks for "
    "approval up to $3500, approve once. Return the final summary."
)


def fresh_turn(base: str = USER_TURN) -> str:
    """USER_TURN with dates no earlier turn used. The booking tool
    de-duplicates on (guest, dates) and answers a repeat with the existing
    row — no charge, no payment-agent, no PSP: the turn's payment branch
    silently never runs. Fresh dates make every turn a new booking, so the declared
    topology is exercised every run. The pair stays within the year after
    the script's 2027-09; the day comes from the clock."""
    import datetime as _dt
    base_day = _dt.date(2027, 9, 1) + _dt.timedelta(days=int(time.time()) % 330)
    a, b = base_day.isoformat(), (base_day + _dt.timedelta(days=5)).isoformat()
    return base.replace("2027-09-10", a).replace("2027-09-15", b)


def rogue_turn(base: str | None = None) -> str:
    """The scripted turn with the guest's contact details on the request (the
    #258 demo: what the rogue instructions tell the booking agent to forward)."""
    text = base or fresh_turn()
    return text.replace("- guest_name: Maya Park\n",
                        "- guest_name: Maya Park\n- guest_email: maya.park@example.com\n- guest_phone: +1 212 555 0147\n")


def guest_turn(*, guest: str, city: str, account: str, base: str = USER_TURN) -> str:
    """USER_TURN with another guest, destination and payment account — the
    concurrency scenario needs two turns whose payloads must never cross."""
    return (
        base.replace("Maya Park", guest)
        .replace("Tokyo", city)
        .replace("acct_001", account)
    )


def mint_trace_id() -> str:
    return secrets.token_hex(16)


def mint_span_id() -> str:
    return secrets.token_hex(8)


# --- payloads -----------------------------------------------------------------
#
# Every POST body is wrapped as a JSON-RPC ``tools/call``: the sidecar
# captures payloads through its protocol parsers only (a plain HTTP body is
# metadata-only by design) and the mcp-parser is content-gated on any
# JSON-RPC body, surfacing exactly ``params.arguments`` as the captured
# input. The arguments are ALSO copied to the top level so the mock PSP
# answers 200 to the charge endpoint. Predicted classifications come from
# the classifier's tag table
# (data_governance/processors/classification/_config_data/
# EntityTypesForTokenClassification_with_tags.csv) and its ladder
# (logic.py: CREDENTIALS/PHI/PCI or a person-ID ⇒ RESTRICTED, PII ⇒
# CONFIDENTIAL, PI ⇒ INTERNAL; an identity bundle forces RESTRICTED).


def wrap(arguments: dict[str, Any], tool: str = "charge_card") -> dict[str, Any]:
    return dict(
        arguments,
        jsonrpc="2.0",
        id="1",
        method="tools/call",
        params={"name": tool, "arguments": arguments},
    )


def card_pii() -> dict[str, Any]:
    """The demo's card, verbatim: PCN (PII+PCI, person-ID), CVV/PCED (PI+PCI),
    PN + EMAIL (PII), MAMOUNT (PI) ⇒ tags {PI, PII, PCI}, level RESTRICTED,
    identity bundle name+email."""
    return wrap({
        "pan": "4111 1111 1111 1111", "expiry": "12/27", "cvv": "123",
        "amount_cents": 320000, "currency": "usd", "merchant": "Atlas Air",
        "cardholder": "Dana Cohen", "email": "dana.cohen@example.com",
        "note": "risk-probe: deliberate PII in transit",
    })


def pi_only() -> dict[str, Any]:
    """Demographics prose and nothing identifying: AGE, LOC, MAMOUNT, DATE
    are all tagged PI only ⇒ tags {PI}, level INTERNAL, no bundle. No
    names, emails, URLs or organisations (those are PII or would add
    tags)."""
    return wrap({
        "note": "Traveller aged 34, based in Boston, budget 3000 USD, "
                "travelling in October 2027.",
        "amount_cents": 300000, "currency": "usd",
    })


def benign() -> dict[str, Any]:
    """Nothing the classifier tags: no name, date, place, amount, id or
    secret ⇒ zero findings, level PUBLIC (#161 scenario 9, driven on purpose: a
    real travel turn never produces a finding-free leg, its prompts carry
    dates, cities and amounts throughout — observed live)."""
    return wrap({"note": "healthy", "status": "ok"})


def credential() -> dict[str, Any]:
    """A password and only a password: PW is tagged PI+CREDENTIALS ⇒ level
    RESTRICTED (CREDENTIALS). Deliberately no username — UN is tagged
    PII+CREDENTIALS and would also fire DG-001 externally."""
    return wrap({
        "note": "service password: Passw0rd!2027",
        "password": "Passw0rd!2027",
        "amount_cents": 100, "currency": "usd",
    })


# --- in-pod programs -----------------------------------------------------------

_PROBE_PROGRAM = r"""
import json, os, socket, time, urllib.error, urllib.request
spec = json.loads(os.environ["E2E_SPEC"])
headers = {"traceparent": "00-%s-%s-01" % (spec["trace_id"], spec["parent_id"])}
data = None
if spec.get("payload") is not None:
    data = json.dumps(spec["payload"]).encode()
    headers["content-type"] = "application/json"
if spec.get("host"):
    headers["Host"] = spec["host"]
req = urllib.request.Request(spec["url"], data=data, headers=headers, method=spec["method"])
t0 = time.time()
out = {"outcome": "ok"}
try:
    r = urllib.request.urlopen(req, timeout=spec["timeout"])
    out["status"] = r.status
    out["body"] = r.read(2000).decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    out = {"outcome": "http_error", "status": e.code}
except (socket.timeout, TimeoutError):
    out = {"outcome": "client_timeout"}
except urllib.error.URLError as e:
    if isinstance(e.reason, (socket.timeout, TimeoutError)):
        out = {"outcome": "client_timeout"}
    else:
        out = {"outcome": "error", "error": str(e.reason)}
out["elapsed"] = round(time.time() - t0, 3)
print(json.dumps(out))
"""

_TURN_PROGRAM = r"""
import json, os, urllib.request, uuid
spec = json.loads(os.environ["E2E_SPEC"])
body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "message/send",
    "params": {"message": {"role": "user", "messageId": uuid.uuid4().hex,
        "contextId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": spec["text"]}]}}}).encode()
req = urllib.request.Request(spec["url"], data=body,
    headers={"content-type": "application/json",
             "traceparent": "00-%s-%s-01" % (spec["trace_id"], spec["parent_id"])})
r = json.load(urllib.request.urlopen(req, timeout=spec["timeout"]))
res = r.get("result", r)
parts = [p for a in res.get("artifacts") or [] for p in a.get("parts", [])]
state = (res.get("status") or {}).get("state", "?")
print(json.dumps({"state": state,
                  "answer": " ".join(p.get("text", "") for p in parts),
                  "raw": json.dumps(r)[:2000]}))
"""


_CUT_TURN_PROGRAM = r"""
import json, os, socket, time, uuid
from urllib.parse import urlparse
spec = json.loads(os.environ["E2E_SPEC"])
u = urlparse(spec["url"])
body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "message/send",
    "params": {"message": {"role": "user", "messageId": uuid.uuid4().hex,
        "contextId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": spec["text"]}]}}}).encode()
head = ("POST %s HTTP/1.1\r\nHost: %s\r\ncontent-type: application/json\r\n"
        "traceparent: 00-%s-%s-01\r\ncontent-length: %d\r\n\r\n") % (
        u.path or "/", u.netloc, spec["trace_id"], spec["parent_id"], len(body))
s = socket.create_connection((u.hostname, u.port or 80), timeout=spec["hold"] + 5)
t0 = time.time()
s.sendall(head.encode() + body)
s.settimeout(spec["hold"])
got = b""
try:
    got = s.recv(1)   # nothing arrives within the hold: the turn takes minutes
except (socket.timeout, TimeoutError):
    pass
s.close()            # the client walks away mid-exchange
print(json.dumps({"held": round(time.time() - t0, 3), "bytes_before_cut": len(got)}))
"""


@dataclass(frozen=True)
class ProbeResult:
    trace_id: str
    parent_id: str
    label: str
    outcome: str  # ok | http_error | client_timeout | error
    status: int | None
    elapsed: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class TurnResult:
    trace_id: str
    parent_id: str
    state: str
    answer: str
    raw: str


def probe(
    kube,
    *,
    trace_id: str,
    parent_id: str,
    label: str,
    payload: dict[str, Any] | None = None,
    host: str | None = None,
    url: str = PSP_URL,
    method: str | None = None,
    timeout: float = 30.0,
) -> ProbeResult:
    """One HTTP exchange from demo-client under (trace_id, parent_id).
    ``method`` defaults to POST with a payload and GET without one."""
    spec = {
        "trace_id": trace_id, "parent_id": parent_id, "url": url,
        "method": method or ("POST" if payload is not None else "GET"),
        "payload": payload, "host": host, "timeout": timeout,
    }
    out = json.loads(kube.exec_py(_PROBE_PROGRAM, spec, timeout=timeout + 60))
    return ProbeResult(
        trace_id=trace_id, parent_id=parent_id, label=label,
        outcome=out["outcome"], status=out.get("status"),
        elapsed=out.get("elapsed", 0.0), raw=out,
    )


def probes_concurrent(kube, specs: list[dict[str, Any]]) -> list[ProbeResult]:
    """Fire every probe spec at once (each is its own ``kubectl exec``
    process, so threads are enough) and return results in spec order."""
    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        return list(pool.map(lambda s: probe(kube, **s), specs))


def turn(
    kube,
    *,
    trace_id: str,
    parent_id: str,
    text: str = USER_TURN,
    url: str = "http://travel-advisor:8080/",
    timeout: float = 580.0,
) -> TurnResult:
    """The app's scripted turn as one non-streaming ``message/send`` (the
    demo's ask.sh, with the ids and the text supplied)."""
    spec = {"trace_id": trace_id, "parent_id": parent_id, "text": text,
            "url": url, "timeout": timeout}
    out = json.loads(kube.exec_py(_TURN_PROGRAM, spec, timeout=timeout + 60))
    return TurnResult(trace_id=trace_id, parent_id=parent_id,
                      state=out["state"], answer=out["answer"], raw=out["raw"])


def turn_cut(kube, *, trace_id: str, parent_id: str, text: str = USER_TURN,
             url: str = "http://travel-advisor:8080/", hold: float = 20.0) -> dict[str, Any]:
    """A turn whose caller walks away: the request is sent in full, the
    client waits *hold* seconds for a first byte (the turn takes minutes)
    and closes the connection without reading a response. The entry
    sidecar sees its downstream disconnect while the upstream is still
    working — the exchange must not derive as ``ok``."""
    spec = {"trace_id": trace_id, "parent_id": parent_id, "text": text, "url": url, "hold": hold}
    return json.loads(kube.exec_py(_CUT_TURN_PROGRAM, spec, timeout=hold + 60))


def turns_concurrent(kube, specs: list[dict[str, Any]]) -> list[TurnResult]:
    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        return list(pool.map(lambda s: turn(kube, **s), specs))
