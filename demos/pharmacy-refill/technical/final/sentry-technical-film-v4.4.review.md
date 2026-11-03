# Review of film-v4.4.mp4

2026-11-03 00:45 · review.py · Gemini cost $0.57 (gemini-3.8-flash: 5,299 in / 43,539 out, gemini-4-argon: 179,506 in / 4,754 out)

## Script vs heard: WER 1.1% of 1356 words

- insert: script «» · heard «the app»
- replace: script «rule s» · heard «rules»
- replace: script «organization» · heard «organisation»
- replace: script «check» · heard «cheque»
- replace: script «organization» · heard «organisation»
- replace: script «model s» · heard «models»
- replace: script «sentry s» · heard «this»
- delete: script «lineage» · heard «»
- replace: script «batch s» · heard «batches»
- replace: script «sentry» · heard «3»

## Frozen frames ≥ 1.5 s: 5, 13.1 s in all

- 8:44.9 · 4.2 s · film
- 8:49.3 · 2.0 s · film
- 8:51.2 · 2.4 s · film
- 9:37.5 · 1.9 s · film
- 9:39.6 · 2.6 s · film

## Loudness

-17.0 LUFS integrated · true peak -4.1 dBFS · LRA 3.0 LU

## Claims: 2 of 162 lines need a look

