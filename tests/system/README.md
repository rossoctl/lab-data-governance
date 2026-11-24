# System tests for label lineage on authored spans

A case is one line. The line is rendered to sidecar spans, the spans go through the real OTLP
receiver and the real processors (interactions, data lineage, classification, label lineage,
leg-ready with the risk observer) against a real Postgres and a real OPA loaded with the suite's one
rule (T-SENS, `rules.py`: a PII label, own or inherited, to an external destination is blocked), and
the result is read back from the tables. Each case runs three times, in about two seconds per run:
`off` (inherited labels off), `conv` (on, the cross-conversation switch off) and `cross` (both on). No
cluster, no live app, no mock of the system: the only stand-in is the classifier's own recorded output,
replayed through its own seam.

```
U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑, V>T2[-], T2>A[-], A>U[-]
```

`X>Y[...]` is one payload X sends to Y (`U` user, `A` agent, `T` tool, `L` LLM, `V` external
partner). `s1` is a sensitive unit whose label the case declares (`units: {s1: MRN}`; `s2: [NAME, PN]`
lists every type that counts as labelling it), `-` is content nobody cares about, `s1@key` sets the
JSON key the value travels under, `{s1 s3}` is one record (`[{s1 s3} {s5 s6} -]` is an array of two
objects under the key `records`, beside the filler). `,` is the next payload of the conversation, `..V1..` an invisible
hop (two payloads, there and back, so the numbering stays the trunk's), `⚑` the request whose
exchange the verdict is read at, `~10min>` a later conversation. `T>A[...]` answers the open
`A>T[...]`, so nested calls sit between request and response; a request belongs to the one call its
sender is serving, or to the call `T1(A1)>V1` names.

**Nothing is guessed.** Everything the judge compares is stated by the case or read from the rows,
and anything else fails loudly. The renderer puts on the wire exactly the payloads the line writes,
and refuses:
- a conversation that does not start with the user, and a request it never answers;
- a request whose sender serves two calls and names neither, and a request from anyone but the user
  while serving no call;
- a response that could answer two open requests or none (a payload to the user, from an LLM or a
  partner, or from a tool to an agent is always a response);
- `⚑` on a response, and a flagged request whose response carries a unit;
- two units with equal bytes, two values under one JSON key of one object, and a record that nests,
  never closes or is empty.

The loader refuses an unknown or duplicated key anywhere, an `id` that is not its directory's name, a
case without `expect.edges`, an edge naming an undeclared unit or a leg the line lacks, an expected
edge between legs that do not both carry its unit, a level in `expect.edges`, a pending block that
disagrees with what is expected or holds a case for a name that is no production path, a classifier premise that leaves a present unit unstated, evidence
that says `classified` where the premise does not say must see (or the reverse) or names a type the unit
does not list, and a step list that sends a leg twice or never, or ends with work no drain follows. A
leg the derivation never produced (no interaction, leg row or stored payload) is red.

## Across conversations

The keys of a case across conversations (`cases.py` shows each; R1–R10 are the crossing rules of
`docs/label-lineage.md`):

- **`expect.cross`**: what the `cross` run draws beside `expect.edges`. A crossing carries its level,
  `s1 11>22 vo` (value-only) or `co` (corroborated); the edges that rest on it inside the conversation
  carry none (`s1 22>23`). The `off` and `conv` runs must draw exactly `expect.edges`, never pending.
- **`expect.evidence`**: at a leg, per unit, how the cross run knows it: `classified SSN` (the type the
  model gave the unit's own bytes there; R8 states it at the leg the partner received the value),
  `inherited`, `crossed vo|co`, with ` ambiguous` for several origins. A stated cell is compared exactly;
  `[]` says nothing reaches it; a crossed label drawn at a cell the case does not state is wrong. Read from the rows (`engine.evidence_at`): today classified and
  inherited; a crossing's level from its row once the crossing core writes it, and a crossed label carried
  on inside the conversation once that is written.
- **`expect.marks`**: at a leg, `common 4` (a common value and its count) or `incomplete payload_bytes`
  (a limit that cut the result). Every mark drawn must be stated. No row writes one on this branch.
- **`steps`**: `send: 17-32` (legs, a range in its own direction: `32-1` is reversed), `drain` (every
  drain on from where it stopped), `derive: 2` (the conversation queued again through the production
  correction queue, `dg_queue_label_trace`), `reclassify: {7: [s1]}`, `cross: 'on'`. By default: send
  every leg in line order, then drain.
- **`scopes`** (`{V2: call}`): a memory scope declared per party, applied through the production
  declaration: `LABEL_MEMORY_SCOPES` (a JSON object, `{"<natural_key>": "<scope>"}`), set for the label-lineage processor's
  `driver.configured()`, each party named by the natural key the derivation gives it
  (`Renderer.natural_key`: `agent:lab/a`, `tool:lab/t2`, `service:api.v2.partner.example:8000`,
  `llm:host.containers.internal:11434/qwen2.5:7b`). A declared party whose key is no derived entity is red.
  Every run sets the variable, `{}` for a case without `scopes`, so the shell's own value never leaks in.
- **`config`** (`{payload_bytes: 16}`): a limit, applied through the production configuration.
- **`expect.verdict`** per mode, or a decision history per mode: `[allow tier 1, block tier 2]`.

A case that needs a production path this branch lacks (the cross switch, a limit, a
reclassification, the tiers: `engine.NOT_ON_BRANCH`, one row each naming the path
and where the engine applies it) is **held**: not run, and PENDING only when `expect.pending.held` names
exactly those paths. The change that builds a path removes its row and the case runs from then on. The
`cross` run sets the cross-conversation switch through `engine.switches` once it exists;
until then it runs the branch as `conv` does.

## Run

```
export DOCKER_HOST=unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
export TESTCONTAINERS_RYUK_DISABLED=true
.venv/bin/pytest tests/system -m opa                 # all cases; each writes cases/<id>/result.md
.venv/bin/pytest tests/system -m opa -k "test_case[C1]"   # one case
```

The `opa` mark is excluded by the repository default (`-m 'not opa and not live'`); pass `-m opa`.
One Postgres container and one OPA container start per session (a few seconds); each case then
takes about two seconds per mode. A case's `result.md` holds nothing that changes from run to run
unless the result does (the timings go to the test output), so a run that changes nothing changes no
file.

## Calibrate

`findings.json` beside each `case.yaml` holds what the real NER model found in every payload the
case puts on the wire, with the model generation and the date. Produce or refresh it with a venv
that has the `classification` extra (about 2 GB):

```
uv venv .venv-classif
uv pip install --python .venv-classif/bin/python -e '.[classification]'
.venv-classif/bin/python -m tests.system.calibrate tests/system/cases/C1 [more case dirs]
```

The model loads once (about 20 s); each payload takes well under a second, and calibrate prints what
the model found per payload. The test replays the recorded annotations through `Detector.detect`, so
classification runs its own code on the model's own output; a payload with no recorded findings fails
loudly. The case's `classifier.must_see` / `must_miss` state what the author assumed the model would
do, leg by leg: every unit present on a leg is in exactly one of them there. One rule decides whether
the model labelled a unit (`Case.labelled`: a finding of one of its types on its own bytes);
`must_see` wants yes, `must_miss` no, in every run. A disagreement, or a stated leg with no
classification row, is red: the case would test something other than it says.

**Authored classifier.** A case with `classifier.authored: true` treats the classifier as a parameter:
calibration runs the real model, then subtracts its findings on the case's units at the `must_miss`
legs (never invents one), records in `findings.json` which values it removed from which payload, and
the report says "authored" on the case. Every run checks that record against the case's current
`must_miss`; a case edited since calibration fails until it is recalibrated. Decided 2026-11-09: the
real model labels an SSN or a date by value under any key (the calibration shows it often labels the
MRN too), so a wire-level classifier alone already blocks them; label lineage's claim does not depend
on where one model happens to miss. The real-model cases (C1, C2, K0) keep the plumbing honest.

## The cases

`trunk_cases.py` writes them: one conversation of sixteen payloads (`U>A`, `A>L`, `L>A`, `A>T1`,
`T1>V1`, `V1>T1`, `T1>A`, `A>L`, `L>A`, `A>T2`, `T2>V2`, `V2>T2`, `T2>A`, `A>L`, `L>A`, `A>U`) and
six routes on it, C1–C6; K0–K7, two conversations of one agent, ten minutes apart, in which the
partner of conversation 2 returns nothing (K0) or some of conversation 1's values (K1–K7); and three
controls on the memoryless rule of #348, P1–P3: a tool that calls two partners inside one call, two
callers of one tool in flight, and the same with one value in both calls (`T1(A1)>V1` names the call a
request belongs to). K8–K30 are the cases across conversations, most of them K0's
conversations or more of them: the rule, the level on its way, the common value, scope, limits, order and
correction, switches and tiers. Each case's `notes` says what it proves (and, for a case green only
because nothing crosses yet, which deliberate break will make it mean something); the `cell` groups them.

## The grid

Units are rows, payloads are columns, and each result draws two grids: **expected** (the case's
edges, crossings included; `●` must_see, `○` must_miss) above **observed** (what the cross run drew and
labelled; labelled by the same rule as the classifier checks). `●` the unit is here and the
classifier labelled it, `○` here but not labelled, `━` an edge into this cell (expected or drawn),
`┈` held earlier but no edge into this cell, `·` absent. The grid does not say which leg an edge came
from; the reasons line names every missing and extra edge, and the rows table every edge's source.

## Verdict and status

`expect.edges` lists every edge the fold must draw, and the judge compares sets: a missing edge and an
extra edge are both red. Where the agent holds the same bytes twice (the LLM echoed them), its next
request inherits from both holdings, so the case lists both edges (C3: `1>4` beside `3>4`).

The verdict is the flagged exchange's risk record, its `enforcement_type` as written. The harness
sends every span before the processors run, so that record evidences the exchange's response too; the
renderer refuses a flagged exchange whose response carries a unit, and the judge one whose response the
model labelled, so the verdict rests on the request alone.

- **GREEN**: every mode's verdict matches `expect.verdict`, the `off` and `conv` runs drew exactly
  `expect.edges`, the `cross` run exactly those and `expect.cross`, with the stated evidence and marks.
- **PENDING**: the case states, in `expect.pending`, a reason and exactly what this branch gets wrong in
  the cross run (`missing` and `extra` edges, `verdict` modes, `evidence` and `marks` legs), and the run
  differs in exactly that; or it is held (`held`). A pending case that passes, or differs in any other
  way, is RED. A wrong level is a missing and an extra edge, and the reasons name it
  (`level of edge s1 11>22: want co, drawn vo`).
- **RED**: anything else, and always (pending or not) when the run did not finish (a drain stopped before the
  end of its stream, as leg-ready does while OPA is down, or left work in its queue:
  `incomplete run: leg_ready stopped at cursor 8 of 16`), when the rows cannot be read (a leg not derived, an
  edge with an end on no line leg, an edge carrying a type its unit does not list or evidence that is not a
  whole match of its value, no risk record or disagreeing ones at the flagged leg, a labelled response in the
  flagged exchange), when an edge is drawn by two rows of one label type, origin and place in the receiving
  payload (the edge set would hide it), when the classifier premise is broken in any run, or when the `off` or
  `conv` run drew other edges than `expect.edges`.

## Files

| file | job |
|---|---|
| `cases.py` | load `case.yaml` into a `Case` and check it; `Case.labelled`, the one label-matching rule |
| `caseline.py` | parse the line |
| `render.py` | line to sidecar span rows (the `spans`-table row shape `tools/load_trace.py` replays) |
| `classify.py`, `calibrate.py` | the replay detector, the authored record and its check; the calibrate command |
| `engine.py` | receiver, the five drains, real OPA; check every drain finished; read the rows back |
| `grid.py` | the unit grid (units as rows, legs as columns) |
| `report.py` | judge a case; write `cases/<id>/result.md` |
| `rules.py` | the suite's one rule, T-SENS |
| `trunk_cases.py` | writes every `case.yaml` (the files are the record) |
| `conftest.py`, `test_cases.py` | the containers and OPA per session; one test per case; a run leg-ready did not finish is red |
| `test_judge.py` | the judge, the loader and the renderer, without containers: every refusal and every branch; every shipped case against its calibration (held ones too) |
| `cases/<id>/` | `case.yaml`, `findings.json`, `result.md` (generated) |
