# Technical film v4.5: the changes, old → new

**Made 2026-11-04**: `final/sentry-technical-film-v4.5.mp4`, 9:47 (v4.4 was 9:43), from a new take (v4.5) recorded on a machine
set up from empty by this package's runbook, with its subtitles and review beside it; a 57-second changes reel,
`final/sentry-technical-film-v4.5-changes.mp4`. v4.4 and its take are in the earlier zip. Two notes from a review of v4.4,
both on the risk dashboard at the end of I9.

## Top rules, by severity

Sentry's Top rules card listed its rules by a count it does not show (the exchanges each rule fired on), so the dashboard read
low → critical while the Rules page in I4 reads critical → low. Fixed in Sentry, not in the film: the card now ranks by risk
level, then by the traces it shows. The Sentry pin moves from lab-data-governance `335fc64` to `b720d74` (that commit + the fix).

| v4.4 → v4.5 | v4.4 | v4.5 |
|---|---|---|
| 9:25, on screen | Top rules low → critical | critical → high → medium → low, as the Rules page in I4 |
| 9:25, spoken | "These are the rules behind those numbers." | unchanged |

## The lower-severity alerts

| v4.4 → v4.5 | v4.4 | v4.5 |
|---|---|---|
| 9:35, new | | spoken: "Below them, lower-severity traces, listed for follow-up."; a gold ring on the alert rows below the three critical sessions, label "Lower severity · listed for follow-up" |

The line says "listed", not "not blocked": Sentry recommends (escalate for high, allow for medium and low). `film4.py` stops if a
take's dashboard shows no such rows.

## This take's counts

The pharmacy's numbers and the five verdicts are as in v4.4 (`take/compare.py v4.5`). The dashboard counts its 24-hour window, and
this take's two travel bookings gave one more medium and two low exchanges.

| v4.4 → v4.5 | v4.4 | v4.5 |
|---|---|---|
| 9:12, spoken | Of 193 exchanges, 3 are critical. 12 are high and 10 medium: … | Of 254 exchanges, 3 are critical. 12 are high, 11 medium and 2 low: … |
| same line, label | critical · high · medium · none | critical · high · medium · low · none (now written from the take's counts) |

`film4.py` now stops when that line names a grade the take does not show, or leaves out one it does.

## Lengths

I9 42.7 → 47.8 s (the new line); I2 60.7 → 59.5 s (the live graph of this take's tap). The other sections are as in v4.4.

## Review

Word error rate 0.6 % (v4.4 1.1 %), loudness as v4.4. Claims: 2 of 158 lines, both known (the pitch's own "26"; a speech-to-text
split of "untrusted" at 3:41, same audio as v4.4). Gemini's must-fixes are the pitch's own scenes, as in v4.4, plus five
"voice glitches" whose audio is v4.4's unchanged. Full report: `final/sentry-technical-film-v4.5.review.md`.