- **contradicts** 0:09.3 «That one tap sets off 26 agent and tool interactions.» — In this take (session A), the tap sets off 29 interactions (8 model calls and 21 among agents and tools); 26 was from an earlier run. (fact: counts: one tap (session A) = 29 interactions, 8 of them calls to the model (qwen2.5:7b), 21 among agents and tools; across the five sessions Sentry judged 105 exchanges and flagged 3 (A, B, N1); the pitch's "26" comes from an earlier run)
- **contradicts** 9:04.3 «Three watches every app in the cluster, and its risk dashboard shows the whole picture.» — Sentry (not 'Three') monitors the apps and owns the risk dashboard. (fact: dashboard: Sentry's own risk dashboard over the last 24 hours, one policy; build: Sentry lab-data-governance 335fc64)

## Gemini watched the film

The film's most significant limitation is a persistent contradiction between the re-voiced narration (which carefully states that Sentry "recommends" a block) and the pitch spine's on-screen graphics, which repeatedly claim the calls are actually "blocked" and "enforced"—a discrepancy made worse when technical screenshots visibly show `HTTP POST -> 200 (ok)` right above the `block` tags. In addition, the opening castle scene's repainted red banner names `report-dispense`, which contradicts the underlying graph crop circling `enroll-adherence -> api.adherence...`. Several push-in transitions between the medieval scenes and the technical sections use an awkward picture-in-picture zoom where the next diagram expands out of an unrelated part of the frame, and the 18-second silent wait while the trace graph builds at 0:59 stalls the momentum early on. Finally, dense UI tables, JSON payloads, and undefined acronyms (`PI`, `PHI`, `PII`, `OPID`, `PN`, `A2A`) pass by too quickly or without explanation for a single viewing.

- **must-fix** · 0:20–0:23 · claim · The repainted red "Risky" banner names `report-dispense -> external host`, but the red circle on the graph directly below it highlights `enroll-adherence -> api.adherenc...xample:8000` (from an older run), and `report-dispense` is not on the graph at all.  
  evidence: At 0:20–0:23 (and again at 0:51–0:52 and 1:51–1:52), the red banner reads "Risky / report-dispense -> external host", while the red circle on the graph encloses the nodes `enroll-adherence` and `api.adherenc...xample:8000`.  
  suggestion: Replace the graph crop under the banner with the current run's graph showing `report-dispense -> demand-forec...xample:8000`.
- **must-fix** · 4:01–4:04 · legibility · An alert table row pops up at the bottom-left of the knight shot with tiny text that is completely illegible and is only on screen for 3 seconds.  
  evidence: At 4:01–4:04, a wide, thin table row (`1 | Crit... | pii_to_untrusted_external...`) appears over the bridge on the bottom left.  
  suggestion: Remove this tiny overlay from the pitch shot, since the full-screen technical section immediately following it shows the exact policy event legibly.
- **must-fix** · 4:05–4:15 · claim · The visual metaphor (iron portcullis slamming shut with sparks and a giant "BLOCK" stamp) and the on-screen subtitle ("Local enforcement") tell the viewer that Sentry actively blocks the network call, contradicting the voiceover ("recommends blocking it") and the truth file (`never_say: that Sentry blocks, stops or prevents a call`).  
  evidence: At 4:05–4:15, the gate slams shut, a red "BLOCK" stamp appears, and the subtitle reads "Local enforcement / most data governance tools do this · so does Sentry".  
  suggestion: Update the subtitle to "Local risk detection" and, if possible, change the stamp to "ALERT · BLOCK" so the visual doesn't claim active request blocking.
- **must-fix** · 5:48–5:56 · claim · The summary banners in the archive room explicitly state `-> blocked` for both Level 1 and Level 2, contradicting the narrator saying "recommends a block" and the truth file rule that Sentry does not block calls.  
  evidence: On-screen text at 5:48–5:56 (and 6:53–6:56): "① All context in place -> blocked" and "② Four hops, out of context -> blocked".  
  suggestion: Change `-> blocked` in the on-screen banners to `-> block recommended` or `-> flagged`.
- **must-fix** · 8:01–8:05 · claim · The on-screen subtitle reads "Sentry doesn't fix the app / it enforces the policy", while the narrator simultaneously says "it applies the policy"—and both border on claiming active enforcement, which the truth file prohibits.  
  evidence: At 8:01–8:05, subtitle: "Sentry doesn't fix the app / it enforces the policy"; voiceover: "Sentry doesn't fix the app, it applies the policy."  
  suggestion: Change both the subtitle and voiceover to "it checks the traffic against the policy" or "it flags policy violations".
- **should-fix** · 0:16–0:19 · claim · On-screen subtitle claims every interaction is "allowed · or blocked", which contradicts the truth file's rule that Sentry only recommends blocking and does not hold or block calls in this setup.  
  evidence: On-screen text at 0:16–0:19: "Every interaction checked against company policy / allowed · or blocked".  
  suggestion: Change the subtitle to "allowed · or flagged" or "recommended allow · or block" to match the re-voiced narration.
- **should-fix** · 0:50–0:53 · seam · The transition from the architecture diagram back to the castle hall shrinks the beige diagram into the top-right corner over the castle before it disappears, then zooms into the dark graph in the center. The spatial relationship between the two frames is broken.  
  evidence: At 0:50–0:53, the "Rosa's refill" diagram shrinks into a floating box in the upper right of the castle hall, vanishes, and then the camera pushes into the dark graph on the left.  
  suggestion: Dissolve or pan cleanly between the diagram and the trace view rather than shrinking the diagram into an arbitrary corner of the castle shot.
- **should-fix** · 0:59–1:16 · pacing · Dead air for 17 seconds while the trace graph slowly populates at 7x speed with only background piano music.  
  evidence: From 0:59 (after "...sped up.") to 1:16 ("Each node is an agent..."), there is no narration while nodes and edges pop in one by one.  
  suggestion: Speed up the trace build to 15x–20x so it finishes in 4–5 seconds, or narrate the explanation of the nodes and edges while the graph is building.
- **should-fix** · 1:48–1:51 · claim · The UI shows `http POST -> 200 (ok)` right above `Risk: critical | block`, which will confuse engineers into wondering whether the call succeeded or was blocked, since the film never explains that `block` in the UI is only a recommendation and that the call actually went through.  
  evidence: At 1:48–1:51 (and again at 4:20–4:23 and 5:01–5:04), the right-hand pane shows `http: POST -> 200 (ok)` above `Risk: critical | block` and `Enforcement: block`.  
  suggestion: Briefly state in the voiceover at 2:40 or 5:02 that in this monitoring mode, the requests still return `200 OK` while Sentry logs the `block` recommendation.
- **should-fix** · 2:06–2:43 · legibility · The "How Sentry sees" diagram contains three columns of dense small text (JSON payloads, exchange IDs, and five stacked boxes on the right) that cannot be read in full while following the narrator's highlights.  
  evidence: At 2:06–2:43, small grey subtitles like `facts only: who, to whom, what. The meaning is Sentry's job.` and the JSON payload fields are too small to read comfortably alongside the moving yellow highlight boxes.  
  suggestion:  Enlarge the text in the three columns or zoom into each column as the narrator discusses it.
- **should-fix** · 2:52–3:00 · legibility · The Risk rules table has 6 rows and 5 columns of small text (`Rule`, `Categories`, `Risk`, `Enforcement`, `Source`) that are on screen for only 7 seconds before cutting away.  
  evidence: At 2:52–3:00, the full table of six rules (`DG-001` through `DG-013`) is shown at full width with small font sizes.  
  suggestion: Zoom in on the first three rules (`DG-001`, `DG-002`, `DG-004`) when they are highlighted at 3:09.
- **should-fix** · 4:29–4:50 · clarity · The UI and subsequent JSON panels rely heavily on regulatory and entity-type acronyms (`PI`, `PII`, `PHI`, `PN`, `OPID`, `MRN`) without ever defining what `PI` vs. `PII` or `PN` vs. `OPID` mean in Sentry's taxonomy.  
  evidence: At 4:29–4:40, the Classification tab shows `RESTRICTED | PI, PII | Identity bundle`, `Carried in: RESTRICTED | PHI, PI, PII`, and types `DOB`, `OPID`, `PN`.  
  suggestion: Add a brief callout or spoken phrase clarifying that `PN` is Person Name, `OPID` is Operational ID, and how `PI` (Personal Information) differs from `PII` in the rules.
- **should-fix** · 4:51–5:00 · attention · At 4:51, the yellow highlight box on the left (`Her name / the first and third rules`) and the voiceover only explain why `DG-001` and `DG-004` fire, then at 4:54 `OPA's answer` on the right shows `DG-001`, `DG-002`, and `DG-004` all triggered without the narrator explaining that `DG-002` fired from the `carried in` lineage block below the highlight.  
  evidence: Narrator at 4:51: "The first and third rules fire on her name. OPA combines the rules..." while the right-hand box shows `triggered_rules: DG-001, DG-002, DG-004`.  
  suggestion: Mention that the second rule (`DG-002`) also fires from the `PHI` tag carried in by lineage.
- **should-fix** · 5:55–5:57 · seam · The transition into the Level 2 technical section zooms into the empty space between the two text banners on the archive wall rather than zooming into a screen or card.  
  evidence: At 5:55–5:57, the camera pushes into the gap between the two `-> blocked` banners and cross-fades into the Sentry UI.
- **should-fix** · 7:10–7:12 · seam · The transition into the Level 3 technical section pops a miniature dark window over the monk's chest/desk before zooming into it, rather than zooming into the lineage card already on screen.  
  evidence: At 7:10–7:12, the `Recent traces` screen appears as a floating rectangle over the monk and expands to full screen.  
  suggestion: Push into the existing lineage card on the bottom left or use a clean dissolve.
- **should-fix** · 7:35–7:43 · claim · In the Level 3 cross-session lineage pane, the narrator says "The records got that number in Rosa's refill", and the callout says "in the trace of Rosa's refill", but the trace ID shown (`in trace bddb9b1c...`) is actually Session B (Level 2, where the app was modified to send only the number), not Session A (`33978695...`). A viewer who notices the trace IDs will wonder why Session A's refill wasn't the source.  
  evidence: At 7:35–7:43, the UI shows `origin: #7326 pharmacy-assistant -> lookup-patient response in trace bddb9b1c...` and `previous: #7605 review-refill -> records:8000 request in trace bddb9b1c...`.
- **nit** · 0:23–0:25 · seam · The first technical section cuts in with a hard zoom out of the dark graph into a blinding white UI screen (`rossoctl`), which is visually jarring compared to the darker theme used in the rest of the film.  
  evidence: At 0:23–0:24, the dark castle hall and dark graph cut/zoom directly into the bright white background of the `rossoctl` Agent Catalog.  
  suggestion: Use a dark-mode theme for `rossoctl` if available, or soften the transition with a brief cross-dissolve.
- **nit** · 0:28–0:30 · audio · The narrator pronounces the CLI/platform name `rossoctl` letter-by-letter as "Rosso-C-T-L" rather than the standard Kubernetes convention "rosso-control" or "rosso-cuddle", which may sound unnatural to Kubernetes engineers.  
  evidence: Narrator at 0:28: "...running on Rosso-C-T-L, an agent platform on Kubernetes."
- **nit** · 2:04–2:06 · seam · The "How Sentry sees" diagram zooms out from the center of the knight's chest/shield rather than from an established screen element.  
  evidence: At 2:04–2:06, the dark technical diagram scales up from the middle of the knight standing on the bridge.
- **nit** · 5:20–5:31 · claim · The dates in the pitch's Level 2 table crop (`2026-10-19` and `2026-11-18`) do not match the dates in the live run's Level 2 payload (`2026-11-02` and `2026-12-02`) shown 37 seconds later.  
  evidence: At 5:21–5:31, the table overlay shows `DATE | 2026-10-19` and `DATE | 2026-11-18`. At 5:58–6:08, the Under the Hood payload shows `"fill_date": "2026-11-02"` and `"refill_due": "2026-12-02"`.  
  suggestion: Repaint the dates in the 5:21 card to match the `2026-11-02` / `2026-12-02` dates from the current run, just as the other cards were repainted.
- **nit** · 7:12–7:19 · attention · In the `Recent traces` list, the highlighted row for the nightly batch (`pharmacy-ops a2a message/stream` at `20:17:02`) is surrounded by 16 other rows, most of which have yellow `Missing parent` badges in the `Status` column, pulling the viewer's eye to what looks like errors.  
  evidence: At 7:12–7:19, 11 rows in the `Status` column show yellow `Missing parent` lozenges while the narrator discusses the nightly batch row.  
  suggestion: Check the `Hide missing-parent traces` box in the UI before recording, or crop tighter to the `Real root` traces.
- **nit** · 8:06–8:18 · claim · In the pitch's "Once the app is fixed" card, the dates are `2026-10-19` and `2026-11-18`, which immediately change to `2026-11-02` and `2026-12-02` when the camera pushes into the live UI at 8:13.  
  evidence: At 8:06–8:12, the card shows `"fill_date": "2026-10-19"`, `"refill_due": "2026-11-18"`. At 8:14, the live payload shows `"fill_date": "2026-11-02"`, `"refill_due": "2026-12-02"`.  
  suggestion: Repaint the dates in the 8:06 pitch card to `2026-11-02` and `2026-12-02`.
- **nit** · 8:54–8:55 · seam · The transition from the summary comparison table into the final technical section zooms into the word "Out" on the second row of the table rather than a logical anchor.  
  evidence: At 8:54–8:55, the camera zooms into the left side of row 2 (`② Out of context, four hops away`) and dissolves into `Sentry's own verdicts`.
