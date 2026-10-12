"""One travel-advisor turn from the demo-client pod, with the evidence around it.

usage: one_turn.py <label> [<guest> <city> <account>]   (writes runs/<label>/turn.json + pod logs)
Sends the live tier's scripted request (book a trip to Tokyo for Maya Park on
acct_001) under a fresh trace id, with dates drawn from 2029+ that no earlier
turn booked. The three optional arguments swap the traveller, the destination
and the payment account (accounts acct_001 to acct_005 are seeded). The booking
agent's behaviour is not in the request; it is the ROSSOCTL_VARIANT env on
deploy/booking-agent."""
import datetime as dt
import json
import os
import random
import secrets
import subprocess
import sys
import time

if len(sys.argv) not in (2, 5):
    sys.exit(__doc__.splitlines()[2])

NS = "travel-advisor"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", sys.argv[1])
os.makedirs(OUT, exist_ok=True)

# The request, the substitution, the id minters and the in-pod program mirror
# tests/live/driver.py (PR #236); re-point to it once that lands.
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

# Runs inside the demo-client pod: one non-streaming A2A message/send to the
# advisor under the given traceparent; prints the end state and the reply.
TURN_PROGRAM = r"""
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


def k(*a, input=None, timeout=120):
    r = subprocess.run(["kubectl", "-n", NS, *a], input=input, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode:
        sys.exit(f"kubectl -n {NS} {' '.join(a)} failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout


def psql(sql):
    return k("exec", "deploy/postgres", "--", "sh", "-c",
             f'psql -U "$POSTGRES_USER" -d travel -Atc "{sql}"')


def turn(trace_id, parent_id, text, url="http://travel-advisor:8080/", timeout=580):
    spec = {"trace_id": trace_id, "parent_id": parent_id, "text": text, "url": url, "timeout": timeout}
    out = k("exec", "-i", "deploy/demo-client", "-c", "client", "--", "env",
            f"E2E_SPEC={json.dumps(spec)}", "python3", "-", input=TURN_PROGRAM, timeout=timeout + 60)
    return json.loads(out)


while True:  # fresh dates: the booking tool keys on (guest, dates)
    d0 = dt.date(2029, 1, 1) + dt.timedelta(days=random.randrange(3000))
    dates = (d0.isoformat(), (d0 + dt.timedelta(days=5)).isoformat())
    if psql(f"select count(*) from bookings where dates = '{dates[0]} to {dates[1]}'").strip() == "0":
        break
text = USER_TURN.replace("2027-09-10", dates[0]).replace("2027-09-15", dates[1])
guest = "Maya Park"
if len(sys.argv) == 5:
    guest, city, account = sys.argv[2:5]
    text = text.replace("Maya Park", guest).replace("Tokyo", city).replace("acct_001", account)

T, p = secrets.token_hex(16), secrets.token_hex(8)
since = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
t0 = time.time()
print(f"trace {T} dates {dates} since {since} guest {guest}", flush=True)
print(f"risk  http://dg.localtest.me:8080/ui/risk/traces/{T}", flush=True)
print(f"flow  http://dg.localtest.me:8080/ui/traces/{T}/flow", flush=True)
r = turn(T, p, text)
secs = round(time.time() - t0, 1)
time.sleep(5)

pods = {"travel-advisor": "agent", "booking-agent": "agent", "create-booking": "mcp",
        "notify-partner": "mcp", "travel-partner": "partner", "payment-agent": "agent"}
for dep, c in pods.items():
    with open(f"{OUT}/{dep}.log", "w") as f:
        f.write(k("logs", f"deploy/{dep}", "-c", c, f"--since-time={since}"))
rows = psql(f"select id,status,created_at from bookings where dates = '{dates[0]} to {dates[1]}'")
summary = {"trace": T, "dates": dates, "since": since, "secs": secs, "state": r["state"],
           "answer": r["answer"], "bookings": rows.strip().splitlines(), "text": text}
json.dump({**summary, "raw": r["raw"]}, open(f"{OUT}/turn.json", "w"), indent=1, default=str)
print(json.dumps(summary, indent=1))
