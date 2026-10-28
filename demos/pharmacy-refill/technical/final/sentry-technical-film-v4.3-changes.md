# Technical film v4.3: the changes, old → new

**Made 2026-10-28**: `final/sentry-technical-film-v4.3.mp4`, 8:54 (534.5 s; v4.2 was 9:17), from
v4.2's take, with its subtitles and review beside it; a 3.9-minute changes reel, `final/sentry-technical-film-v4.3-changes.mp4`.
Where the film differs from the plan below: I1 enters through the pitch's "26 · one tap" counter, not the graph (so
the two dives at the shared cut differ: the app through the counter, Sentry's console through the graph); I4 L1
says "In this demo, Sentry holds the company policy as three rules" (the review read "written down in Sentry" as
saying who writes them); the first rule's ring (I4 L2) is on its rule information, the camera close. Not done: the
pitch's graph crop under the "Risky" tag still shows the earlier run (unchanged from v4.2).

The base is v4.2 (9:17) on its own take. Every change is made offline: no cluster, no new screen. Times are v4.2's. The
notes were written on v4/v4.1, whose times run 5–9 s later from 3:00 on. The "read alone" note is already fixed in v4.2:
it says "red".

## Order

1. "That one tap sets off 26 agent and tool interactions" now comes before "Under the hood". The under-the-hood section
   (the catalogs and the map) moves from after "…which triggers an agentic flow" to after "…One it marks as risky, let's
   see why", and runs straight into Sentry's console. The pitch's opening, 0:00–0:54, plays without a break.

## Spoken

| v4.2 | v4.2 says | v4.3 says |
|---|---|---|
| 1:02 | Each node is an agent, a tool, the model, or a vendor. | Each node is an agent, a tool, or the model. Some tools belong to outside vendors, like the forecaster. |
| 2:39 | In Sentry, the company policy is written down as three rules. | In this demo, Sentry holds the company policy as three rules. |
| 2:43 | Rule DG-001: personal data, sent to an untrusted external destination. Its suggested enforcement is block. | The first rule: personal data, sent to an untrusted external destination. Its suggested enforcement is block. |
| 2:52 | Rule DG-002 says the same for health data, and DG-004 for restricted data. | The second says the same for health data, and the third for restricted data. |
| 3:14 | Sentry evaluates the rules with OPA, the Open Policy Agent. Here is rule DG-001, in Rego, as OPA runs it in this cluster. | Sentry evaluates the rules with OPA, the Open Policy Agent. Here are the first rule's conditions, as Sentry shows them. |
| 3:24 | Any flow that carries an item tagged PII, to a destination that is external and untrusted, fires the rule. | Any flow that carries personal data to a destination that is external and untrusted fires the rule. |
| 4:05 | Level one, in Sentry: the report to the forecaster, from Rosa's refill. | Here is what actually happens inside Sentry, at level one: the report to the forecaster, from Rosa's refill. |
| 4:15 | Classification reads it. A trained model finds each value and names its type, and a catalog gives each type its tags and its sensitivity. | Classification reads it: it finds each value, names its type, and gives it its tags and its sensitivity. |
| 4:40 | Rules DG-001 and DG-004 fire on what is found here. DG-002, for health data, fires on the number: level two shows why. | The first and third rules fire on what is found here. The second, for health data, fires on the number: lineage knows it is her medical record number. |
| 4:58 | Sentry raises an alert and recommends the block. In this setup it does not hold the call: the report still goes through. | Sentry raises an alert and recommends the block. |
| 5:59 | Level two, in Sentry. The report now sends only the number. | Here is what actually happens inside Sentry, at level two. The report now sends only the number. |
| 6:17 | …her name and her medical record number, together. Here the label is born. | …her name and her medical record number, together. Here the label is created. |
| 7:11 | Level three, in Sentry. The nightly job runs as a session of its own, with its own trace. In this demo it runs a few minutes after the refill. | Here is what actually happens inside Sentry, at level three. The nightly job runs as a session of its own, with its own trace. |
| 8:15 | The fixed app, in Sentry. The report now sends the drug and the dates. No patient key. | Inside Sentry, the fixed app. The report now sends the drug and the dates. No patient key. |
| 8:59 | …from one run on rossoctl: 5 sessions, 102 exchanges judged, 3 flagged. | …from one run on Kubernetes: 5 sessions, 102 exchanges judged, 3 flagged. |

