"""ids.py <take-dir> — every id the film's stills and repainted cards need, read from Sentry's database for the take's
five sessions (<take-dir>/traces.txt: A, B, N1, C, N2), written to <take-dir>/ids.json. Read-only.

  A   look: pharmacy-assistant → lookup-patient (its request names Rosa) · rep: report-dispense → demand-forecast · courier: schedule-delivery → courier
  B   rep · rec: review-refill → records (record_fill: account_ref) · bAt: B's start, as the trace list shows it (HH:MM, UTC)
  N1  nAt: N1's start, the same way (the level-3 still rings N1's row by it; a later nightly, N2, is on the same list)
  N1  rep: export-dispense-batch → demand-forecast · ans: export-dispense-batch → records (claim_fills: its answer holds the fills)
  C   rep · N2 rep
  cards: what the pitch's five Sentry crops are repainted with (film/long/v4/fixes/cards.py): B's lineage card (the origin
  and previous legs of the MRN on the report's request, by their leg numbers), N1's cross-session card (its origin leg and
  trace), and run A's alert time in this Mac's time zone, as Sentry's console prints it.

Stops when a session is missing an exchange or a label the film shows: the words follow the run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]


def env() -> dict[str, str]:
    out = subprocess.run(["bash", "-c", f'set -a; . "{os.environ.get("FILM_ENV", PKG / "film.env")}"; env'], capture_output=True, text=True, check=True).stdout
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


E = env()
KUBE = ["kubectl", "--kubeconfig", E["KUBECONFIG"], "--context", f"kind-{E['CLUSTER']}"]


def sql(q: str) -> list[list[str]]:
    cmd = (os.environ["IDS_PSQL"].split() if os.environ.get("IDS_PSQL") else   # a test hook: a psql on a restored dump
           KUBE + ["-n", "data-governance", "exec", "-i", "data-governance-postgres-0", "--", "sh", "-c",
                   'psql -U "$POSTGRES_USER" -d data_governance -At -F "|" -v ON_ERROR_STOP=1'])
    out = subprocess.run(cmd, input=q, capture_output=True, text=True, check=True).stdout
    return [l.split("|") for l in out.splitlines() if l]


def one(trace: str, caller: str, callee: str, has: str | None = None, kind: str = "request") -> str:
    """The first exchange caller → callee* in the trace (with `has` in its `kind` leg's payload, when given; a payload
    holds the tool's arguments or result, not the method's name)."""
    rows = sql(f"""select i.id, coalesce(p.content::text, '') from interactions i
      join entities c on c.id = i.caller_entity_id join entities e on e.id = i.callee_entity_id
      left join interaction_legs l on l.interaction_id = i.id and l.leg_type = '{kind}'
      left join interaction_payloads p on p.content_hash = l.payload_hash
      where i.trace_id = '{trace}' and coalesce(c.display_name, c.natural_key) = '{caller}'
        and coalesce(e.display_name, e.natural_key) like '{callee}%'
      order by (select min(x.occurred_at) from interaction_legs x where x.interaction_id = i.id)""")
    rows = [r for r in rows if not has or has in r[1]]
    if not rows:
        sys.exit(f"STOP: no {caller} → {callee}{' with ' + has + ' in its ' + kind if has else ''} in trace {trace}: the film shows one")
    return rows[0][0]


def leg(iid: str, kind: str) -> tuple[str, str, str]:
    """(leg number, caller, callee) of an exchange's request or response leg."""
    r = sql(f"""select l.seq, coalesce(c.display_name, c.natural_key), coalesce(e.display_name, e.natural_key)
      from interaction_legs l join interactions i on i.id = l.interaction_id
      join entities c on c.id = i.caller_entity_id join entities e on e.id = i.callee_entity_id
      where l.interaction_id = '{iid}' and l.leg_type = '{kind}'""")
    return tuple(r[0])


def mrn_edge(iid: str, kind: str, cross: bool) -> list[str]:
    """The MRN label edge on an exchange's leg: [origin interaction, origin leg type, from interaction, from leg type, origin trace]."""
    r = sql(f"""select origin_interaction_id, origin_leg_type, from_interaction_id, from_leg_type, coalesce(origin_trace_id, '')
      from payload_label_edges x where x.to_interaction_id = '{iid}' and x.to_leg_type = '{kind}' and x.entity_type = 'MRN'
        and x.unit_text = '4471820' and {"x.origin_trace_id is not null and x.origin_trace_id <> x.trace_id" if cross else "coalesce(x.origin_trace_id, x.trace_id) = x.trace_id"}
      order by x.edge_id limit 1""")
    if not r:
        sys.exit(f"STOP: no {'cross-session ' if cross else ''}MRN label on {iid}'s {kind}: the film's level {'3' if cross else '2'} rests on it")
    return r[0]


