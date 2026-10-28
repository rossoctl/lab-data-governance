# Review of film-v4.3.mp4

2026-10-28 08:24 · review.py · Gemini cost $0.48 (gemini-3.8-flash: 4,946 in / 27,686 out, gemini-4-argon: 164,519 in / 4,714 out)

## Script vs heard: WER 1.8% of 1281 words

- insert: script «» · heard «in the app»
- replace: script «rule s» · heard «rules»
- replace: script «organization» · heard «organisation»
- delete: script «personal data restricted going to an external untrusted destination» · heard «»
- replace: script «organization» · heard «organisation»
- replace: script «red» · heard «read»
- replace: script «model s» · heard «models»
- replace: script «red» · heard «read»
- replace: script «records» · heard «record s»
- replace: script «s» · heard «is»

## Frozen frames ≥ 1.5 s: 5, 13.1 s in all

- 8:27.7 · 4.1 s · film
- 8:32.1 · 2.1 s · film
- 8:34.2 · 2.3 s · film
- 8:48.8 · 1.9 s · film
- 8:50.9 · 2.6 s · film

## Loudness

-17.0 LUFS integrated · true peak -4.3 dBFS · LRA 2.8 LU

## Claims: 1 of 149 lines need a look

- **unsupported** 8:48.2 «Sentry, with lineage and risk, is live in the next release.» — No truth file contains release schedule or commercial availability dates.

## Gemini watched the film

The film is most limited by contradictions between the re-voiced narration, the visual stamps, and the product's actual behavior: while the voice carefully says Sentry 'recommends' blocking in Levels 1 and 2, the giant red 'BLOCK' stamps, the gate-slamming metaphor, and the final lines ('it enforces the policy', 'Sentry lets it through', and the summary table) all claim active inline blocking, which the truth file explicitly forbids (`never_say: that Sentry blocks, stops or prevents a call`) and the UI's `POST → 200 (ok)` disproves. Unrepainted remnants from the earlier pitch run—most notably the graph crop circling `enroll-adherence → api.adherenc...xample:8000` right beneath the new `report-dispense` tag—will immediately confuse an engineering audience watching once. Several key technical terms (`PHI`, `PI`, `OPID`, `PN`, `A2A`, `MCP`, `authbridge`, and how exact-match lineage avoids false positives on short numbers) appear on screen without explanation. Finally, the push-in/pull-out transitions repeatedly zoom into or out of unrelated frames (such as zooming out of a dark UI screen into a medieval scroll, or shrinking a full-screen diagram into the corner of a castle hall), and a few dense UI tables and JSON payloads are held too briefly to read.

- **must-fix** · 0:20–0:23 · claim · The red callout tag and the circled nodes in the graph crop underneath it name two different tools and hosts.  
  evidence: The red tag at the top of the crop reads `Risky / report-dispense → external host`, but the red circle on the graph below it highlights `enroll-adherence` and `api.adherenc...xample:8000` (from an earlier run). This same mismatch reappears at 0:51 and 1:36.  
  suggestion: Repaint or replace the graph crop in the pitch spine so the circled nodes show `report-dispense` and `demand-forec...xample:8000`.
- **must-fix** · 3:42–3:44 · legibility · A tiny, wide alert table appears at the bottom-left of the bridge scene for 2 seconds with completely illegible text.  
  evidence: At 3:42–3:44, a dark banner appears at the bottom left (`# | Level | Title | Description | Status...` with `critical | pii_to_untrusted_external...`) in microscopic font while the eye is on the right-hand cards, then vanishes at the cut.  
  suggestion: Either omit this tiny crop (since the real policy event is shown clearly at 4:48) or make it large enough to read.
