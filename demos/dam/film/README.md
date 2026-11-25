# Sentry on DAM, the film

A 6:38 narrated film (`sentry_on_dam.mp4`) of Sentry's two components, aiac and dg, on DAM: one
issue-triage agent's MCP tool calls with no Sentry, with aiac, and with aiac + dg. The DAM UI is a
real recorded session; the film around it is an HTML timeline rendered frame by frame.

| Path | What it is |
| --- | --- |
| `ui/record.mjs` | Drives DAM's UI through the story and records it to `build/ui/<take>/` (session video and step events) |
| `ui/cleanup.sh` | After a take: reopens s-and-p-team/demo-public#1 and deletes the comments the take posted |
| `scripts/film_data.py` | Writes `film/data/film-data.js` from a take (steps, agent id, aiac's Rego) |
| `scripts/narration.txt` | The narration, one line per beat, with `{hold=N}` silences |
| `scripts/tts.py` | Speaks the narration with Kokoro into `voice/<voice>/` |
| `scripts/fit_holds.py` | Sets the holds so each typed prompt plays at its real speed |
| `scripts/cues.sh` | Exposes line and word timings to the film as `film/cues.js` |
| `scripts/render/render.mjs` | Renders `film/film.html` in headless Chrome to `build/film_silent.mp4` |
| `scripts/mux.sh` | Adds the narration at -16 LUFS and writes `sentry_on_dam.mp4` |
| `scripts/render_stills.sh`, `scripts/prompts/` | The illustrated scenes in `stills/final/` |

## Rebuilding

```sh
PW_FROM=~/development/dam/packages/e2e/playwright node ui/record.mjs take3   # or reuse film/media/take3.mp4
python3 scripts/film_data.py take3
python3 scripts/tts.py scripts/narration.txt voice af_heart
python3 scripts/fit_holds.py take3 && python3 scripts/tts.py scripts/narration.txt voice af_heart
scripts/cues.sh
(cd scripts/render && npm i playwright-core && node render.mjs)
scripts/mux.sh
```

`../sentry-overview-slide.html` is the one-slide overview for the meeting: aiac (intent-based, and
role-based in the colleague's Software Developer Plus demo) and dg (classification-based
enforcement, and data lineage, which the demos don't show).
