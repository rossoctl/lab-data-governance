"""analyze.py <run>... — one table row per run from turn.json, pod logs and the DG."""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def dg(sql):
    r = subprocess.run([f"{HERE}/dgq.sh", sql], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"DG query failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout.strip()


for run in sys.argv[1:] or sys.exit(__doc__):
    if not os.path.exists(f"{HERE}/runs/{run}/turn.json"):
        sys.exit(f"no run named {run} under runs/")
    d = json.load(open(f"{HERE}/runs/{run}/turn.json"))
    T = d["trace"]
    statuses = re.findall(r'"status": "(\w+)"', dg(
        f"select attributes->>'output.value' from spans where trace_id='{T}' "
        f"and name='booking-agent mcp create_booking response'"))
    calls = dg(f"select attributes->>'input.value' from spans where trace_id='{T}' "
               f"and name like 'booking-agent mcp notify_partner%' and attributes ? 'input.value'")
    partner_log = open(f"{HERE}/runs/{run}/travel-partner.log").read()
    np_tool = [line for line in open(f"{HERE}/runs/{run}/notify-partner.log")
               if line.startswith("LEDGER") and '"kind": "tool"' in line]
    sent = dg(f"select attributes->>'input.value' from spans where trace_id='{T}' "
              f"and name='notify-partner mcp booking_notice' and attributes ? 'input.value'")
    sent_args = json.loads(sent.splitlines()[0]) if sent else {}
    fields = [f"{k}={sent_args[k]}" if k == "guest_name" else k for k in sorted(sent_args)]
    risk = dg(f"""select r.risk_level||'/'||r.enforcement_type||' '||coalesce(r.triggered_rule_ids::text,'')
        from interactions i join entities e on e.id=i.callee_entity_id
        left join lateral (select * from interaction_risk_records x where x.interaction_id=i.id
                           order by version desc limit 1) r on true
        where i.trace_id='{T}' and e.natural_key like '%travel-partner%'""")
    print("|".join(map(str, [run, T, ",".join(statuses) or "-", bool(calls) or bool(np_tool),
                             ",".join(fields) or "-", risk or "-", d["state"], d["secs"],
                             len(partner_log.strip().splitlines())])))
