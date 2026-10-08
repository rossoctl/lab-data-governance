"""Render a live-tier run record as tables, or diff two records.

    python3 tests/live/report.py [tests/live/runs/<dir>]        # Markdown: header, verdicts, one table per scenario
    python3 tests/live/report.py --csv [<dir>]                   # the run as one unpivoted CSV (case, id, property, expected, actual, status, comment)
    python3 tests/live/report.py diff <dir A> <dir B>            # rows whose status or actual changed between two runs, joined on (case, id)

Reads only what the run already wrote (verdict.json, preflight.json,
capabilities.json, the per-scenario checks.json) and the scenario
docstrings from the test modules (via ``ast``, no import). Nothing is
asserted here: the verdicts are pytest's, the rows are the tests' own
(tests/live/checks.py); this file only lays them out.

Statuses in a row: ``pass`` / ``FAIL`` (an expectation), ``KNOWN`` (a
failure inside a declared gap or a filed defect — never counted green),
``recorded`` (an observation with no expectation).
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
# Assertion text → the finding it is known to be. The keys
# are substrings of the assertion messages (a check's comment or id) at
#   tests/live/test_travel_live.py  "PII from another session"  (L6, isolation.leak)
#   tests/live/shape.py             "entity set mismatch"       (risk.entity_set, L7 and every lab row)
#   tests/live/test_travel_live.py  "a cut stream reads ok"     (L15, span.not_ok)
#   tests/live/test_travel_live.py  "scored as sent to the external destination"  (L11)
# Matching is by substring of pytest's longrepr or of a row's comment: if one
# of those messages is reworded, change the key here in the same commit, or
# the finding silently reads as "none" below. tests/live_offline/test_report_findings.py
# pins the pairing.
FINDINGS = {
    "PII from another session": "booking-agent cross-session prompt leak",
    "entity set mismatch": "risk record keeps the stale peer.host callee (#279)",
    "a cut stream reads ok": "cut stream recorded ok",
    "scored as sent to the external destination": "response data scored as sent (#271)",
}
COLS = ["case", "id", "property", "expected", "actual", "status", "comment"]


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def docstrings() -> dict[str, str]:
    """Test name → the first paragraph of its docstring (the one-line card)."""
    out: dict[str, str] = {}
    for f in ("test_travel_live.py", "test_lab_live.py"):
        tree = ast.parse((HERE / f).read_text(encoding="utf-8"))
        out.update({n.name: (ast.get_docstring(n) or "").split("\n\n")[0].replace("\n", " ")
                    for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")})
    return out


def failure_line(longrepr: str | None) -> str:
    if not longrepr:
        return ""
    m = re.search(r"^E\s+(AssertionError: .*|assert .*)$", longrepr, re.M)
    return (m.group(1) if m else longrepr.strip().splitlines()[-1])[:300]


def cell(v) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, default=str, sort_keys=True)
    s = s.replace("|", "¦").replace("\n", " ")
    return s if len(s) <= 120 else s[:117] + "…"


def status_cell(s: str) -> str:
    return {"pass": "pass", "FAIL": "**FAIL**", "KNOWN": "KNOWN", "recorded": "recorded"}.get(s, s)


def case_dir(run: Path, test_name: str) -> Path:
    m = re.match(r"test_([LR]\d+)", test_name) or re.match(r"test_(\w+?)(?:_|$)", test_name)
    return run / m.group(1)


def rows_of(run: Path) -> list[dict]:
    """Every scenario's rows, in verdict order (the order the tests ran)."""
    verdict = load(run / "verdict.json") or {}
    out = []
    for name in verdict:
        rows = load(case_dir(run, name) / "checks.json") or []
        out += rows
    return out


def summary_counts(rows: list[dict]) -> dict[str, int]:
    return {s: sum(1 for r in rows if r["status"] == s) for s in ("pass", "FAIL", "KNOWN", "recorded")}


