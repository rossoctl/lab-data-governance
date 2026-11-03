# Technical film v4.4: the changes, old → new

**Made 2026-11-03**: `final/sentry-technical-film-v4.4.mp4`, 9:43 (v4.3 was 8:54), from a new take, with its subtitles and review
beside it; a 3-minute changes reel, `final/sentry-technical-film-v4.4-changes.mp4`. v4.3 and its take are in the earlier zip.

## Level 1 needs no lineage

The verdict at level 1 stands on the classifier's findings alone. Take v4.2's OPA input for that report, run in OPA against the
pinned policy with the carried-in item removed, still gives critical and block (DG-001, DG-004).

| v4.3 → v4.4 | v4.3 | v4.4 |
|---|---|---|
| 4:31 → 4:51, spoken | The first and third rules fire on what is found here. The second, for health data, fires on the number: lineage knows it is her medical record number. | The first and third rules fire on her name. |
| same line, ring | the "carried in" block | the "found here" block; label "Her name / the first and third rules" |
| 4:09 → 4:28, ring | "Found here" and "Carried in" | "Found here" only |

In the real run the second rule also fires, through lineage, and OPA's answer on screen lists all three. Level 2 tells why.

## One policy, more than one app

| v4.3 → v4.4 | v4.3 | v4.4 |
|---|---|---|
| 2:37 → 2:53, spoken | In this demo, Sentry holds the company policy as three rules. | In this demo, Sentry holds the company policy as six rules, graded from critical to low. Three of them matter for this story. |
| same line, on screen | three rows | six rows; the next line rings the three |

The three new rules: card data shared internally (high, escalate), personal data in a financial context (medium, allow), financial
information (low, allow). None changes a pharmacy verdict in this take.

## The decision, said one way

| v4.3 → v4.4 | v4.3 | v4.4 |
|---|---|---|
| I5, 4:54 | …suggested enforcement block. | …suggested enforcement: block. |
| I6, 6:29 → 6:42 | O P A now gets the carried label. The same three rules fire: critical, recommend block. | O P A now gets the carried label. Three rules fire. The decision: risk level critical, suggested enforcement: block. The ring now holds OPA's answer. |
| I7, 7:27 → 7:43 | …Critical, recommend block, on the same three rules, linked back to Rosa's refill. | …The decision: risk level critical, suggested enforcement: block, linked back to Rosa's refill. |

## The pictures

| v4.3 → v4.4 | change |
|---|---|
| 0:03 | the tap's outline follows the button |
| 0:35 | the map's box holds report-dispense, which it cut through |
| 1:01 → 1:17 | "One exchange" on the client and the assistant |

## New: the risk dashboard, 9:03–9:35, in I9

| spoken | on screen |
|---|---|
| Sentry watches every app in the cluster, and its risk dashboard shows the whole picture. Here is the pharmacy, beside a travel booking app, under the same policy. | the page; ring on the four tiles |
| Of 193 exchanges, 3 are critical. 12 are high and 10 medium: card and financial data moving inside the travel app. Worth reviewing, not an emergency. | the distribution |
| These are the rules behind those numbers. | Top rules |
| At the top: the sessions that sent Rosa's number to the forecaster, the three we just followed. | Alerts; the three critical rows |

The words follow the run: this run shows no low exchange (every exchange the low rule touches also meets a stricter one), so the line
names high and medium, and the policy's low grade is seen in Top rules.

## The take

New, on a new cluster, from the package's runbook: the same five verdicts. Counts: 105 exchanges judged (v4.3: 102), 29 interactions in
level 1 (28). Other screens are re-shot from it, so ids and dates
differ. The last music piece is newly composed, same style, for the longer ending. The pitch's own words are unchanged.
