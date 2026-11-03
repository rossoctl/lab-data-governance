# Sentry · the technical film (v4.4)

A 9:43 film of **Sentry**, a data-governance system for AI agents, running on Kubernetes beside an agent platform.
Its spine is the 2:35 pitch of the pharmacy-refill demo, kept as it was made: Rosa taps "refill", an agentic flow runs,
and one report leaves the pharmacy for an outside demand forecaster. Nine "under the hood" sections open the pitch's
frames onto the real system: the app on the platform, Sentry recording the tap live, how Sentry sees an exchange, the
policy's rules as Sentry's own pages show them, the exact input Sentry sends OPA, Rosa's number tracked hop by hop on the real graph, the
same number caught in another session, the fixed app allowed, Sentry's own verdicts on all five sessions, and its risk
dashboard over the whole run: the pharmacy beside a travel booking app, under one policy.

This folder reproduces it from nothing on your own Mac (Apple Silicon with podman: the cluster, the platform, Sentry,
the app, a take of five sessions, every still and panel, the film and its review), and lets your Claude Code change
anything in it. Two access gates: the app's repository (s-and-p-team/agent-examples-snp) is private, and the media zip
(`pins.yaml` → media) is on Box behind a sign-in.

v4.4 (2026-11-03) is v4.3 after one more review round, from a new take: level 1 no longer leans on lineage ("The first and
third rules fire on her name"); the risk dashboard is new, closing section I9; the policy is six rules, three of which
the story follows, and the second app shows the others at work; the decision is said one way ("risk level critical,
suggested enforcement: block"); four rings are corrected. Every change, old → new: `final/sentry-technical-film-v4.4-changes.md`;
the reel `final/sentry-technical-film-v4.4-changes.mp4` shows each one (3 min). v4.3 is in the earlier zip,
`sentry-technical-film-v4.3-full.zip`, in the same Box folder, with its take v4.2.

The take holds the five pharmacy sessions as `sequence.sh` recorded them. Its two travel bookings were recorded a second time
after a setup fix (the first bookings found their store empty); the first pass is kept in `travel-first-pass/`. The dashboard's
numbers are the second pass's.

Known since v4.2, kept as made: the pitch's own crops come from an earlier run of the app, so its graph crop still shows
the old vendor (`enroll-adherence` → `api.adherence-insights.example`), "26" interactions and older dates; four Sentry
crops and the small "Risky" tag over that graph are repainted with the take's names, but not the graph under that tag.
Its own review (`final/…review.md`) lists them; repainting more is a change of the film, not of this package.

```sh
cp film.env.example film.env
setup/sentry-film.sh check            # what this machine lacks, and the command for each
setup/sentry-film.sh plan             # what the cluster has, and what setup would change (read-only)
setup/sentry-film.sh up               # platform, Sentry, the two apps, the lineage wiring (~90 min)
take/sequence.sh my-take              # the five sessions and two travel bookings (~50 min)
take/film.sh all my-take              # verdicts checked, stills, panels, the film, its review (~30 min)
```

No cluster? The take v4.4 was made from ships whole in the zip (`film/takes/v4.4/`: its stills, panels, live frames,
the nine rendered sections, the pitch with its repainted cards; a git checkout gets them with
`setup/sentry-film.sh media <the zip>`), so `take/film.sh stage v4.4 && take/film.sh join v4.4 film-v4.4` rebuilds film v4.4 (~15 minutes),
and a changed line re-renders only its section: edit `film/long/v4/narration.yaml`, then
`take/film.sh words v4.4 I8` (one section, the join, the review).

| path | what |
|---|---|
| `final/` | the film as delivered (v4.4), its subtitles, its review, its changes reel |
| `RUNBOOK.md` | every step, what it changes, what to do when it fails, how to change the film |
| `pins.yaml` | the exact commits (platform, Sentry, the app, the pitch) and Sentry's settings for the take |
| `setup/` | `sentry-film.sh` (check · plan · media · sources · cluster · cli · sentry · app · room · instrument · travel · doctor · up · recreate), Sentry's lockfile, the kind config, the six-rule catalog, the travel app's store image |
| `take/` | `sequence.sh` (the take), `take.sh` (one step of it), `film.sh` (the film from a take), `ids.py`, `facts.py`, `compare.py` (a take's film against v4.4's) |
| `film/long/` | the film's code: `v4/film4.py` (sections, join, subtitles), `v4/narration.yaml` (the words), `common.py` (the voice), `steps.js` (the camera), `review.py`, the stills and panel scripts, the voice cache `tts11/`, the score `v4/music/` |
| `film/takes/v4.4/` | the take v4.4 was made from: its traces, the live recording and frames, stills, panels, OPA inputs, counts, ids, facts, the repainted pitch, the nine rendered sections (`out/`) |
| `film.env` | yours, made from `film.env.example`; not in the zip (nor `state/`, which the setup writes per cluster) |
| `film/pitch-2m35/` | the pitch's transcript and the pitch film (pinned by sha256; `setup/sentry-film.sh sources` downloads it if missing) |
| `CLAUDE.md` | for your Claude Code (the zip also carries it as a skill, `.claude/skills/technical-film/`) |

Sentry **recommends**: its verdict is a recommendation and an alert, and the call goes through. Enforcing is a
separate component, not in this film.