- **must-fix** · 3:45–3:56 · claim · The visual metaphor (a slamming iron portcullis with sparks and a giant red `BLOCK` stamp) contradicts the voice and truth file, which state that Sentry only recommends blocking and does not hold the call.  
  evidence: At 3:44, the voice says `so Sentry recommends blocking it`, while an iron gate slams shut with sparks and a red `BLOCK` stamp hits the screen (3:45). In the UI at 4:03 (`http: POST → 200 (ok)`), the call actually succeeded. The truth file states: `block_means: Sentry's verdict and an alert, a recommendation; in this setup the call is not held` and `never_say: ... that Sentry blocks, stops or prevents a call (it recommends)`.  
  suggestion: Change the stamp to `RECOMMEND BLOCK` or `ALERT · BLOCK` and clarify in the narration that in this governance mode Sentry flags and recommends blocking rather than dropping the packet.
- **must-fix** · 5:34–5:42 · claim · On-screen text says `→ blocked` for Levels 1 and 2 while the voice says `recommends a block` and the truth file forbids claiming that Sentry blocks calls.  
  evidence: At 5:34–5:42 (and 6:37–6:40), the two summary bars in the archive room read `① All context in place → blocked` and `② Four hops, out of context → blocked`.  
  suggestion: Change `→ blocked` in the on-screen bars to `→ recommend block` or `→ flagged (block)`.
- **must-fix** · 6:52–7:55 · claim · In Level 3, the voice says `so this leak is caught too` while a giant red `BLOCK` stamp hits the screen, and at 7:43–7:55 the film explicitly claims Sentry enforces the policy and lets calls through.  
  evidence: At 6:52, `BLOCK` is stamped on screen. At 7:43, the subtitle reads `Sentry doesn't fix the app / it enforces the policy` while the knight holds up a hand to stop the messenger. At 7:53, the voice says `Sentry lets it through` with a giant `ALLOW` stamp. Truth file `never_say`: `that Sentry blocks, stops or prevents a call (it recommends)`.  
  suggestion: Reword `it enforces the policy` (7:43) and `Sentry lets it through` (7:53) to reflect that Sentry audits/evaluates against policy and clears the exchange (`recommends allow`), matching the truth file.
- **must-fix** · 8:26–8:36 · claim · The final comparison table (`LOCAL ENFORCEMENT` vs `SENTRY`) lists `Block` under `SENTRY` for all three levels, contradicting the truth file and the earlier `recommends block` table.  
  evidence: At 8:26–8:36, the `SENTRY` column reads `Block`, `Block`, `Block`, whereas two seconds later at 8:38 (`Sentry's own verdicts`), the verdicts are accurately shown as `critical · recommends block`.  
  suggestion: Change the column header or cell text at 8:26 from `Block` to `Recommend block` (or `Flag / Block`) so it does not contradict 8:38 and the truth file.
- **should-fix** · 0:31–0:35 · legibility · The Tool Catalog table has 8 rows of small text and long image paths, but is held for only 4 seconds.  
  evidence: At 0:31–0:35, the `Tool Catalog` lists 8 tools (`enroll-copay-card` through `send-refill-reminders`) with long descriptions (`Tool '...' deployed from existing image 'registry.cr-system.svc.cluster.local:5000/...'`) and `MCP` labels while the voice says `five agents and eight tools, each in its own pod`.  
  suggestion: Hold the Tool Catalog a couple of seconds longer or highlight the specific tools used in Rosa's refill (`lookup-patient`, `review-refill`, `schedule-delivery`, `report-dispense`).
- **should-fix** · 0:50–0:53 · seam · Jarring pull-out transition where a light-mode architecture diagram shrinks into the top-right corner of the dark castle hall before vanishing.  
  evidence: At 0:50–0:52, the cream-colored `Rosa's refill` diagram shrinks into a floating rectangle in the upper-right of the medieval hall while the dark graph crop is in the lower-left, then pops off.  
  suggestion: Cross-dissolve or zoom directly from the diagram back into the graph frame rather than shrinking the whole slide into the corner of the hall.
