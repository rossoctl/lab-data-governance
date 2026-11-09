# One request through this pod's sidecar (HTTP_PROXY / HTTPS_PROXY), run INSIDE an app container, then the
# sidecar's own records of it from the session API (127.0.0.1:9094). Prints one JSON line.
#   usage: call.py <kind> <host:port>      kind: clean | pii | big | plain | connect
# clean/pii/big are MCP tools/call dispense_event (probe.py's payloads: a drug and dates, + name and birth date,
# + a 6 KB note); plain is a non-MCP JSON POST to /store (the gate's envelope shows its status on the wire);
# connect is an HTTPS GET through the proxy (a CONNECT tunnel). Every payload is new (dates from the clock).
import datetime
import json
import sys
import time
import urllib.request as u

import httpx

kind, host = sys.argv[1], sys.argv[2]
day = int(time.time() * 10) % 200000
fill = datetime.date(2030, 1, 1) + datetime.timedelta(days=day % 20000)
args = {"drug": ["Sertraline 50 mg tablets", "Lisinopril 10 mg tablets", "Atorvastatin 20 mg tablets"][day % 3],
        "fill_date": fill.isoformat(), "refill_due": (fill + datetime.timedelta(days=30)).isoformat()}
if kind == "pii":
    args.update({"external_id": str(5000000 + day % 4999999), "patient_name": "Priya Raman", "date_of_birth": "1989-07-14"})
if kind == "big":
    args["notes"] = "Refill packed and checked at the counter. " * 150
t0 = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
t = time.monotonic(); out = {"kind": kind, "host": host, "t0": t0}
try:
    if kind == "connect":
        r = httpx.get(f"https://{host}/", verify=False, timeout=15)
    elif kind == "plain":
        r = httpx.post(f"http://{host}/store", json={"note": f"shipment {fill.isoformat()}", "quantity": day % 97}, timeout=15)
    else:
        r = httpx.post(f"http://{host}/mcp", json={"jsonrpc": "2.0", "id": str(day), "method": "tools/call",
                       "params": {"name": "dispense_event", "arguments": args}},
                       headers={"Accept": "application/json, text/event-stream"}, timeout=15)
    out.update(status=r.status_code, text=r.text[:400])
    try:
        doc = r.json()
    except ValueError:
        doc = {}
    res = doc.get("result") if isinstance(doc, dict) else None
    sc = (res or {}).get("structuredContent") or {}
    out["delivered"] = sc.get("received") is True          # only the vendor's ack server answers received: true
    out["isError"] = bool((res or {}).get("isError"))
    out["refusal"] = sc if sc.get("plugin") == "sentry-gate" else (doc if isinstance(doc, dict) and doc.get("plugin") == "sentry-gate" else None)
except Exception as e:  # noqa: BLE001 — the caller reports any failure, it does not judge
    out.update(status=None, error=f"{type(e).__name__}: {str(e)[:200]}", delivered=False)
out["wall_ms"] = int((time.monotonic() - t) * 1000)
time.sleep(0.3)
o = u.build_opener(u.ProxyHandler({}))
recs = []
for s in json.load(o.open("http://127.0.0.1:9094/v1/sessions"))["sessions"]:
    for e in json.load(o.open(f"http://127.0.0.1:9094/v1/sessions/{s['id']}?limit=2000"))["events"]:
        if e.get("at", "") < t0 or e.get("direction") != "outbound":
            continue
        inv = (e.get("invocations") or {}).get("outbound") or []
        g = [i for i in inv if i.get("plugin") == "sentry-gate"]
        if not g:
            continue
        lin = [i for i in inv if i.get("plugin") == "lineage-telemetry"]
        recs.append({"at": e.get("at"), "host": e.get("host"), "method": e.get("httpMethod"), "phase": e.get("phase"),
                     "statusCode": e.get("statusCode"), "tunnel": e.get("tunnel"), "tunnelReason": e.get("tunnelReason"),
                     "gate": {k: g[0].get(k) for k in ("action", "reason", "details", "shadow") if g[0].get(k) is not None},
                     "lineage": {k: lin[0].get(k) for k in ("action", "reason", "details") if lin and lin[0].get(k) is not None}})
recs.sort(key=lambda r: r["at"])
out["records"] = recs
print(json.dumps(out))
