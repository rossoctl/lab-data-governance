# summarize.py <label> <caller> <rows.jsonl> — reads call.py's JSON line on stdin, appends it (with label and caller)
# to rows.jsonl, prints one summary line: wire status, delivered, the gate's action/reason and the keys that explain it.
import json
import sys

label, caller, outf = sys.argv[1:4]
raw = sys.stdin.read().strip()
try:
    d = json.loads(raw)
except ValueError:
    print(label, "RAW", raw[:600]); sys.exit()
d["label"], d["caller"] = label, caller
with open(outf, "a") as f:
    f.write(json.dumps(d) + "\n")
r = (d.get("records") or [{}])[-1]
g = r.get("gate", {})
det = g.get("details", {})
keep = {k: det[k] for k in ("no_verdict", "error_kind", "waited_ms", "fail_mode_destination", "fail_mode_source",
                            "fail_mode_revision", "body_cut", "kind", "rules") if k in det}
if "fail_mode_revision" in keep:
    keep["fail_mode_revision"] = keep["fail_mode_revision"][:12]
shadow = " SHADOW" if g.get("shadow") else ""
print(f"{label:<9} {caller}->{d['host']} {d['kind']}: wire={d.get('status')} delivered={d.get('delivered')} "
      f"| gate {g.get('action')}/{g.get('reason')}{shadow} {keep}")