- **should-fix** · 1:35–1:37 · seam · The pull-out transition at 1:35–1:36 briefly flashes the mismatched pitch graph crop (`enroll-adherence`) for one second before cutting to the knight on the bridge.  
  evidence: At 1:35, the real Sentry console dissolves back to the castle hall with the `Risky / report-dispense` tag over the `enroll-adherence` circle for barely a second, then cuts to the exterior bridge at 1:37.  
  suggestion: Cut or dissolve directly from the Sentry console to the knight on the bridge at 1:37 instead of flashing the interior hall for one second.
- **should-fix** · 1:48–1:50 · seam · The `How Sentry sees` diagram zooms out of the middle card (`Classification`) instead of the whole screen or a relevant element.  
  evidence: At 1:48–1:50, the dark `How Sentry sees` diagram scales up from inside the `Classification` card on the bridge, covering the three cards just after they were introduced.  
  suggestion: Use a clean push-in or dissolve after holding the three pillars (`Lineage`, `Classification`, `Risk assessment`) a beat longer.
- **should-fix** · 2:29–3:26 · seam · At 2:35–2:37 and 3:24–3:26, the dark Sentry UI zooms out of and shrinks back into a medieval parchment scroll on a castle door.  
  evidence: At 2:35, the `Risk rules` UI page scales up from the center of the parchment scroll (`Personal data must not leave the organization`), and at 3:24 the `Conditions` table shrinks back onto the scroll before cutting to the bridge.  
  suggestion: Anchor UI zoom transitions to UI crops in the scene (such as the top-left `POLICY RULE` badge) or use a clean dissolve.
- **should-fix** · 2:49–2:53 · legibility · The second and third policy rules (`DG-002` and `DG-004`) are covered in 4 seconds without showing their conditions or explaining `PHI`.  
  evidence: At 2:49–2:53, the voice says `The second says the same for health data, and the third for restricted data` over the 3-row table (`pii_to_untrusted_external`, `phi_to_untrusted_external`, `restricted_external_sharing`), and immediately cuts away.  
  suggestion: Highlight rows 2 and 3 individually and briefly define `PHI` (Protected Health Information) vs. `PII` and `RESTRICTED`.
- **should-fix** · 4:08–4:22 · clarity · The UI shows `Found here (classifier): RESTRICTED | PI, PII | Identity bundle` and `Carried in (data lineage): RESTRICTED | PHI, PI, PII`, and the table below lists `OPID`, `PN`, `PI`, `PII`, `PHI`, none of which are defined for the viewer.  
  evidence: At 4:08–4:22, the Classification tab displays abbreviations (`DOB`, `OPID`, `PN`, `PI`, `PII`, `PHI`, `Identity bundle`) while the voice only says `It finds each value, names its type, and gives it its tags and its sensitivity. Her name is found right here: personal data, restricted.`  
  suggestion: Briefly gloss the key tags (`PN` = person name, `OPID` = operational ID, `PII` = personally identifiable information, `PHI` = protected health information) or add them to the yellow callout.
- **should-fix** · 4:31–4:40 · claim · In Level 1 (`4:05`), the voice claims the payload holds `her medical record number` and at `4:11` says `Classification reads it. It finds each value...`, implying the classifier recognized the MRN locally in Level 1, whereas the UI and truth file show the classifier only saw `OPID` even in Level 1 and got `MRN` from lineage.  
  evidence: At 4:16, the Classification table shows row 1 (`4471820 | MRN | RESTRICTED | PHI, PII | Carried in`) and row 2 (`4471820 | OPID | INTERNAL | PI | Found here`). At 4:23, the `found here: the classifier` JSON block contains `OPID "4471820"` (no `PHI` tag), while `MRN "4471820"` and `"PHI"` are in the `carried in: data lineage` block (`4:32`). Earlier at 3:33–3:55, Level 1 was presented as `All context in place` where `Local enforcement — most data governance tools do this` catches both the name and MRN locally.  
  suggestion: Clarify that in Level 1's `report-dispense` payload, the field is named `external_id` (`4:05`), so the local classifier already sees the number only as `OPID` and catches the leak locally only because `patient_name` is still in the payload; `MRN` and `PHI` (Rule 2) are already relying on lineage from `lookup-patient`.
