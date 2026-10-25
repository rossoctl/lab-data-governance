# The pharmacy-refill pitch film

A 2:35 narrated film of the [pharmacy-refill scenario](../README.md), made in October 2026 for a
non-technical audience. Sentry is a knight guarding a castle; the agents are its squires; DG UI
screenshots are laid over the scenes at the moment the narration names them. Everything is made
locally: stills with Z-Image Turbo (mflux), the voice with Kokoro, frames with headless Chrome,
the video with ffmpeg.

The finished film is [`sentry_pitch_demo.mp4`](sentry_pitch_demo.mp4) (2:35, 1920×1080, 55 MB), stored
through Git LFS: a clone without `git lfs` installed gets a small pointer file in its place.

## What is here

| path | what |
|---|---|
| `sentry_pitch_demo.mp4` | the finished film, narrated; `scripts/mux.sh` overwrites it |
| `scripts/narration.txt` | the narration, one `beat\|text` line per sentence; the beat names the scene |
| `film/film.html`, `film/timeline.js` | the page that draws the film, and its timeline. Every cue is anchored to a spoken word, so rewording a line moves its cues with it |
| `film/layout.js` | positions measured on the chosen stills; a new still means new numbers here only |
| `film/cues.js` | the line and word timings of the current voice render, written by `scripts/cues.sh`; committed so the page loads without re-voicing |
| `overlays/ui/` | the DG UI screenshots (below) |
| `overlays/fonts/` | Fira Sans and Fira Mono, with their licenses (OFL) |
| `scripts/prompts/v3/` | one image prompt per scene, `STYLE\|description` |
| `scripts/render_stills.sh`, `scripts/tts.py`, `scripts/cues.sh`, `scripts/render/render.mjs`, `scripts/mux.sh` | the pipeline, in order |

Ignored (`.gitignore`): what the pipeline generates (`stills/`, `voice/`, `build/`, `scripts/render/node_modules/`) and the earlier cuts and experiments kept beside it, including `pitch_rosso/`, the rossoctl cut.

## The screenshots

From the 2026-10-19 run. They are the most reusable part for slides or a longer demo.

| file | shows |
|---|---|
| `exec_flow_full.png` | the whole flow graph: the client, three agents, four tools, the LLM, two external hosts |
| `risk_flow.png` | the same graph with the risky edge, `enroll-adherence → api.adherence-insights.example`, in red |
| `lookup_reply.png` | `lookup-patient`'s response: Rosa's full record |
| `classify_l1.png` | level 1 findings: `PN` Rosa Delgado and `OPID` 4471820 in one payload, RESTRICTED (identity bundle) |
| `dest_l1.png` | the destination, `pharmacy/enroll-adherence → api.adherence-insights.example:8000` |
| `alert_l1.png` | the critical `pii_to_untrusted_external` alert |
| `opid_l2.png` | level 2: alone, `4471820` classifies as `OPID` |
| `mrn_born_l2.png` | level 2 label lineage: `4471820` is an MRN, born at #203801 `pharmacy-assistant → lookup-patient` response |
| `cross_session_l3.png` | level 3: the same label, born in another trace (`36b88b54…`) |
| `fix_payload.png` | the fixed payload: `drug`, `fill_date`, `refill_due` |
| `no_alerts_fix.png` | "No alert has been raised for this trace" |
| `obs_dashboard.png` | the rossoctl `v0.8.0-alpha.2` Observability Dashboard, with its Data Governance card |

## Rebuilding the film

Needs an Apple Silicon Mac (mflux runs on MLX), Google Chrome in `/Applications` (the `CHROME`
constant in `render.mjs`), ffmpeg, Node and uv. All commands run from this directory.

**Stills.** Two seeds per scene into `stills/draft_v3/`, then the picks below, upscaled to 1920×1080
into `stills/v3/`, which `film.html` reads. The same seeds should give close images; a different
mflux version can drift, and `layout.js` is measured on these exact pictures.

```sh
uv tool install --python 3.13 mflux --with "pillow<12.3"
mkdir -p build
SET=v3 SEEDS="7 21" scripts/render_stills.sh

D=stills/draft_v3 O=stills/v3; mkdir -p $O
for pick in 01_rosa_app:s7 02_hall_squires:s21 03_knight_drawbridge:s7 04_rule_scroll:s21 \
            05_l1_inspect:s7 06c_portcullis:s21 07_l2_snip:s21 08_l2_lantern:s7 09_l3_vault_day:s21 \
            10_l3_night_clerk:s21 11_fix_cross:s7 12_rosa_refill:s21; do
  id=${pick%%:*}; s=${pick##*:}; out=${id/06c_portcullis/06_gate_shut}
  ffmpeg -v error -y -i $D/${id}_$s.png -vf "scale=1920:-1:flags=lanczos,crop=1920:1080:0:8" $O/$out.png
done
ffmpeg -v error -y -i $D/13_logo_s21.png -vf "crop=600:600:212:200" $O/13_logo.png
```

**Voice.** Kokoro's `af_heart` at 1.1×. `tts.py` writes one WAV per line, the joined narration, and
the line and word timings; `cues.sh` hands the timings to the page.

```sh
uv venv --python 3.12 ~/.venvs/kokoro
uv pip install --python ~/.venvs/kokoro/bin/python "kokoro>=0.9.4" soundfile pip
PYTORCH_ENABLE_MPS_FALLBACK=1 ~/.venvs/kokoro/bin/python -I scripts/tts.py scripts/narration.txt voice af_heart 1.1
scripts/cues.sh
```

**Render and mux.**

```sh
(cd scripts/render && npm ci)
(cd scripts/render && node render.mjs --stills 5,47.9,69)   # preview frames -> build/preview/
(cd scripts/render && node render.mjs)                      # silent film -> build/film_silent.mp4
scripts/mux.sh                                              # narration on, -16 LUFS -> sentry_pitch_demo.mp4
```

To look at one moment in a browser, serve this directory (`python3 -m http.server`), open
`/film/film.html`, and call `seek(<seconds>)` in the console.

**Changing the words.** Edit `scripts/narration.txt`, re-voice, run `cues.sh`, render. A cue is
written as `W("opening words of the line", "word")`; if an edit removes a word a cue is anchored to,
`timeline.js` throws `cue not found` and names it.
