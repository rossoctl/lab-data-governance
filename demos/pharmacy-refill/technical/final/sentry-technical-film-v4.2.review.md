# Review of long-v4.2-music.mp4

2026-10-26 10:43 · review.py · Gemini cost $0.45 (gemini-3.8-flash: 11,270 in / 13,756 out, gemini-4-argon: 177,786 in / 3,538 out)

## Script vs heard: WER 0.7% of 1291 words

- replace: script «to a» · heard «the»
- delete: script «most» · heard «»
- replace: script «catalog» · heard «catalogue»
- replace: script «model s» · heard «models»
- replace: script «records» · heard «record»
- replace: script «red» · heard «read»
- replace: script «s» · heard «is»

## Frozen frames ≥ 1.5 s: 4, 12.9 s in all

- 8:48.7 · 4.5 s · part
- 8:53.6 · 4.4 s · part
- 9:11.2 · 2.0 s · part
- 9:13.5 · 2.0 s · part

## Loudness

-17.0 LUFS integrated · true peak -4.3 dBFS · LRA 3.0 LU

## Claims: 6 of 139 lines need a look

- **contradicts** 0:36.4 «That one tap sets off 26 agent and tool interactions.» — In take-facts.yaml for film v4, one tap (session A) is 28 interactions, while the pitch's '26' comes from an earlier run. (fact: take-facts.yaml: 'one tap (session A) = 28 interactions, 8 of them calls to the model (qwen2.5:7b), 20 among agents and tools... the pitch's "26" comes from an earlier run')
- **unsupported** 4:00.8 «data governance tools can do it, and so does Sentry.» — Claim about 'most data governance tools' has no source in the truth files. (fact: LONG-PLAN.md: '"most data governance tools can do it" | no source')
- **words** 7:03.6 «Sentry's cross-session lineage carries the full context across sessions, so this leak is caught too.» — Voice vocabulary strictly bans 'cross-session lineage' / 'cross-session lineage propagation' in favor of 'data lineage' and 'across conversations'. (fact: voice-facts.yaml: 'say: ["data lineage"] never: ["lineage propagation", "cross-session lineage propagation"]', LONG-PLAN.md: '8 | the vocabulary: "lineage", "cross-session lineage" | DG's tab is called "Data lineage" since 10-22')
- **words** 8:40.9 «keeping the company in line with data protection regulations.» — Voice vocabulary strictly bans claims of compliance / data protection regulations. (fact: voice-facts.yaml: 'never: ["real-time prevention", "compliance", "any product claim beyond what is on screen"]', 'the demo shows DG's verdict, not a legal ruling')
- **words** 8:50.5 «Sentry sees its whole history, protecting data locally,» — Voice vocabulary prohibits claims of 'prevention' or 'protecting data/safe' beyond recommendations. (fact: voice-facts.yaml: 'never: ["stops", "blocks the call", "prevents", "the data never left", "safe", "real-time prevention"]')
- **unsupported** 9:08.7 «Sentry, with lineage and risk, is live in the next release.» — The release timeline claim is unsupported/flagged as local only in LONG-PLAN.md. (fact: LONG-PLAN.md: 'end card: "live in the next rossoctl release" | cross-session lineage (slice 2) is local, not on origin/risk')

## Gemini watched the film

The film successfully marries the medieval pitch metaphor with concrete technical proof, but it suffers from several unaddressed visual contradictions between the pitch's older crops and the live system's current reality. Most notably, the pitch's graph crops show older tool names (`enroll-adherence`, `api.adherence...`) right under a new callout naming `report-dispense`, and dates shift between October 19 and October 25 across adjacent scenes. A few key technical concepts—such as what `PI` vs `PII` means, why `DG-003` is missing, and how `account_ref` relates to `external_id`—are shown on screen without explanation, leaving careful viewers to guess. Finally, the core tension between the pitch's visual metaphor of a gate slamming shut ('BLOCK') and the technical reality that Sentry only recommends blocking while letting the call return `200 (ok)` is handled in the voiceover, but still creates cognitive dissonance during the medieval scenes.

- **must-fix** · 0:50–0:53 · claim · The red callout above the graph says `report-dispense -> external host`, directly contradicting the red-circled nodes directly beneath it, which read `enroll-adherence` and `api.adherenc...xample:8000`.  
  evidence: Red badge: 'Risky / report-dispense -> external host' pointing to red circle around `enroll-adherence` -> `api.adherenc...xample:8000`.  
  suggestion: Replace the graph inset in the pitch with the trace graph from the current take so the circled node actually says `report-dispense` and `demand-forecast.example`.
- **should-fix** · 0:41–0:53 · claim · The pitch's graph crop shows older tool and host names (`enroll-adherence`, `api.adherenc...xample:8000`, `api.courier...est.me:8000`) that contradict the app architecture just explained at 0:22 (`report-dispense`, `demand-forecast.example`, `courier`).  
  evidence: On screen in the graph inset: nodes `enroll-adherence`, `api.adherenc...xample:8000`, `api.courier...est.me:8000`.  
  suggestion: Repaint the node labels in this pitch crop to match the current take's tool and vendor names, just as other pitch cards were repainted.
- **should-fix** · 1:19–1:22 · legibility · The right-hand Interaction panel is cut off on the right edge of the frame, truncating the summary text and interaction ID.  
  evidence: On screen: `summary: pharmacy/pharmacy-assistant -> pharm...` and `interaction_id: a9fa49e9-e27f-5c8f-885d-7ccea36f785...` clipped by the right edge of the video.  
  suggestion: Pan the crop slightly right or widen the viewport capture so the right-hand inspector column isn't clipped.
- **should-fix** · 3:51–3:53 · legibility · The alert table row at the bottom left is far too small to read in the 2.5 seconds it is on screen, and its date (`10/19/2026`) contradicts the `2026-10-25` date in the Under the Hood sections.  
  evidence: Tiny table crop at bottom-left at 3:51–3:53 showing `1 | critical | pii_to_untrusted_external... | 10/19/2026, 10:05:38 PM`.  
  suggestion: Either enlarge the alert crop and update its timestamp to match 2026-10-25, or omit it since the Under the Hood section shows the exact policy event clearly.
- **should-fix** · 3:54–4:04 · story · Visual vs. verbal contradiction: the voice carefully says 'Sentry recommends blocking it' (and at 4:59 'it does not hold the call; the report still goes through'), but the pitch visuals show a heavy iron portcullis slamming shut with sparks and a giant red 'BLOCK' stamp, followed at 4:00 by a caption reading 'Local enforcement'.  
  evidence: Voice at 3:53: 'so Sentry recommends blocking it.' Picture at 3:54: gate slams shut, red 'BLOCK' stamp, caption at 4:00: 'Local enforcement / most data governance tools do this · so does Sentry'.  
  suggestion: Change the pitch's subtitle at 4:00 from 'Local enforcement' to 'Local detection' or 'Local inspection', and at 8:03 from 'it enforces the policy' to 'it checks the policy', so the on-screen text doesn't claim active blocking.
- **should-fix** · 5:23–5:33 · claim · The dates in the pitch's inset table (`2026-10-19` and `2026-11-18`) come from an older run and contradict the dates in the live Sentry console shown immediately after at 6:00 (`2026-10-25` and `2026-11-24`).  
  evidence: At 5:23, table shows `DATE | 2026-10-19` and `DATE | 2026-11-18`. At 6:00, payload shows `"fill_date": "2026-10-25", "refill_due": "2026-11-24"`.  
  suggestion: Repaint the dates in the 5:23 and 8:07 pitch cards to `2026-10-25` and `2026-11-24` so they match the live take.
- **should-fix** · 6:18–6:37 · clarity · The voice counts four hops ('Hop 1... Hop 2... Hop 3... Hop 4'), which matches the four-hop animation at 5:38, but at 4:18 and 4:24 the Classification tab in Sentry says `from 2 earlier steps`.  
  evidence: At 4:24, UI reads: `Carried in (data lineage) RESTRICTED PHI, PI, PII from 2 earlier steps`. At 6:18–6:36, narrator walks through Hop 1, Hop 2, Hop 3, Hop 4.  
  suggestion: This is a minor discrepancy between how the UI counts prior agent steps vs. total message legs, but worth noting if viewers read the '2 earlier steps' text.
- **should-fix** · 8:02–8:06 · claim · The on-screen subtitle says 'it enforces the policy' while the narrator says 'it applies the policy', contradicting the earlier explanation (4:59) that Sentry only recommends blocking and does not enforce/hold the call.  
  evidence: On-screen text at 8:02: 'Sentry doesn't fix the app / it enforces the policy'. Voice: 'Sentry doesn't fix the app; it applies the policy.'  
  suggestion: Change the on-screen subtitle from 'it enforces the policy' to 'it checks the policy' or 'it flags policy violations'.
- **should-fix** · 8:07–8:20 · claim · Just like at 5:23, the pitch's payload card shows older dates (`2026-10-19` and `2026-11-18`) right before the push-in to the live UI at 8:15, which shows `2026-10-25` and `2026-11-24`.  
  evidence: At 8:07, card shows `"fill_date": "2026-10-19", "refill_due": "2026-11-18"`. At 8:15, live UI shows `"fill_date": "2026-10-25", "refill_due": "2026-11-24"`.  
  suggestion: Update the dates on the 8:07 card to match `2026-10-25` and `2026-11-24`.
- **nit** · 0:18–0:21 · clarity · The callout introduces the term 'sidecar' before the voice or film has explained what a sidecar is (which doesn't happen until 1:52).  
  evidence: Callout text: '5 agents · 8 tools / each in its own pod, beside its sidecar' while voice says 'each in its own pod.'  
  suggestion: Remove 'beside its sidecar' from this early callout, or have the voice briefly mention it here.
- **nit** · 0:37–0:38 · seam · During the pull-out transition back to Rosa, the architecture map shrinks into the phone screen rather than reversing the push-in from the phone's UI.  
  evidence: From 0:36 to 0:38, the white 'Rosa's refill' diagram scales down over the green 'Your pharmacy' mobile app screen before vanishing.  
  suggestion: Cross-dissolve or zoom out from the phone screen itself so the spatial metaphor stays consistent.
- **nit** · 1:45–1:49 · attention · The voice lists the three pillars in the order 'lineage, classification, and risk assessment' while the third card ('Risk assessment') is still dimmed, and the subtitle on the 'Classification' card says 'an ID' while later screens call it 'a date'.  
  evidence: Voice: 'It's built on lineage, classification, and risk assessment.' Cards light up one by one; Classification card reads 'Labels each value: a name, a record number, an ID.'  
  suggestion: Time the card highlights tightly to each spoken word.
- **nit** · 2:23–2:27 · clarity · The yellow callout 'Risk assessment' overlaps and obscures the bottom caption ('facts only: who, to whom, what. The meaning is Sentry's job.') in the middle column.  
  evidence: At 2:23, the yellow box 'Risk assessment / the policy, then a verdict' covers the words 'The meaning is Sentry's job.'  
  suggestion: Place the callout to the left or below so it doesn't cover existing diagram text.
- **nit** · 2:52–2:57 · clarity · The rules table jumps from `DG-001` and `DG-002` to `DG-004`, with no `DG-003` shown or explained. Engineers watching closely will wonder what happened to rule 3.  
  evidence: Table rows: `DG-001`, `DG-002`, `DG-004`. Voice: 'Rule DG-2 says the same for health data, and DG-4 for restricted data.'  
  suggestion: Either renumber `DG-004` to `DG-003` in the demo config or ignore it as a minor quirk.
- **nit** · 4:25–4:31 · clarity · The UI shows multiple acronyms (`PN`, `OPID`, `PI`, `PII`, `PHI`) without explaining the difference between `PI` (Personal Information, which does not fire DG-001) and `PII` (Personally Identifiable Information, which does).  
  evidence: Classification table shows `1958-03-22 | DOB | INTERNAL / PI` and `4471820 | OPID | INTERNAL / PI` vs `Rosa Delgado | PN | RESTRICTED / PII`.  
  suggestion: When introducing the classifier or in Level 2 (around 6:06), briefly clarify that `PI` is internal operational info while `PII` and `PHI` are regulated personal/health data.
- **nit** · 7:21–7:27 · clarity · In the `record_fill` payload, Rosa's number `4471820` is sent under the JSON key `account_ref`, whereas in `report-dispense` it was `external_id`, and in `lookup-patient` it was `mrn`. The voice says 'under her number' without mentioning that Sentry tracks the value regardless of key name.  
  evidence: Payload on screen: `{"drug": "Metformin 500 mg tablets", "account_ref": "4471820"}`.  
  suggestion: Consider pointing out that the key name changed (`mrn` -> `account_ref` -> `external_id`)—it actually makes Sentry's value-based lineage look even more impressive.
