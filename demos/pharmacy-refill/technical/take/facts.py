"""facts.py <take-dir> — <take>/take-facts.yaml, the review's truth file, from take/take-facts.template.yaml and the take's
own counts.json, ids.json (traces) and traces.txt (start times). Stops if a field is missing."""
import datetime as dt
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent; PKG = HERE.parent
take = pathlib.Path(sys.argv[1]).resolve()
c = json.loads((take / "counts.json").read_text())
starts = {l.split()[0]: dt.datetime.fromisoformat(l.split()[2].replace("Z", "+00:00")) for l in (take / "traces.txt").read_text().splitlines() if l.strip()}
t = {l.split()[0]: l.split()[1] for l in (take / "traces.txt").read_text().splitlines() if l.strip()}
pins = __import__("yaml").safe_load((PKG / "pins.yaml").read_text())
env = dict(kv.split("=", 1) for kv in subprocess.run(["bash", "-c", f'set -a; . "{PKG / "film.env"}"; env -0'], capture_output=True, text=True).stdout.split("\0") if "=" in kv)
gap = lambda a, b: f"{round((starts[b] - starts[a]).total_seconds() / 30) / 2:g}"
v = {"name": take.name, "cluster": env.get("CLUSTER", "?"), "date": starts["A"].strftime("%Y-%m-%d %H:%M UTC"),
     "dg_sha": pins["lab-data-governance"]["sha"][:7], "dg_branch": pins["lab-data-governance"]["branch"],
     "app_sha": pins["agent-examples-snp"]["sha"][:7],
     "gap_B": gap("A", "B"), "gap_N1": gap("B", "N1"), "gap_C": gap("N1", "C"), "gap_N2": gap("C", "N2"),
     **{f"{k}8": t[k][:8] for k in t}, **{k: c[k] for k in ("N", "M", "T", "J", "F")}}
out = (HERE / "take-facts.template.yaml").read_text().format(**v)
(take / "take-facts.yaml").write_text(out)
print(f">> take-facts.yaml: {v['N']} interactions in A, {v['J']} judged, {v['F']} flagged; gaps {v['gap_B']}/{v['gap_N1']}/{v['gap_C']}/{v['gap_N2']} min")
