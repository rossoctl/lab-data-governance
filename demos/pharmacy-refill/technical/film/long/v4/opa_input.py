"""opa_input.py <label> <interaction_id> — the exact input Sentry's risk engine sends OPA for one exchange, and OPA's
answer, for the film's panels. Runs the engine's own evidence gathering and input builder (lab-dg at the pin,
risk/engine/compute.py _get_or_refresh_decision: gather_evidence → utils.build_opa_input) against the take's database,
read-only, with leg-ready's own settings; then asks the cluster's OPA (POST /v1/data/data_governance/policy_decision).
Nothing is written to Sentry. Writes <take>/opa/<label>.json = {interaction_id, input, result}.

    film/long/v4/opa_input.sh <label> <interaction_id> …      (FILM_TAKE=… picks the take)
Needs: port-forwards to the take's Sentry Postgres and OPA (OPA_URL), DG_DSN, leg-ready's RISK_* settings and the
Sentry checkout's Python env (opa_input.sh does all of it).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

from data_governance import db
from data_governance.risk.engine import utils
from data_governance.risk.engine.evidence import gather_evidence

label, iid = sys.argv[1], sys.argv[2]
db.configure(os.environ["DG_DSN"], min_size=1, max_size=2)
ev = gather_evidence(iid)
opa_input = utils.build_opa_input(legs=ev.legs, span_ids=ev.span_ids, classifications=ev.classifications,
                                  caller_entity_id=ev.caller_entity_id, callee_entity_id=ev.callee_entity_id,
                                  anchor=ev.anchor, inherited=ev.inherited)
req = urllib.request.Request(os.environ["OPA_URL"] + "/v1/data/data_governance/policy_decision",
                             json.dumps({"input": opa_input}).encode(), {"Content-Type": "application/json"})
result = json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]
out = pathlib.Path(os.environ["FILM_TAKE_DIR"]) / "opa" / f"{label}.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({"interaction_id": iid, "input": opa_input, "result": result}, indent=1, default=str))
print(f"{out.name}: {result.get('risk_level')} · {result.get('enforcement_type')} · {result.get('triggered_rules')}")
