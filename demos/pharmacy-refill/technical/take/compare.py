"""compare.py <take> [<film name>] [--to v4.2] [--to-film film] — what differs between a take's film and film v4.2's (or another take's):
the counts and verdicts the narration reads, every spoken line that changed, each section's length, the film's length,
and the review's numbers. Read-only; prints Markdown.

    python3 take/compare.py 2026-10-26-b film
"""
import difflib
import json
import pathlib
import re
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
ref, ref_name = opt("--to", "v4.2"), opt("--to-film", "film")
args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] not in ("--to", "--to-film")]
take, name = PKG / "film/takes" / args[0], (args[1] if len(args) > 1 else "film")
base = PKG / "film/takes" / ref
P = lambda t, n: json.loads((t / f"{n}.plan.json").read_text())
a, b = P(base, ref_name), P(take, name)
ca, cb = (json.loads((t / "counts.json").read_text()) for t in (base, take))
out = [f"# {take.name}/{name}.mp4 against {ref}", ""]
out += ["## Counts and verdicts", "", "| | " + ref + " | " + take.name + " |", "|---|---|---|"]
for k in ("N", "M", "T", "J", "F", "S", "ff"):
    out.append(f"| {k} | {ca.get(k)} | {cb.get(k)}{'' if ca.get(k) == cb.get(k) else '  ← differs'} |")
for r in ("A", "B", "N1", "C", "N2"):
    va, vb = ca["runs"][r], cb["runs"][r]
    fa = f"{va.get('level')} {va.get('enforcement')} {','.join(va.get('rules') or [])}"
    fb = f"{vb.get('level')} {vb.get('enforcement')} {','.join(vb.get('rules') or [])}"
    out.append(f"| {r} | {fa} | {fb}{'' if fa == fb else '  ← differs'} |")
out += ["", "## Spoken lines that changed", ""]
sm = difflib.SequenceMatcher(a=a["script"], b=b["script"], autojunk=False)
changed = [(op, a["script"][i1:i2], b["script"][j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
out += [f"- {ref}: «{' / '.join(x)}»  \n  {take.name}: «{' / '.join(y)}»" for _, x, y in changed] or ["none"]
out += ["", "## Section lengths (s)", "", f"| section | {ref} | {take.name} |", "|---|---|---|"]
for n in a["sections"]:
    ea, eb = a["sections"][n]["cues"]["end"], b["sections"][n]["cues"]["end"]
    out.append(f"| {n} | {ea:.1f} | {eb:.1f}{'' if abs(ea - eb) < 0.05 else f'  ({eb - ea:+.1f})'} |")
la = a["film_lines"][-1][1]; lb = b["film_lines"][-1][1]
out += ["", f"Last line starts at {la:.1f} s ({ref}) and {lb:.1f} s ({take.name})."]
for t, n in ((base, ref_name), (take, name)):
    f = t / "review" / n / "REPORT.md"
    if f.exists():
        s = f.read_text()
        w = re.search(r"WER ([\d.]+%) of (\d+) words", s); l = re.search(r"(-?[\d.]+) LUFS integrated · true peak (-?[\d.]+)", s)
        c = re.search(r"Claims: (\d+) of (\d+)", s); fr = re.search(r"Frozen frames ≥ 1.5 s: (\d+), ([\d.]+) s", s)
        out.append(f"- review of {t.name}: WER {w and w[1]} of {w and w[2]} words · {l and l[1]} LUFS, peak {l and l[2]} · "
                   f"claims needing a look {c and c[1]}/{c and c[2]} · frozen {fr and fr[1]} ({fr and fr[2]} s)")
print("\n".join(out))
