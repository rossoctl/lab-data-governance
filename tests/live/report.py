"""Render one Markdown summary of a live-tier run record.

    python3 tests/live/report.py [tests/live/runs/<dir>]   # default: the newest run

Reads only what the run already wrote (verdict.json, preflight.json, the
per-scenario json files) and the scenario docstrings from the test module
(via ``ast``, no import), and prints Markdown. Nothing is asserted here:
the verdicts are pytest's, this file only puts the numbers beside them.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
FINDINGS = {  # assertion text → the finding it is known to be (docs/FINDING-*.md)
    "PII from another session": "booking-agent cross-session prompt leak (2026-09-09)",
    "entity set mismatch": "risk record keeps the stale peer.host callee (2026-09-10)",
}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def docstrings() -> dict[str, str]:
    tree = ast.parse((HERE / "test_travel_live.py").read_text(encoding="utf-8"))
    return {n.name: "\n>\n> ".join(par.replace("\n", " ") for par in
                               (ast.get_docstring(n) or "").split("\n\n")[:2])
            for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def failure_line(longrepr: str | None) -> str:
    if not longrepr:
        return ""
    m = re.search(r"^E\s+(AssertionError: .*|assert .*)$", longrepr, re.M)
    return (m.group(1) if m else longrepr.strip().splitlines()[-1])[:300]


def fmt_forest(f: dict) -> str:
    return (f"{f['interactions']} interactions · {f['roots']} roots · {f['orphans']} orphans · "
            f"{f['unpaired_requests']} unpaired · parents {f['inbound_parent_sources']} · "
            f"depth {f['depth_histogram']} · escaped {len(f['escaped_traces'])}")


def fmt_settle(s: dict) -> str:
    return (f"{s['elapsed']:.1f} s · {s['polls']} polls · {s['spans']} spans · "
            f"absent streams {s['streams_absent'] or 'none'}")


def fmt_risk(r: dict) -> str:
    t = r["trace"] or {}
    return (f"{len(r['records'])} records / {len(r['live'])} live · trace v{t.get('version')} "
            f"{t.get('level')}/{t.get('enforcement')} · ghosts {len(r['ghosts'])} · "
            f"unclassified {len(r.get('unclassified_legs', []))} · pending {len(r.get('pending_records', {}))}")


def verdict_of(rec: dict) -> str:
    return f"{rec['risk_level']}/{rec['enforcement_type']} {sorted(set(rec['rules']) - {'0000'})}"


def scenario_lines(d: Path) -> list[str]:
    out = []
    for p in sorted(d.glob("settle*.json")):
        out.append(f"settle `{p.name}`: {fmt_settle(load(p))}")
    for p in sorted(d.glob("forest*.json")):
        out.append(f"forest `{p.name}`: {fmt_forest(load(p))}")
    for p in sorted(d.glob("risk*.json")):
        out.append(f"risk `{p.name}`: {fmt_risk(load(p))}")
    if (r := load(d / "records.json")):  # L2
        out += [f"probe {k}: {verdict_of(v)}" for k, v in r.items()]
    if (r := load(d / "per_probe.json")):  # L3
        out += [f"probe {k}: {verdict_of(v['record'])}" for k, v in r.items()]
    for name in ("benign_record.json", "record.json"):  # L1, L6
        if (r := load(d / name)):
            out.append(f"`{name}`: {verdict_of(r)} · summary {r['summary']}")
    if (h := load(d / "history.json")):  # L4, L9
        out.append("versions: " + " → ".join(f"v{x['version']} legs {x['legs']} {x['risk_level']}/"
                                             f"{x['enforcement_type']}" for x in h))
    if (fp := load(d / "fingerprints.json")):
        out.append(f"distinct decision fingerprints: {fp['distinct']}")
    if (sp := load(d / "response_span.json")):  # L5
        out.append(f"response span outcome `{sp['outcome']}` status {sp['status']}")
    if (legs := load(d / "legs.json")):
        out.append("legs: " + ", ".join(f"{k} payload={'yes' if v['payload_hash'] else 'none'} "
                                        f"error={v['error']}" for k, v in legs.items()))
    if (pair := load(d / "pair.json")):  # L8
        same = all(pair["l2"][k] == pair["new"][k]
                   for k in ("risk_level", "enforcement_type", "rules", "summary"))
        out.append(f"byte-identical probe: new v{pair['new']['version']}, equals L2's: {same}")
    if (drift := load(d / "l1_drift.json")) is not None:
        out.append(f"L1 interactions re-versioned: {drift or 'none'}")
    for p in sorted(d.glob("crossover-*.json")):  # L7
        c = load(p)
        out.append(f"`{p.name}`: own {c['own']!r} on {len(c['own_hops'])} hops · "
                   f"leak {c['other']!r} on {len(c['leak_hops'])} hops"
                   + (f" at {sorted({(h['self_id'], h['protocol']) for h in c['leak_hops']})}"
                      if c["leak_hops"] else ""))
    for p in sorted(d.glob("alerts-*.json")):  # L10
        out.append(f"`{p.name}`: " + "; ".join(f"{a['level']} {a['status']} dup={a['duplicates']} "
                                               f"superseded={'yes' if a['superseded_by'] else 'no'}"
                                               for a in load(p)))
    if (s := load(d / "strays.json")) is not None:
        out.append(f"strays tolerated: {len(s)}")
    return out


def main(run: Path) -> None:
    verdict, pf, caps = load(run / "verdict.json") or {}, load(run / "preflight.json") or {}, \
        load(run / "capabilities.json") or {}
    docs = docstrings()
    print(f"# Live run {run.name}\n")
    env = load(run / "env.json") or {}
    print(f"cluster `{env.get('kube_context')}` · app ns `{env.get('app_ns')}` · "
          f"DG `{env.get('dg_commit', pf.get('dg.commit', {}).get('observed', '?')[:7])}` · "
          f"destructive {env.get('destructive')}\n")
    bad = [k for k, v in pf.items() if "expected" in v and v["expected"] != v["observed"]] + \
          [k for k, v in pf.items() if "expected_suffix" in v
           and not str(v["observed"]).endswith(v["expected_suffix"])]
    print(f"**Preflight**: {len(pf)} checks, mismatches: {bad or 'none'}  ")
    print(f"**Capabilities**: alerts={caps.get('alerts')} health={caps.get('health')}  ")
    proof, restored = load(run / "catalog-proof.json") or {}, load(run / "catalog-restored-proof.json")
    print(f"**Catalog**: test catalog proved by rules {proof.get('triggered_rules')}; "
          f"restored proof {restored.get('triggered_rules') if restored else 'MISSING'} "
          f"(shipped = ['0000']); `catalog-after.yaml` "
          f"{'present' if (run / 'catalog-after.yaml').exists() else 'MISSING'}. "
          f"The sink's deletion is not recorded: check `kubectl -n {env.get('app_ns')} get deploy e2e-sink`.\n")
    print("| scenario | outcome | s | failure |\n|---|---|---:|---|")
    for name, v in verdict.items():
        print(f"| {name.removeprefix('test_')} | {v['outcome']} | {v['duration']} | "
              f"{failure_line(v['longrepr']).replace('|', '¦')} |")
    hits = {f for v in verdict.values() for k, f in FINDINGS.items() if k in (v["longrepr"] or "")}
    print("\n**Known findings reproduced**: " + ("; ".join(sorted(hits)) if hits else "none") + "\n")
    for name, v in verdict.items():
        d = run / re.match(r"test_(L\d+)", name).group(1)
        print(f"## {name.removeprefix('test_')} — {v['outcome']} ({v['duration']} s)\n")
        print(f"> {docs.get(name, '')}\n")
        for line in (scenario_lines(d) if d.is_dir() else ["no scenario files written"]):
            print(f"- {line}")
        print()


if __name__ == "__main__":
    runs = HERE / "runs"
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(runs.iterdir())[-1])
