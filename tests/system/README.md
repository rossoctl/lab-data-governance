# System tests for label lineage on authored spans

A case is one line. The line is rendered to sidecar spans, the spans go through the real OTLP
receiver and the real processors (interactions, data lineage, classification, label lineage,
leg-ready with the risk observer) against a real Postgres and a real OPA loaded with the suite's one
rule (T-SENS, `rules.py`: a PII label, own or inherited, to an external destination is blocked), and
the result is read back from the tables. Each case runs twice, with
`RISK_INHERITED_LABELS` on and off, in about two seconds per run. No cluster, no live app, no mock of the
system: the only stand-in is the classifier's own recorded output, replayed through its own seam.

```
U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑
```

`X>Y[...]` is one payload X sends to Y (`U` user, `A` agent, `T` tool, `L` LLM, `V` external
partner, `P` a store no sidecar sees). `s1` is a sensitive unit whose label the case declares
(`units: {s1: MRN}`; `s2: [NAME, PN]` lists every type that counts as labelling it), `-` is content nobody cares about, `=s1` is bytes equal to s1 that are not
that unit here, `s1@key` sets the JSON key the value travels under. `,` is the next payload of the
conversation, `..P..` an invisible hop (two payloads, there and back, so the numbering stays the trunk's), `⚑` the egress the verdict is read at, `~10min>` a later
conversation. `T>A[...]` answers the open `A>T[...]`, so nested calls sit between request and response;
a request belongs to the one call its sender is serving, or to the call `T1(A1)>V1` names.

**Nothing is guessed.** Everything the judge compares is stated by the case or read from the rows,
and anything else fails loudly: the renderer refuses a request whose sender serves two calls and names
neither, a response that could answer two open requests or none, two units with equal bytes, and two
values under one JSON key; the loader refuses a case without `expect.edges`, an unknown key, and a
classifier premise that leaves a present unit unstated.

## Run

```
export DOCKER_HOST=unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
export TESTCONTAINERS_RYUK_DISABLED=true
.venv/bin/pytest tests/system -m opa                 # all cases; writes tests/system/REPORT.md and cases/*/result.md
                                                     # a subset (-k, -x) or SYSTEM_REPORT_TAG=x writes REPORT-partial.md / REPORT-x.md only
.venv/bin/pytest tests/system -m opa -k C1           # one case
LABEL_CROSS_TRACE_WINDOW_HOURS=0.25 ...              # window for cross-conversation cases (branches with slice 2;
                                                     # here, like LABEL_SEED_RULE, it has no Config field and no effect)
```

The `opa` mark is excluded by the repository default (`-m 'not opa and not live'`); pass `-m opa`.
One Postgres container and one OPA container start per session (a few seconds); each case then
takes about two seconds per mode.

## Calibrate

`findings.json` beside each `case.yaml` holds what the real NER model found in every payload the
case puts on the wire, with the model generation and the date. Produce or refresh it with a venv
that has the `classification` extra (about 2 GB):

```
uv venv .venv-classif
uv pip install --python .venv-classif/bin/python -e '.[classification]'
.venv-classif/bin/python -m tests.system.calibrate tests/system/cases/C1 [more case dirs]
```

The model loads once (about 20 s); each payload takes well under a second. The test replays the
recorded annotations through `Detector.detect`, so classification runs its own code on the model's
own output; a payload with no recorded findings fails loudly. The case's `classifier.must_see` /
`must_miss` state what the author assumed the model would do, leg by leg: every unit present on a leg
is in exactly one of them there (the loader refuses a case that leaves one out or lists one in both).
One rule decides whether the model labelled a unit (`Case.labelled`: a finding of one of its types on
its own bytes); `must_see` wants yes, `must_miss` no. A disagreement, or a stated leg with no
classification row, is red: the case would test something other than it says.

**Authored classifier.** A case with `classifier.authored: true` treats the classifier as a parameter:
calibration runs the real model, then subtracts its findings on the case's units at the `must_miss`
legs (never invents one), records in `findings.json` which values it removed from which payload, and
the report says "authored" on the case. Every run checks that record against the case's current
`must_miss`; a case edited since calibration fails until it is recalibrated. Decided 2026-11-09: the real model labels an SSN or a date by value
under any key (the calibration shows it often labels the MRN too), so a wire-level classifier alone already blocks them; label lineage's claim does not
depend on where one model happens to miss. The real-model cases (C1, C2, K0) keep the plumbing honest.

## The cases

`trunk_cases.py` writes them: one conversation of sixteen payloads (`U>A`, `A>L`, `L>A`, `A>T1`,
`T1>V1`, `V1>T1`, `T1>A`, `A>L`, `L>A`, `A>T2`, `T2>V2`, `V2>T2`, `T2>A`, `A>L`, `L>A`, `A>U`) and
fourteen routes on it: C1–C6 in one conversation, K0–K7 across two (each case's `notes` says what it
proves; the `cell` groups them). Three controls on the memoryless rule of #348,
P1–P3: a tool that calls two partners inside one call, two callers of one tool in flight, and the same
with one value in both calls (`T1(A1)>V1` names the call a request belongs to).

## The grid

Units are rows, payloads are columns, and each result draws two grids: **expected** (the case's
edges; `●` must_see, `○` must_miss) above **observed** (what the run drew and labelled, inherited
labels on; labelled by the same rule as the classifier checks). `●` the unit is here and the
classifier labelled it, `○` here but not labelled, `≡`
bytes equal to the unit that are not the unit (`=s1`), `━` an edge into this cell (expected or drawn),
`┈` held earlier but no edge into this cell, `·` absent. An edge into a `≡` cell is a false positive.
The grid does not say which leg an edge came from; the reasons line names every missing and extra edge,
and the rows table every edge's source. The `neighbourhood` row counts, per leg an edge arrives at, how
many of the units born beside this one (at any of its origins, every route followed) recur with it
(`k of n`).

## Status of a case

`expect.edges` lists every edge the fold must draw, and the judge compares sets: a missing edge and an
extra edge are both red. Where the agent holds the same bytes twice (the LLM echoed them), its next
request inherits from both holdings, so the case lists both edges (C3: `1>4` beside `3>4`).

- **GREEN**: the verdict pair matches `expect.verdict` (the risk record that evidences the flagged
  leg, its `enforcement_type` as written) and the drawn edges are exactly `expect.edges`.
- **RED**: anything else, and always when the rows cannot be read (an edge with an end on no line leg,
  an edge carrying a type its unit does not list, no risk record or disagreeing ones at the flagged
  leg), when the classifier premise is broken, or when the on and off runs drew different edges.
- **PENDING**: the case states, in `expect.pending`, a reason and exactly what this branch gets wrong
  (`missing` and `extra` edges, `verdict` modes), and the run differs in exactly that. A run that
  passes, or fails in any other way, is RED.
- **RED-BY-DESIGN**: the same, under `expect.cannot`, for what no wire observer could get right.

## Files

| file | job |
|---|---|
| `cases.py` | load `case.yaml` into a `Case` and check its classifier premise; `Case.labelled`, the one label-matching rule |
| `caseline.py` | parse the line |
| `render.py` | line to sidecar span rows (the `spans`-table row shape `tools/load_trace.py` replays) |
| `classify.py`, `calibrate.py` | the replay detector and the calibrate command |
| `engine.py` | receiver, the five drains, real OPA, rows; the neighbourhood measurement |
| `grid.py` | the unit grid (units as rows, legs as columns) |
| `report.py` | judge a case; `REPORT.md` and `cases/<id>/result.md` |
| `rules.py` | the suite's one rule, T-SENS |
| `trunk_cases.py` | writes every `case.yaml` (the files are the record) |
| `conftest.py`, `test_cases.py` | the containers and OPA per session; one test per case |
| `test_judge.py` | the judge, the loader and the renderer, without containers: every refusal and every branch |
| `cases/<id>/` | `case.yaml`, `findings.json`, `result.md` (generated) |