def started(trace: str) -> str:
    """The conversation's root span start, HH:MM:SS in UTC: how Sentry's trace list prints a trace's start."""
    r = sql(f"""select to_char(started_at at time zone 'UTC', 'HH24:MI:SS') from spans where trace_id = '{trace}'
      and parent_id is null and name like '%a2a%' order by started_at limit 1""")
    if not r or not r[0][0]:
        sys.exit(f"STOP: no spans for trace {trace} in this cluster's Sentry (the take was recorded on another cluster?)")
    return r[0][0]


def main() -> None:
    take = pathlib.Path(sys.argv[1]).resolve()
    runs = {l.split()[0]: (l.split()[1], l.split()[2]) for l in (take / "traces.txt").read_text().splitlines() if l.strip()}
    miss = [k for k in ("A", "B", "N1", "C", "N2") if k not in runs]
    if miss:
        sys.exit(f"STOP: {take}/traces.txt lacks {' '.join(miss)} (take/sequence.sh writes all five)")
    t = {k: v[0] for k, v in runs.items()}
    # the lookup whose request carries Rosa's name (on a fresh deploy the trace also holds the tools' discovery, tools/list)
    ids = {"traces": t, "A": {"look": one(t["A"], "pharmacy-assistant", "lookup-patient", "Rosa"), "rep": one(t["A"], "report-dispense", "demand-forecast"),
                              "courier": one(t["A"], "schedule-delivery", "courier")},
           "B": {"rep": one(t["B"], "report-dispense", "demand-forecast"), "rec": one(t["B"], "review-refill", "records", '"account_ref"'),
                 "bAt": started(t["B"])},
           "N1": {"nAt": started(t["N1"]), "rep": one(t["N1"], "export-dispense-batch", "demand-forecast"), "ans": one(t["N1"], "export-dispense-batch", "records", '"fills"', "response")},
           "C": {"rep": one(t["C"], "report-dispense", "demand-forecast")},
           "N2": {"rep": one(t["N2"], "export-dispense-batch", "demand-forecast")}}
    # B's lineage card: the MRN on the report's request, its origin and the leg before it
    o_iid, o_kind, f_iid, f_kind, _ = mrn_edge(ids["B"]["rep"], "request", cross=False)
    origin, prev = leg(o_iid, o_kind), leg(f_iid, f_kind)
    want = [("pharmacy-assistant", "lookup-patient", o_kind, "response"), ("fulfillment-agent", "report-dispense", f_kind, "request")]
    for (seq, c, e), (wc, we, k, wk) in zip([origin, prev], want):
        if (c, e, k) != (wc, we, wk):
            sys.exit(f"STOP: B's lineage card would read {c} → {e} {k}; the card says {wc} → {we} {wk} (the words follow the run: change cards.py's text)")
    # N1's cross-session card: the MRN on the records' answer at night, born in B
    x_iid, x_kind, _, _, x_trace = mrn_edge(ids["N1"]["ans"], "response", cross=True)
    xo = leg(x_iid, x_kind)
    if x_trace != t["B"]:
        sys.exit(f"STOP: N1's MRN is carried in from trace {x_trace[:8]}, not B ({t['B'][:8]}): the take's windows overlapped (refills > 15 min apart)")
    al = sql(f"""select extract(epoch from "timestamp") from alerts where trace_id = '{t["A"]}' and title like 'pii_to_untrusted_external%report-dispense%'
      order by "timestamp" limit 1""")
    if not al:
        sys.exit("STOP: run A raised no pii_to_untrusted_external alert on report-dispense; the pitch's level-1 alert row needs it")
    when = dt.datetime.fromtimestamp(float(al[0][0]))          # this Mac's zone, as the console (in this browser) prints it
    ex = sql(f"""select s.attributes->>'lineage.exchange.id' from interaction_spans x join spans s on s.span_id = x.span_id
      and s.trace_id = x.trace_id where x.interaction_id = '{ids["A"]["rep"]}' and x.role = 'anchor' limit 1""")
    ids["A"]["exchange"] = ex[0][0] if ex else ""        # the pipeline panel's example exchange (A's report)
    ids["cards"] = {"origin": f"#{origin[0]} pharmacy-assistant → lookup-patient response",
                    "previous": f"#{prev[0]} fulfillment-agent → report-dispense request",
                    "cross": f"#{xo[0]} pharmacy-assistant → lookup-patient response", "cross_trace": f"in trace {x_trace[:8]}…",
                    "alert_time": f"{when.month}/{when.day}/{when.year},\n{when.strftime('%I').lstrip('0')}:{when.strftime('%M:%S %p')}"}
    (take / "ids.json").write_text(json.dumps(ids, indent=1, ensure_ascii=False))
    print(json.dumps(ids, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