Kept as is:
- 0:10, "running on rossoctl, an agent platform on Kubernetes". This is the one mention, said over rossoctl's own catalog,
  whose header reads Rossoctl.
- Both "read alone" lines.

## On screen

| v4.2 | v4.2 shows | v4.3 shows |
|---|---|---|
| 0:17 | 5 agents · 8 tools / each in its own pod, beside its sidecar | 5 agents · 8 tools / each in its own pod |
| 0:29 | map card: the pharmacy app on rossoctl: five agents and eight tools, each beside its own sidecar | the pharmacy app: five agents and eight tools, each in its own pod |
| every section | banner: UNDER THE HOOD · live on rossoctl | UNDER THE HOOD · live on Kubernetes |
| 2:43 | DG-001 / personal data → … (ring on the rule's conditions) | The first rule / personal data → … (ring on its risk level and enforcement) |
| 2:52 | DG-002 · health data · DG-004 · restricted | The second · health data · the third · restricted |
| 3:14–3:30 | the rule in Rego (our panel): "Rule DG-001 in Rego", then "PII · external · untrusted" | Sentry's own page for the first rule, its Conditions table ringed: "Its conditions / as Sentry shows them", then "Personal data · external · untrusted / all three in one flow: the rule fires" |
| 4:05 | Level 1 · version A | Level 1 · the full record |
| 4:40 | Carried in: the number is an MRN / health data · DG-002 (level 2 shows why) | Carried in: the number is an MRN / health data · the second rule |
| 4:58 | A recommendation / the alert is raised; the call went through: POST → 200 (over the HTTP response) | An alert · recommend block (over Sentry's verdict for that exchange) |
| 5:59 | Level 2 · version B / only the number | Level 2 · only the number / the report's payload |
| 6:17 | Hop 1 · the label is born | Hop 1 · the label is created |
| 7:45 | Critical · recommend block / DG-001 · DG-002 · DG-004 | Critical · recommend block / the same three rules |
| 8:59 | grid: one run on rossoctl · … | one run on Kubernetes · … |

Sentry's own screens still show the rule ids (DG-001…), as the product does.

## Pace

- Each section exit is about 1 s shorter (the camera's pull-out and the hold before the pitch returns).
- Gaps between lines go from 0.3 to 0.2 s.
- The pitch's longest pauses are capped near 1 s.
- The voice speed is unchanged.
- With the two dropped sentences: 9:17 → 8:54 (the sections 380 → 364 s, the pitch's beats about 6 s shorter).

## Not changed

- The take, every verdict and count, and the pitch's own frames.
- The policy shown is the three rules that ran. The graded rule set (critical to low) would mean another policy and a
  re-take: a separate round, only if wanted.

## How it was made

1. 16 lines voiced (ElevenLabs), all nine sections re-rendered (the pace touches every exit), the map and the grid.
2. Joined, subtitles written, reviewed: words heard vs the script 1.8 % of 1,281 (the one long miss, I5 L5, is heard
   word for word when its 10 s are transcribed alone), -17.0 LUFS, 1 of 149 lines flagged (the pitch's own "live in the
   next release"). Gemini's six must-fixes are all the pitch's own picture, as in v4.2: the old graph under "Risky",
   the BLOCK stamps and "→ blocked" bars, the closing table's "Block", the tiny alert crop. An earlier draft's five
   "audio glitches" (at 3:29, 3:59, 4:29, 5:29, 6:29, on Gemini's 30-s chunk edges) were checked against the source
   lines and are not in the film.
3. The changes reel (`film/long/v4/reel/pairs.py` + `reel.py`): seven before/after picture pairs, eleven new-words
   clips, the order pair, four cards.

