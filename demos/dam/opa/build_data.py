"""Merge the decision documents aiac writes (one per agent) into OPA's data.dam.agents."""
import json, sys, pathlib
agents = {}
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    d = json.loads(f.read_text())
    agents[d["agent"]["id"]] = {
        "name": d["agent"]["name"],
        "connection": d["connection"],
        "host": d["host"],
        "generated_at": d["generated_at"],
        "tools": {n: {"effect": t["effect"], "basis": t["basis"], "reason": t["reason"]} for n, t in d["tools"].items()},
    }
json.dump({"dam": {"agents": agents}}, sys.stdout, indent=1, sort_keys=True)
