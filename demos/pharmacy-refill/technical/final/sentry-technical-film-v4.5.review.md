# Review of film-v4.5.mp4

2026-11-04 08:53 · review.py · Gemini cost $0.46 (gemini-3.8-flash: 5,302 in / 14,150 out, gemini-4-argon: 180,726 in / 4,088 out)

## Script vs heard: WER 0.6% of 1367 words

- replace: script «rule s» · heard «rules»
- insert: script «» · heard «trusted»
- replace: script «model s» · heard «models»
- replace: script «red» · heard «read»
- replace: script «batch s» · heard «batches»

## Frozen frames ≥ 1.5 s: 5, 12.8 s in all

- 8:43.7 · 4.0 s · film
- 8:48.1 · 2.1 s · film
- 8:50.2 · 2.3 s · film
- 9:41.4 · 1.9 s · film
- 9:43.7 · 2.5 s · film

## Loudness

-17.0 LUFS integrated · true peak -4.2 dBFS · LRA 3.0 LU

## Claims: 2 of 158 lines need a look

- **contradicts** 0:09.4 «That one tap sets off 26 agent and tool interactions.» — In this take, one tap sets off 29 interactions (21 among agents and tools), not 26. (fact: one tap (session A) = 29 interactions, 8 of them calls to the model (qwen2.5:7b), 21 among agents and tools; ... the pitch's "26" comes from an earlier run)
- **contradicts** 3:41.2 «trusted fires the rule.» — The rule fires on untrusted destinations, not trusted. (fact: trust level UNTRUSTED_EXTERNAL)

## Gemini watched the film

The film is most limited by severe audio synthesis glitches on key words ('value', 'block', 'report', 'Sentry') and a 15-second dead silence during the live trace animation. The pitch spine contradicts both the truth file and the technical sections by claiming and showing that Sentry actively blocks calls ('BLOCK', 'blocked', 'enforces the policy') when in this setup it only recommends blocking and the HTTP POST returns 200 OK. In the opening pitch scene, the red 'Risky' callout names `report-dispense` while the graph circle right below it highlights `enroll-adherence` → `api.adherenc...xample:8000`, creating an immediate visual contradiction. Several UI tables and JSON payloads are either too small to read or held too briefly for a single-watch technical audience. Finally, the push-in/pull-out transitions between the medieval scenes and full-screen UI dashboards are jarring.

- **must-fix** · 0:20–0:23 · claim · The red 'Risky' callout banner names `report-dispense → external host`, but the red circle on the graph directly below it highlights `enroll-adherence` → `api.adherenc...xample:8000` (from an older run). The same mismatch reappears at 0:51 and 1:51.  
  evidence: Red banner at 0:20 reads 'Risky / report-dispense → external host', while the red glowing circle on the graph encircles nodes `enroll-adherence` and `api.adherenc...xample:8000`.  
  suggestion: Repaint the graph crop in the pitch scene so the red nodes and circle show `report-dispense` → `demand-forec...xample:8000` (and update '26' to '29' or explain the difference immediately).
- **must-fix** · 0:59–1:15 · pacing · 15 seconds of dead air (music only) while the execution flow graph slowly adds nodes at 7x speed.  
  evidence: From 0:59 ('...sped up.') to 1:15 ('Each node is an agent...'), there is no narration while gray edges and nodes pop in one by one.  
  suggestion: Speed up the trace replay to 20x–30x so it finishes in 3–4 seconds, or narrate the explanation of nodes and edges over the animation.
- **must-fix** · 4:30–4:32 · audio · Severe TTS voice glitch / distortion on the word 'value'.  
  evidence: 4:30: 'It finds each [glitch: v-a-a-l-yoo], names its type...'  
  suggestion: Regenerate the TTS audio for this sentence.
- **must-fix** · 4:59–5:01 · audio · Severe TTS voice glitch / throat-clearing sound on the word 'block'.  
  evidence: 4:59: 'Suggested enforcement: [glitch: b-l-o-o-ck].'  
  suggestion: Regenerate the TTS audio for 'block'.
- **must-fix** · 5:59–6:01 · audio · TTS voice warbles and stretches the word 'report'.  
  evidence: 5:59–6:01: 'The [warble: re-po-o-ort] now sends only the number.'  
  suggestion: Regenerate the TTS audio for this line.
- **must-fix** · 6:29–6:31 · audio · TTS voice severely glitches and drops in pitch on the words 'report tool'.  
  evidence: 6:29–6:31: 'Fulfillment passes it to the [glitch: re-PO-O-ORT tool].'  
  suggestion: Regenerate the TTS audio for this line.
- **must-fix** · 8:30–8:32 · audio · Audio dropout/glitch on the first syllable of 'Sentry'.  
  evidence: 8:30: '[glitch: S-s]entry found the leak, and it confirms the fix.'  
  suggestion: Regenerate the TTS audio for this sentence.
- **should-fix** · 0:17–8:03 · claim · The pitch's subtitle claims interactions are 'allowed · or blocked', and later pitch screens show 'BLOCK' stamps (4:04, 5:44, 7:08), 'blocked' status (5:47), and 'Sentry doesn't fix the app / it enforces the policy' (8:00). In this setup Sentry only recommends blocking; the actual HTTP call returns `POST → 200 (ok)` (shown at 4:22 and 5:00).  
  evidence: 0:17 subtitle: 'Every interaction checked against company policy / allowed · or blocked'; 5:47 text: '① All context in place → blocked / ② Four hops, out of context → blocked'; 8:00 text: 'Sentry doesn't fix the app / it enforces the policy'; vs 4:22 UI showing `http POST → 200 (ok)` and truth file `never_say: that Sentry blocks, stops or prevents a call (it recommends)`.  
  suggestion: Update the pitch's on-screen text and stamps to say 'RECOMMEND BLOCK' / 'flagged' and 'evaluates the policy', or explicitly state in narration that enforcement is in recommendation/audit mode during this run.
- **should-fix** · 0:23–0:24 · seam · The push-in transition from the medieval hall into the white `rossoctl` UI is a fast scale-up of a flat screenshot over the 3D scene; the same abrupt picture-in-picture zoom-out/zoom-in happens at every section boundary (0:51, 1:51, 2:42, 2:51, 3:44, 4:14, 5:10, 5:55, 6:51, 7:10, 7:57, 8:11, 8:33).  
  evidence: At 0:23–0:24 the white Agent Catalog window scales up from the center of the dark castle frame; at 0:50–0:52 the light diagram shrinks into the top-right corner of the castle frame before the dark console zooms up from the bottom-left.  
  suggestion: Use a clean cross-dissolve or a matched camera push into the existing monitor/card frame rather than scaling floating rectangles in and out of arbitrary corners.
- **should-fix** · 1:45–1:50 · clarity · The narrator says 'one exchange turns red: the report to the forecaster' before explaining what the labels on the edges (`PHI, PII`, `PI, PII +1`, `CREDENTIALS, PI`) or the red dashed arrows mean.  
  evidence: 1:45–1:50: 'With the risk layer on, one exchange turns red: the report to the forecaster.' The graph suddenly shows gray badges (`PHI, PII`, `CREDENTIALS, PI`) on every arrow and two red dashed arrows between `report-dispense` and `demand-forec...xample:8000`.  
  suggestion: Briefly state that the risk toggle overlays sensitivity tags on every arrow and highlights policy-violating flows in red.
- **should-fix** · 3:19–3:21 · clarity · Earlier (0:36) the diagram showed `courier` in a 'CONTRACTED' column outside 'INSIDE THE PHARMACY', but here `courier:8000` is classified as 'inside (internal)' because it runs as a service of the cluster. A technical viewer will wonder why a third-party courier runs inside the pharmacy's own Kubernetes cluster.  
  evidence: 0:36 diagram places `courier` under 'CONTRACTED' (outside 'INSIDE THE PHARMACY'); 3:18 narration: 'The cluster's own services count as inside. Here the contracted courier runs as one of them.'  
  suggestion: Clarify in one phrase that in this demo environment the contracted courier's MCP/API gateway is deployed as an internal cluster service (or whitelisted as a trusted partner).
- **should-fix** · 3:48–4:03 · clarity · At 3:48 the narrator says 'In Rosa's record, the context is all there: her name, and beside it, her medical record number. Both are personal data, and the vendor is external... Sending them there breaks the policy.' However, the JSON box shown at 3:48 (`dob`, `mrn`, `found: true`, `phone`, `address`, `patient_name`, `plan_member_id`) is the output of `lookup-patient` (internal), not the payload sent to the external vendor (`drug`, `fill_date`, `refill_due`, `external_id`, `patient_name`, `date_of_birth`, shown at 4:23).  
  evidence: 3:48 card 'Rosa's record / the full context, at the point of decision' shows `"mrn": "4471820", "found": true, "phone": ...` while narration at 3:56–4:02 says 'and the vendor is external, outside the organization. Sending them there breaks the policy.'  
  suggestion: Either show the `report-dispense` payload at 3:48 or clarify in the voiceover that `lookup-patient` retrieves Rosa's record and the app then includes her name and MRN in the report sent to the external vendor.
- **should-fix** · 4:01–4:03 · legibility · The alert table strip at the bottom-left of the frame is tiny and illegible, and only stays up for 2 seconds.  
  evidence: 4:01–4:03: A narrow dark table row appears at bottom-left (`pii_to_untrusted_external...`) with microscopic text.  
  suggestion: Remove the tiny bottom-left crop in the pitch scene (since the full readable Policy event panel is shown right after at 5:01) or enlarge it.
- **should-fix** · 7:11–7:17 · legibility · The 'Recent traces' table is shown at full 1080p desktop resolution with very small font, making the highlighted row (`authbridge | pharmacy-ops a2a message/stream | 17:23:59`) hard to read without pausing.  
  evidence: 7:11–7:17: Full-screen 'Recent traces' table with small gray/white text.  
  suggestion: Zoom in 150–200% on the top half of the 'Recent traces' table so the service names and timestamps are easily readable.
- **nit** · 2:00–2:03 · pacing · The three pillar cards (Lineage, Classification, Risk assessment) appear on screen for only 3.5 seconds—too fast to read their body text while listening to the voice.  
  evidence: 2:00–2:03: Three cards ('Lineage: Carries each value's context, hop by hop, across sessions', 'Classification: Labels each value...', 'Risk assessment: Judges each interaction...') appear at 2:00 and cut away at 2:03.  
  suggestion: Hold the frame for 2–3 more seconds or let the narration read the one-line definitions.
- **nit** · 4:25–4:27 · clarity · The narrator says the payload holds 'her medical record number', while the JSON field on screen is named `"external_id": "4471820"`. The connection that `4471820` is her MRN under a renamed field (`external_id`) is not stated here.  
  evidence: 4:23–4:26 narration: 'Its payload holds her name, her birth date, and her medical record number.' On-screen JSON shows `"external_id": "4471820"`.  
  suggestion: Say 'and her medical record number, passed here as external_id'.
- **nit** · 5:20–5:30 · claim · The dates in the pitch's Level 2 crop (`2026-10-19`, `2026-11-18`) and Fixed App crop (8:04) come from an earlier run and do not match the dates in the live Sentry section right after (`2026-11-03`, `2026-12-03` at 6:00 and 8:15).  
  evidence: 5:20 card shows `DATE 2026-10-19` and `DATE 2026-11-18`; 6:00 payload and classification table show `2026-11-03` and `2026-12-03`.  
  suggestion: Repaint the dates in the 5:20 and 8:04 overlay cards to `2026-11-03` and `2026-12-03` to match the rest of the film.
- **nit** · 7:15–7:17 · clarity · The narration calls the trace 'the nightly job' / 'at night', while the highlighted trace timestamp on screen reads `17:23:59` (23 minutes after the 17:00 refill).  
  evidence: 7:15 narration: 'The nightly job runs as a session of its own'; screen shows `Started 17:23:59`.  
  suggestion: Optionally mention that the nightly batch was triggered manually in the test run, or zoom so the focus is on `pharmacy-ops` rather than the 17:23 timestamp.
- **nit** · 8:53–9:15 · claim · At 8:53 the summary card says '5 sessions · 105 exchanges judged · 3 flagged', and then at 9:03 the Risk dashboard shows '78 total' traces and '254 total' monitored interactions (and at 9:30 the Alerts table shows `df902ff5410...` with 29 interactions and `b85898b08...` with 28 interactions). How the 5 sessions (105 exchanges) relate to the 78 traces (254 interactions) on the dashboard is not explained.  
  evidence: 8:53 bottom text: '5 sessions · 105 exchanges judged · 3 flagged'; 9:03 dashboard top bar: 'Traces: 78 total / Monitored interactions: 254 total'.  
  suggestion: Briefly note at 9:08 that the 254 interactions on the cluster dashboard combine the 105 exchanges from the five pharmacy sessions with 149 exchanges from the travel booking app.
