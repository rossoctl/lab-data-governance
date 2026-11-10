# System tests for label lineage on authored spans

A case is one line. The line is rendered to sidecar spans, the spans go through the real OTLP
receiver and the real processors (interactions, data lineage, classification, label lineage,
leg-ready with the risk observer) against a real Postgres and a real OPA loaded with the suite's one
rule (T-SENS, `rules.py`: a PII label, own or inherited, to an external destination is blocked), and
the result is read back from the tables. Each case runs twice, with
`RISK_INHERITED_LABELS` on and off, in about two seconds. No cluster, no live app, no mock of the
system: the only stand-in is the classifier's own recorded output, replayed through its own seam.

```
U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑
```

`X>Y[...]` is one payload X sends to Y (`U` user, `A` agent, `T` tool, `L` LLM, `V` external
partner, `P` a store no sidecar sees). `s1` is a sensitive unit whose label the case declares
(`units: {s1: MRN}`), `-` is content nobody cares about, `=s1` is bytes equal to s1 that are not
that unit here, `s1@key` sets the JSON key the value travels under. `,` is the next payload of the
conversation, `..P..` an invisible hop (two payloads, there and back, so the numbering stays the trunk's), `⚑` the egress the verdict is read at, `~10min>` a later
conversation. Consecutive `A>T[...]`, `T>A[...]` pair into one exchange; an answer that comes later
closes the open request it answers, so nested calls sit between request and response.

## Run

```
export DOCKER_HOST=unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
export TESTCONTAINERS_RYUK_DISABLED=true
.venv/bin/pytest tests/system -m opa                 # all cases; writes tests/system/REPORT.md and cases/*/result.md
                                                     # a subset (-k, -x) or SYSTEM_REPORT_TAG=x writes REPORT-partial.md / REPORT-x.md only
.venv/bin/pytest tests/system -m opa -k C1           # one case
LABEL_CROSS_TRACE_WINDOW_HOURS=0.25 ...              # window for cross-conversation cases (branches with slice 2)
```

The `opa` mark is excluded by the repository default (`-m 'not opa and not live'`); pass `-m opa`.
One Postgres container and one OPA container start per session (a few seconds); each case then
takes about two seconds per mode.

## Calibrate

`findings.json` beside each `case.yaml` holds what the real NER model found in every payload the
case puts on the wire, with the model generation and the date. Produce or refresh it with the venv
that has the `classification` extra (`uv sync --extra classification`, about 2 GB):

```
.venv-classif/bin/python -m tests.system.calibrate tests/system/cases/C1 [more case dirs]
```

The model loads once (about 20 s); each payload takes well under a second. The test replays the
recorded annotations through `Detector.detect`, so classification runs its own code on the model's
own output; a payload with no recorded findings fails loudly. The case's `classifier.must_see` /
`must_miss` state what the author assumed the model would do; disagreements are printed in the
report, never silently accepted.

**Authored classifier.** A case with `classifier.authored: true` treats the classifier as a parameter:
calibration runs the real model, then subtracts its findings on the case's units at the `must_miss`
legs (never invents one), records the subtraction in `findings.json`, and the report says
"authored" on the case. Decided 2026-11-09: the real model labels an SSN or a date by value
under any key, so a wire-level classifier alone already blocks them; label lineage's claim does not
depend on where one model happens to miss. The real-model cases (C1, C2, K0) keep the plumbing honest.

## The cases

`trunk_cases.py` writes them: one conversation of sixteen payloads (`U>A`, `A>L`, `L>A`, `A>T1`,
`T1>V1`, `V1>T1`, `T1>A`, `A>L`, `L>A`, `A>T2`, `T2>V2`, `V2>T2`, `T2>A`, `A>L`, `L>A`, `A>U`) and
fourteen routes on it: C1–C6 in one conversation, K0–K7 across two (each case's `notes` says what it
proves; the `cell` groups them). Three controls on the memoryless rule of #348,
P1–P3: a tool that calls two partners inside one call, two callers of one tool in flight, and the same
with one value in both calls (`T1(A1)>V1` names the call a request belongs to).

## The grid

Units are rows, payloads are columns. `●` the unit is here and the classifier labelled it, `○` here
but not labelled, `≡` bytes equal to the unit that are not the unit (`=s1`), `━` an edge the
derivation drew into this cell, `┈` seen earlier but no edge arrived, `·` absent. An edge into a
`≡` cell is a false positive; a `┈` cell is a miss. The `neighbourhood` row counts, per crossing,
how many of the units born beside this one recur with it (`k of n`).

## Status of a case

`PENDING` (with `expect.pending: <reason>`) is a case the branch cannot pass yet, counted apart so the
suite stays green; the day it passes while still marked pending it goes RED, so the mark is removed.
An edge into a leg from an earlier holding of the same unit, beside the expected edge from the later
holding, is reported as "also drawn", not red: the agent remembers, so both edges are true; the short
chain says where the value was born, the long one which way it went.

- **GREEN**: the verdict pair matches `expect.verdict`; the edges match `expect.edges` (or, when
  `edges: derived`, the naive rule: a unit present here and at an earlier leg of the same
  conversation with no invisible hop between gets an edge; extras and misses are reported).
- **RED**: it does not, and the case has no `cannot`. The test fails.
- **RED-BY-DESIGN**: it does not, and `expect.cannot` says why no wire observer could get it right.
- **PENDING**: it does not, and `expect.pending` says why this branch cannot pass it yet.

## Files

| file | job |
|---|---|
| `caseline.py` | parse the line; the naive expected-edge rule |
| `render.py` | line to sidecar span rows (the `spans`-table row shape `tools/load_trace.py` replays) |
| `classify.py`, `calibrate.py` | the replay detector and the calibrate command |
| `engine.py` | receiver, the five drains, real OPA, rows; the neighbourhood measurement |
| `grid.py` | the unit grid (units as rows, legs as columns) |
| `report.py` | judge a case; `REPORT.md` and `cases/<id>/result.md` |
| `cases/<id>/` | `case.yaml`, `findings.json`, `result.md` (generated) |