def table(rows: list[dict], *, with_case: bool = False) -> str:
    head = (["case"] if with_case else []) + ["id", "property", "expected", "actual", "status", "comment"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        vals = ([r["case"]] if with_case else []) + [
            f"`{r['id']}`", cell(r["property"]), cell(r["expected"]), cell(r["actual"]), status_cell(r["status"]), cell(r["comment"])]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render(run: Path) -> str:
    verdict, pf, caps = load(run / "verdict.json") or {}, load(run / "preflight.json") or {}, \
        load(run / "capabilities.json") or {}
    docs = docstrings()
    env = load(run / "env.json") or {}
    out = [f"# Live run {run.name}", ""]
    out.append(f"cluster `{env.get('kube_context')}` · app `{env.get('app', env.get('app_ns'))}` · ns `{env.get('app_ns')}` · "
               f"DG `{env.get('dg_commit', pf.get('dg.commit', {}).get('observed', '?')[:7])}` · "
               f"destructive {env.get('destructive')}")
    out.append("")
    counts = {}
    for v in verdict.values():
        counts[v["outcome"]] = counts.get(v["outcome"], 0) + 1
    all_rows = rows_of(run)
    rc = summary_counts(all_rows)
    out.append("**Scenarios**: " + " · ".join(f"{n} {k}" for k, n in sorted(counts.items())) +
               f"  \n**Rows**: {len(all_rows)} — {rc['pass']} pass · {rc['FAIL']} FAIL · {rc['KNOWN']} KNOWN · {rc['recorded']} recorded  ")
    bad = [k for k, v in pf.items() if "expected" in v and v["expected"] != v["observed"]] + \
          [k for k, v in pf.items() if "expected_suffix" in v
           and not str(v["observed"]).endswith(v["expected_suffix"])]
    out.append(f"**Preflight**: {len(pf)} pins, mismatches: {bad or 'none'}  ")
    ah = pf.get("dg.alembic_head") or {}
    if ah.get("accepted_override"):
        out.append(f"**Schema**: cluster at `{ah['observed']}`, this checkout's head is `{ah['checkout_head']}` — "
                   f"accepted by E2E_ACCEPT_ALEMBIC_HEAD (another branch's processors on the same derived tables)  ")
    out.append(f"**Capabilities**: alerts={caps.get('alerts')} health={caps.get('health')} ledger={caps.get('ledger')}  ")
    proof, restored = load(run / "catalog-proof.json") or {}, load(run / "catalog-restored-proof.json")
    out.append(f"**Catalog**: test catalog proved live by {proof.get('triggered_rules')}; shipped catalog restored "
               f"{'and proved' if restored else 'NOT PROVED'}; `catalog-after.yaml` "
               f"{'present' if (run / 'catalog-after.yaml').exists() else 'MISSING'}.")
    out.append("")
    out.append("| scenario | outcome | s | reason / failure |\n|---|---|---:|---|")
    for name, v in verdict.items():
        why = v.get("reason") or failure_line(v["longrepr"])
        out.append(f"| {name.removeprefix('test_')} | {v['outcome']} | {v['duration']} | {cell(why)} |")
    hits = set()
    for v in verdict.values():
        for k, f in FINDINGS.items():
            if k in (v["longrepr"] or "") or k in (v.get("reason") or ""):
                hits.add(f)
    for r in all_rows:
        for k, f in FINDINGS.items():
            if r["status"] in ("FAIL", "KNOWN") and (k in (r["comment"] or "") or k in (r["property"] or "")):
                hits.add(f)
    out.append("\n**Known findings reproduced**: " + ("; ".join(sorted(hits)) if hits else "none") + "\n")
    for name, v in verdict.items():
        d = case_dir(run, name)
        rows = load(d / "checks.json") or []
        c = summary_counts(rows)
        out.append(f"## {name.removeprefix('test_')} — {v['outcome']} ({v['duration']} s)")
        out.append("")
        out.append(f"> {docs.get(name, '')}")
        out.append("")
        if v.get("reason"):
            out.append(f"_{v['outcome']}: {v['reason']}_\n")
        if rows:
            out.append(f"{len(rows)} rows: {c['pass']} pass · {c['FAIL']} FAIL · {c['KNOWN']} KNOWN · {c['recorded']} recorded\n")
            out.append(table(rows))
        else:
            out.append("_no rows recorded_")
        if v["outcome"] == "failed" and not any(r["status"] == "FAIL" for r in rows):
            out.append(f"\nfailure outside the table: `{cell(failure_line(v['longrepr']))}`")
        out.append("")
    return "\n".join(out)


def to_csv(run: Path) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    for r in rows_of(run):
        w.writerow({k: (r.get(k) if isinstance(r.get(k), str) or r.get(k) is None
                        else json.dumps(r.get(k), default=str, sort_keys=True)) for k in COLS})
    return buf.getvalue()


def diff(a: Path, b: Path) -> str:
    """Rows joined on (case, id); printed when status or actual differ, or
    when a row exists on one side only. Recorded rows are compared on actual
    too, so a changed count is visible."""
    ra = {(r["case"], r["id"]): r for r in rows_of(a)}
    rb = {(r["case"], r["id"]): r for r in rows_of(b)}
    keys = sorted(set(ra) | set(rb), key=lambda k: (_case_order(k[0]), k[1]))
    out = [f"# diff `{a.name}` → `{b.name}`", "",
           "| case | id | property | A status | A actual | B status | B actual |", "|---|---|---|---|---|---|---|"]
    changed = 0
    for k in keys:
        x, y = ra.get(k), rb.get(k)
        if x and y and x["status"] == y["status"] and json.dumps(x["actual"], sort_keys=True, default=str) == json.dumps(y["actual"], sort_keys=True, default=str):
            continue
        changed += 1
        prop = (y or x)["property"]
        out.append(f"| {k[0]} | `{k[1]}` | {cell(prop)} | {status_cell(x['status']) if x else '—'} | {cell(x['actual']) if x else '—'} | "
                   f"{status_cell(y['status']) if y else '—'} | {cell(y['actual']) if y else '—'} |")
    out.insert(2, f"{changed} of {len(keys)} rows differ (status or actual); rows only on one side shown with —\n")
    return "\n".join(out)


def _case_order(case: str) -> tuple:
    m = re.match(r"([LR])(\d+)", case)
    return (m.group(1), int(m.group(2))) if m else ("Z", 0)


def _newest() -> Path:
    runs = HERE / "runs"
    if runs.exists() and any(runs.iterdir()):
        return sorted(runs.iterdir())[-1]
    sys.exit(f"no run records under {runs}; pass a run directory")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "diff":
        if len(args) != 3:
            sys.exit("usage: report.py diff <run A> <run B>")
        print(diff(Path(args[1]), Path(args[2])))
    elif args and args[0] == "--csv":
        print(to_csv(Path(args[1]) if len(args) > 1 else _newest()), end="")
    else:
        print(render(Path(args[0]) if args else _newest()))
