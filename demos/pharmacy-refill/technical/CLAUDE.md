# The technical film — for a Claude agent working here

This folder reproduces one film: **Sentry** (a data-governance system for AI agents) on the pharmacy-refill app, the
2:35 pitch with nine technical sections (v4.2, 9:17, `final/`). Read `README.md`, then `RUNBOOK.md`;
"The shape of a job" below says how to run one end to end. `pins.yaml` holds every commit and setting.

What a person usually wants, and where it is:

| ask | do |
|---|---|
| set it up / does my machine have what it needs | `setup/sentry-film.sh check`, then `plan`, then `up` (RUNBOOK § 0–3) |
| record a new take | `take/sequence.sh <name>` (§ 4), then `take/film.sh check <name>` (§ 5) |
| rebuild the film | `take/film.sh all <name>` (§ 6); from the shipped take, no cluster: `take/film.sh stage v4.2 && take/film.sh join v4.2` |
| change a line | `film/long/v4/narration.yaml` → `take/film.sh words <take> <section>` (§ 8) |
| change the voice | `film/long/common.py` `VOICE`; its pace: the `VOICE_TEMPO` line at the top of `film/long/v4/film4.py` (the shell's value is ignored) → `take/film.sh words <take>` |
| change the cuts, pictures, music, cards, review | RUNBOOK § 8, one row each |

## Rules

1. **Sentry recommends; it never blocks.** Never say or caption that Sentry blocks, stops or prevents a call. Say
   "recommends blocking"; the film's verdict is a recommendation and an alert (the call went through: POST → 200).
2. **The words follow the run.** A verdict, count or name in the narration must be on the take's screens or in its
   `take-facts.yaml`. After a new take, `take/film.sh check` must pass before the film is built from it; counts come
   from `counts.json`, never typed in.
3. **Takes are evidence.** Never edit a take's recording, ids, counts, stills or OPA inputs by hand (`film.sh`
   regenerates the derived ones from Sentry; what `sequence.sh` recorded is never touched). A broken take is recorded
   again (`take/sequence.sh <new name>`); a take folder is never re-run into.
4. **Never weaken a check** (`setup/sentry-film.sh doctor`, `take/film.sh check`, `review.py`) to make it pass. Fix
   the input.
5. **Your cluster only.** Touch only the cluster in `film.env`. Never `kind delete` a cluster you did not create; never
   `podman system prune`, `podman volume prune` or `podman rmi -f` (the image store holds every cluster's node).
   A delete goes through `CONFIRM=<name>` and only when the person agreed to it.
6. **Keys stay in the environment.** `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`: read from the shell, never written to a
   file, never printed.
7. **One run at a time; refills more than 15 minutes apart** (the label window). `sequence.sh` keeps the spacing;
   `doctor` says how long ago the last conversation was.
8. **Never say who writes the rules** (in time they are read from the policy itself).
9. **"Red alone"** is the voice's spelling of "read alone"; the subtitles write it back as "Read alone". Keep both
   when you touch those lines.
10. **No personal names** in the film or in shareable text beyond the story's characters (Rosa).

## The shape of a job

1. **The machine** (§ 0): `setup/sentry-film.sh check`. Each FAIL line ends with its fix. Install only what the person
   agrees to; a podman machine resize stops every cluster on it — ask first.
2. **The cluster** (§ 1–3): `film.env` (ask which cluster and ports if they have one), then `plan` and show the person
   what it says before `up`. When `plan` or a step asks for `CONFIRM=<name>`, explain what would be deleted and ask;
   never pass it on your own. When a step stops, fix what it prints and run that step again (all are idempotent).
   Done = `setup/sentry-film.sh doctor` prints `ready`.
3. **The take** (§ 4, ~45 min): `take/sequence.sh <new name>`. Nobody else uses the cluster's pharmacy app meanwhile.
   Then `take/film.sh check <name>`: if a verdict differs, stop and report it; never build the film from that take.
4. **The film** (§ 6, ~30 min): `take/film.sh all <name>` (or the steps one by one). A section's line whose count
   changed is spoken again (needs `ELEVENLABS_API_KEY`).
5. **Look** (§ 7): read `review/<film>/REPORT.md`, check each finding against its frame, pull frames under each
   section's rings (`ffmpeg -ss <t> -i <film> -frames:v 1 x.png`), and list what differs from v4.2 (counts, changed
   lines, length, frames).
6. **Changes the person asks for** (§ 8): words → `narration.yaml` / `film4.SAY` → `take/film.sh words <take> <I…>`;
   voice → `common.VOICE`; cuts, pictures, music, cards → their row. A new screen needs a new take. Rebuild only
   what changed, then review again.

Report: the film's path and length, the take's five verdicts, the review's numbers, every check that failed and what
you changed. Say plainly which steps you did not run.

## Done means

- setup: `setup/sentry-film.sh doctor` prints `ready`
- a take: `take/film.sh check <take>` prints "all five verdicts match the film"
- a film: `film/takes/<take>/review/<film>/REPORT.md` read (word error rate, loudness, claims, Gemini's findings checked against the
  frames), you looked at the frames under each section's rings, and you listed what differs from v4.2. Report only
  steps you ran.
