"""counts4.py — the film's numbers and verdicts, read from Sentry's database for the take's five sessions
(<take>/traces.txt: A, B, N1, C, N2). N = interactions in run A (one tap), T of them
among agents and tools, M calls to the model; S = sessions; J = interactions with a risk record across the five;
F = those whose latest risk level is not none; per run, the verdict on its call to the demand forecaster.
ff = the live recording's speed-up (take/film.sh live). Writes <take>/counts.json.

    FILM_TAKE=… $PYTHON film/long/v4/counts4.py"""
import json
import pathlib
import sys

V4 = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(V4.parent))
from env import TAKE, dgsql  # noqa: E402
RUNS = {l.split()[0]: l.split()[1] for l in (TAKE / "traces.txt").read_text().splitlines() if l.strip()}
ORDER = ["A", "B", "N1", "C", "N2"]
ids = ",".join(f"'{RUNS[k]}'" for k in ORDER)
LATEST = "(select rr.{c} from interaction_risk_records rr where rr.interaction_id=i.id order by rr.version desc limit 1)"
SQL = f"""select json_build_object(
 'N', (select count(*) from interactions where trace_id='{RUNS["A"]}'),
 'M', (select count(*) from interactions i join entities e on e.id=i.callee_entity_id where i.trace_id='{RUNS["A"]}' and e.kind::text='llm'),
 'J', (select count(distinct r.interaction_id) from interaction_risk_records r join interactions i on i.id=r.interaction_id where i.trace_id in ({ids})),
 'F', (select count(*) from interactions i where i.trace_id in ({ids}) and {LATEST.format(c="risk_level::text")} <> 'none'),
 'forecaster', (select json_object_agg(i.trace_id, json_build_object('caller', ce.display_name, 'level', {LATEST.format(c="risk_level::text")},
     'enforcement', {LATEST.format(c="enforcement_type::text")}, 'rules', {LATEST.format(c="triggered_rule_ids")}))
   from interactions i join entities e on e.id=i.callee_entity_id join entities ce on ce.id=i.caller_entity_id
   where i.trace_id in ({ids}) and e.display_name like 'demand-forecast%'))"""
d = json.loads(dgsql(SQL))
d["T"] = d["N"] - d["M"]
d["S"] = len(ORDER)
d["runs"] = {k: {"trace": RUNS[k], **(d["forecaster"] or {}).get(RUNS[k], {})} for k in ORDER}
d["ff"] = "▶▶ ×" + __import__("os").environ.get("LIVE_SPEED", "7")
del d["forecaster"]
(TAKE / "counts.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
print(json.dumps(d, indent=1))