- **should-fix** · 6:22–6:28 · clarity · The film states that lineage matches the exact value (`matched on the exact value`), raising an obvious engineering question—what if a generic number or string collides with a sensitive value, or what if the value is transformed?—without explaining the scope or window.  
  evidence: At 6:22–6:28, the callout says `Carried in: her MRN / MRN · restricted · PHI, PII · matched on the exact value` and the voice says `At every hop, Sentry matches the exact value, so the label, medical record number, travels with it.`  
  suggestion: Briefly mention how exact-match lineage is scoped (e.g., within the trace or the configured 15-minute cross-session label window) so engineers understand why it works here.
- **should-fix** · 6:38–6:41 · claim · At 6:39, the badge says `SESSION 1 Rosa's refill → shared database`, implying the nightly batch in Session 2 pulled from Session 1 (Level 1), whereas in the actual take (7:18) the lineage came from Level 2 (`trace a4bc18da`), and the shared database is an HTTP `records` service.  
  evidence: At 6:39, the lower-left badge reads `SESSION 1 Rosa's refill → shared database`. At 7:05, the voice explains `The shared database is the pharmacy's records service`, and at 7:18 the UI shows `origin ... in trace a4bc18da...` (which is Level 2, as shown at 8:40).  
  suggestion: Ensure the session numbering and database terminology (`shared database` vs. `records service`) are consistent between the pitch cards and the technical walkthrough.
- **nit** · 0:28–0:30 · audio · Awkward pronunciation of the CLI/platform name `rossoctl`.  
  evidence: The narrator pronounces `rossoctl` as 'rosso-C-T-L' (`running on rossoctl`), whereas Kubernetes engineers usually read `-ctl` tools as 'control' or 'cuddle', and the UI header spells it `Rossoctl`.  
  suggestion: Confirm the intended pronunciation of `rossoctl` and re-voice if needed.
- **nit** · 1:00–1:02 · seam · The `Trace detail` header and tabs abruptly disappear when transitioning into the node explanation.  
  evidence: At 1:00, the top half of the Sentry console (`Data Governance / Traces / Risk`, `Trace detail Live`, `Span tree`, `Execution Flow`) vanishes to black while the graph shifts up.  
  suggestion: Keep the console chrome dimmed in the background rather than cutting it to solid black.
- **nit** · 1:11–1:20 · clarity · The voice reconciles the 26 vs. 28 interaction count, but the viewer has no time to verify it on the graph.  
  evidence: Voice: `The count changes a little from run to run. In this one, Sentry counted 28 interactions for the tap, 8 of them calls to the model.` The graph shows 17 directional arrows with numeric badges (`30645`, `30865`, etc.), and it is not obvious how 17 arrows equal 28 interactions.  
  suggestion: Briefly note or show that single arrows between a pair of nodes can bundle multiple interactions (or point to the `Edges per interaction` toggle).
- **nit** · 5:07–5:17 · claim · The dates in the Level 2 pitch card (`2026-10-19` and `2026-11-18`) do not match the dates in the Level 2 Sentry console right after it (`2026-10-25` and `2026-11-24`).  
  evidence: At 5:07, the overlay table shows `DATE | 2026-10-19` and `DATE | 2026-11-18` (and again at 7:48 in the fixed app card), whereas at 5:44 and 7:57 the actual Sentry payload shows `"fill_date": "2026-10-25"` and `"refill_due": "2026-11-24"`.  
  suggestion: Repaint the dates in the two pitch cards at 5:07 and 7:48 to `2026-10-25` and `2026-11-24` so they match the take.
