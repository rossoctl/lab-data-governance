# RUNBOOK — reproduce the technical film, from nothing

The film (`final/sentry-technical-film-v4.2.mp4`, 9:17) is the 2:35 pitch of the pharmacy-refill demo, as made, with
nine "under the hood" sections spliced into its pauses: Sentry's real pages from a live run, the policy in Rego, the
exact input Sentry sends OPA, the number tracked hop by hop, the verdicts. Every screen in a section comes from a take
on a real cluster. This runbook takes a Mac from nothing to that film, rebuilt from a take of its own.

Every command runs from this folder (the package root). Each script says what it found before it changes anything,
stops with the exact command to run when it needs something, and deletes nothing unless you name it in `CONFIRM=` (the one
exception is the demo app's own `fills` table, emptied before each refill of a take).

| § | what | time |
|---|---|---|
| 0 | the machine: tools, memory, disk, access, keys | ~30 min once |
| 1 | your settings: `film.env` | 2 min |
| 2 | read the cluster before touching it: `plan` | 1 min |
| 3 | set up: platform, Sentry, the app, lineage wiring | ~60 min from nothing (measured: platform 13, Sentry 12 with a warm build cache — ~25 cold —, app 6, instrument 20) |
| 4 | the take: five sessions | 43 min (measured) |
| 5 | check the verdicts | 1 min |
| 6 | build the film | ~30 min (measured: stills 3, OPA + panels 0.5, cards 3.5, sections 8–10, join 4, review 6.5) |
| 7 | review, look, list the differences | ~15 min |
| 8 | change it: words, voice, sections, story | |
| 9 | when something fails | |
| 10 | undo, clean up | |

## 0. The machine

From git (this folder in lab-data-governance) you have the text; the binary parts (the voice cache, the music, the
v4.2 take's recording, stills, cards and rendered sections, the pitch and the finished film) come in one zip, pinned in
`pins.yaml` → media. Download it from the link there (Box: sign in), then
`setup/sentry-film.sh media <the zip>` (it checks the sha256 and never overwrites a file you have). From the zip itself
you already have everything; its text is a snapshot of the day it was made, and the git folder holds the current text.

```sh
cp film.env.example film.env        # § 1 — the defaults fit a Mac with no other kind cluster
setup/sentry-film.sh check          # read-only: every tool, version, memory, disk, port, repo access, key
```

`check` prints one line per item; each FAIL line ends with the command that fixes it. What it looks for:

| item | why | install |
|---|---|---|
| macOS on Apple Silicon | the only proven host | — |
| podman + a running machine, ≥ 9.5 GiB free for this cluster (more if other kind clusters run on it), ≥ 40 GB free disk in it | the cluster's node runs in it; Sentry's classifier image needs ~35 GB to build | `brew install podman && podman machine init --cpus 8 --memory 16384 --disk-size 200 && podman machine start` (a machine you have: `podman machine stop && podman machine set --memory … --disk-size … && podman machine start`; it stops every cluster on it for a minute, and a disk only grows) |
| kind, kubectl, helm, git, git-lfs, curl | the cluster and the platform installer | `brew install kind kubectl helm git git-lfs && git lfs install` |
| uv | Sentry's Python env for the OPA panels (§ 6) | `brew install uv` |
| ollama + `qwen2.5:7b` | the agents' model, on this Mac (`host.containers.internal:11434`) | `brew install ollama && brew services start ollama && ollama pull qwen2.5:7b` |
| `rossoctl` CLI, release in `pins.yaml` | the app's deploy imports agents and tools through it | `gh release download v0.1.5-beta.1 -R rossoctl/rossoctl-cli -p '*Darwin-arm64.tar.gz' && mkdir -p ~/.config/rossoctl && tar xzf rossoctl-*.tar.gz -C ~/.config/rossoctl` (the scripts add `~/.config/rossoctl` to PATH) |
| node + Playwright 1.61 | recordings, stills, panels, section renders | `brew install node && npm install && npx playwright install chromium` |
| python 3.11 with pyyaml, numpy, Pillow, faster-whisper | the film build and its review | `brew install python@3.11 && python3.11 -m pip install pyyaml numpy Pillow faster-whisper` |
| ffmpeg with libx264 and ebur128 | every cut, the loudness check | `brew install ffmpeg` |
| read access to the three repos in `pins.yaml` | the code is built from them | the app's repo (agent-examples-snp) is private: ask its owners for read access, then `brew install gh && gh auth login && gh auth setup-git` (git then uses your GitHub login) |
| `ELEVENLABS_API_KEY` (optional) | a line not yet in the voice cache, new music | export it in your shell; never in a file |
| `GEMINI_API_KEY` (optional) | the review's claim check and its watch pass | export it in your shell; never in a file |

What runs without the keys:

| | no key | needs `ELEVENLABS_API_KEY` | needs `GEMINI_API_KEY` |
|---|---|---|---|
| setup, take, check, stills, panels, cards | yes | | |
| a section whose words are as in v4.2 | yes (the voice cache `film/long/tts11/` holds all 86 lines) | | |
| a line whose words change (an edit, or a count that differs in a new take) | | yes: it stops and names the line | |
| the music | yes (the four pieces in `film/long/v4/music/` stretch to the new lengths) | new pieces only | |
| the review | words heard, frozen frames, loudness | | the claims check and the watch pass (~$0.45 a film) |

## 1. Your settings: `film.env`

| key | default | when to change it |
|---|---|---|
| `CLUSTER` | `sentry-film` | to reuse a kind cluster you have (`kind get clusters`) |
| `HTTP_PORT`, `HTTPS_PORT` | 8080, 9443 | when another cluster or program has them (`check` says so); an existing cluster: its own ports (`podman port <cluster>-control-plane` shows them) |
| `KUBECONFIG` | `~/.kube/sentry-film.kubeconfig` | one file for this cluster: the scripts write the cluster's context into it themselves (`kind export kubeconfig`), also for a cluster you made before; your `~/.kube/config` is not touched |
| `CLI_CONTEXT` | `sentry-film` | the rossoctl CLI's context for this cluster; a name already used for another cluster stops `cli` (pick another); the CLI's current context is never switched |
| `SRC` | `~/sentry-film/src` | where the three checkouts go; outside Dropbox / iCloud is best |
| `LLM_MODEL`, `PYTHON` | `qwen2.5:7b`, `python3.11` | |

## 2. Read the cluster before touching it

```sh
setup/sentry-film.sh plan
```

Read-only. For an absent cluster it says it will be created; for an existing one it lists, per layer, what it found
and what `up` would do: keep the platform or complete it, keep Sentry or rebuild it at the pin (its data kept), keep
the app or replace it. When a layer cannot be fixed without deleting something, it says so here, before anything runs:

| `plan` says | means | your choice |
|---|---|---|
| `sentry: database at 00NN, newer than the pin's 0025` | another, newer Sentry used this cluster; this one cannot run on its data | another cluster (`CLUSTER=`), or drop Sentry's data: `CONFIRM=data-governance setup/sentry-film.sh sentry` (dumps it first to `state/<cluster>/`) |
| `app: other code in: … → replaced, needs CONFIRM=pharmacy` | a pharmacy app of another version is deployed | `CONFIRM=pharmacy setup/sentry-film.sh app`: the app's own `undeploy.sh` deletes namespace `pharmacy` (pods, its database) and its Keycloak clients, then the pin is deployed; the namespace's object list is saved to `state/<cluster>/` first |
| `cluster: exists but does not answer` | its node container is stopped or broken | `podman start <cluster>-control-plane`, or rebuild: `CONFIRM=<cluster> setup/sentry-film.sh recreate` |
| `other namespaces (left alone …)` | other apps run there; their traffic also reaches Sentry | nothing; the take only reads its own sessions |

## 3. Set up

```sh
setup/sentry-film.sh up             # sources → cluster → cli → sentry → app → instrument → doctor
```

or one step at a time (each is idempotent; when one stops, fix what it prints and run it again):

| step | on an empty machine | on a used cluster | time |
|---|---|---|---|
| `sources` | clones kagenti, lab-data-governance, agent-examples-snp into `$SRC` at their pins; pulls the classifier weights (LFS, ~500 MB); downloads the pitch film (checks its sha256); puts the package's `uv.lock` into the Sentry checkout | fetches and checks out the pin; refuses a checkout with local changes or of another repo | ~5 min |
| `cluster` | creates the kind cluster with the gateway on your ports, installs the platform (`setup-rossoctl.sh` at its pin, flags in `pins.yaml`), turns the platform UI's login off (see below) | checks 12 platform pieces; re-runs the installer only when one is missing (it keeps the rest); stops when the cluster's ports differ from `film.env` | ~15–25 min |
| `cli` | a CLI context for this cluster, logged in with the platform's service account | reuses the context; stops if that name points at another cluster | seconds |
| `sentry` | builds Sentry's two images from the pin (each labelled with its commit), loads them into the node, installs Sentry and hooks the platform's collector to it, sets the take's four settings (`pins.yaml` → settings), compiles OPA's policy from Sentry's rule catalog (`deploy/create-opa-configmap.sh`, with Sentry's own Python env made by `uv`; Sentry's installer does not do it, and OPA waits for it) | keeps pods already at the pin; otherwise rebuilds and rolls them, keeping the data; saves every setting it changes in `state/<cluster>/changes.log` | ~25 min first time, ~5 min after |
| `app` | deploys the pharmacy app at its pin through the platform's proxy mode (`deploy.sh`) | keeps an app whose code matches the pin (four pods' code hashed against the checkout); another version needs `CONFIRM=pharmacy` | ~10 min |
| `instrument` | Sentry's lineage wiring on the app's 13 agents and tools, payload capture on, the configs saved for `take.sh relineage`, the plain pods pinned to the digest they run | skips when all 13 are wired and the copies exist | ~20 min |
| `doctor` | read-only: every check the take needs; exit 0 = ready | | 1 min |

Sentry's console is then at `http://dg.localtest.me:<HTTP_PORT>/ui/traces`; the platform's at
`http://rossoctl-ui.localtest.me:<HTTP_PORT>`.

What the setup changes beyond the pinned code, and why:

| change | why |
|---|---|
| the platform UI without a login (`rossoctl-backend ENABLE_AUTH=false`, pins.yaml → platform.ui_auth) | the film shows the platform's Agent and Tool catalogs, shot by a script; the installer turns the login on with SPIRE, and on a cluster not on port 8080 its Keycloak redirect cannot work at all |
| `RISK_INHERITED_LABELS=on` (leg-ready) | data lineage feeds the risk verdict (ships off) |
| `RISK_INHERITED_LABELS_CROSS_CONVERSATIONS=on` (leg-ready) | a label carried in from another session counts too: level 3 (ships off) |
| `LABEL_CROSS_TRACE_WINDOW_HOURS=0.25` (label-lineage) | sessions up to 15 min apart are linked (ships 0 = off); the take's spacing is built on it |
| console memory 1 GiB | its Data lineage reads need it (ships 512 MiB) |
| payload capture on in the 13 sidecars | the film shows payloads (the instrument default is off) |
| plain pods pinned to their digest | they run `:latest` with `imagePullPolicy: Always`; a restart could pull another build |

## 4. The take

```sh
setup/sentry-film.sh doctor          # must print "ready"
take/sequence.sh my-take             # → film/takes/my-take/ (~45 min, mostly two 15.5-min waits; never re-run into a take)
```

Five sessions in the pitch's order:

| run | what | report-dispense key | export key | when |
|---|---|---|---|---|
| A | level 1 · the report sends the full record (recorded live) | `full` | `mrn` | start |
| B | level 2 · only the number | `mrn` | `mrn` | > 15 min after A |
| N1 | level 3 · the nightly batch, its own session | | `mrn` | ~3.5 min after B |
| C | the fixed app · no patient key | `none` | `none` | > 15 min after N1 |
| N2 | the fixed app's nightly batch | | `none` | ~3.5 min after C |

Rules (the script keeps them; keep them when you run steps by hand with `take/take.sh`):
1. **One run at a time, nobody else on the cluster's pharmacy app.** Another conversation or a pod roll changes what
   Sentry records.
2. **A refill starts more than 15 minutes after the run before it.** Sentry links a value to every session in its
   window (0.25 h); a refill inside another's window inherits its labels and the levels blur. `doctor` tells you how
   long ago the last pharmacy conversation was.
3. **`fills` is emptied before each refill** (`take.sh reset`), so each release starts clean and the nightly batch
   sees only its own refill.
4. **A key switch rolls the tool's pod, and a roll drops its lineage wiring** (the platform re-renders the sidecar's
   config on every pod start). `take.sh key` puts the saved config back and waits until the sidecar confirms lineage;
   after any other roll, run `take/take.sh relineage <deployment>` yourself.

## 5. Check the verdicts

```sh
take/film.sh check my-take
```

| run | the call to the demand forecaster must be |
|---|---|
| A, B, N1 | `critical block DG-001,DG-002,DG-004` (A: the courier exchange allowed; N1: its MRN carried in from B only) |
| C, N2 | `none allow` |

If one differs, **stop: the words follow the run, never the reverse.** Find out why (`take/take.sh verdicts <trace>`
lists every exchange with its risk, rules and carried-in labels). The usual cause is a broken rule of § 4: a refill
inside another's window, a key switch without relineage, another conversation. Record a new take. Counts may differ
from v4.2's (A was 28 interactions there); the narration reads them from the take's `counts.json`.

## 6. Build the film

```sh
take/film.sh all my-take             # check → live → ids → counts → facts → stills → opa → panels → cards → stage → join → review (~30 min)
```

| step | makes (in `film/takes/my-take/`) | from |
|---|---|---|
| `live` | `live/*.png`: the graph growing as the refill runs, ×7 | `rec/A.webm` |
| `ids` | `ids.json`: every exchange the stills open, the leg numbers and alert time for the repainted cards | Sentry's database |
| `counts` | `counts.json`: N, M, T, J, F and the five verdicts the narration and the closing grid read | Sentry's database |
| `facts` | `take-facts.yaml`: the review's truth file | `take/take-facts.template.yaml` + the two above |
| `stills` | `shots/{tap,level1,level2,night,fixed,rossoctl,policy}/`, `map/`: PNGs at 2× + the boxes the rings frame | Sentry's and rossoctl's pages, the app's map |
| `opa` | `opa/{A,courierA,B,N1}.json`: the exact OPA input and answer | Sentry's own risk engine code at the pin, the cluster's OPA |
| `panels` | `panels/out/*.png`: the OPA panels, the pipeline, the policy and Rego, the closing grid | `film/long/v4/panels/` |
| `cards` | `cards/`, `pitch-fixed.mp4`, `src/`: the pitch's five Sentry crops repainted with this take's names, the door frames | the pitch film, Sentry's console font |
| `stage` | `out/I1…I9.stage.mp4` + plans: the nine sections | all of the above, the voice cache |
| `join` | `film.mp4` + `film.srt` + `film.plan.json` | the pitch + the sections + the score |
| `review` | `review/film/REPORT.md` (in the take folder) | the film, `take-facts.yaml` |

`film.sh` writes only derived files into the take (`verdicts.txt`, `ids.json`, `counts.json`, the stills, panels,
`out/`, the film); what `sequence.sh` recorded (`traces.txt`, the logs, `rec/`) is never touched. A step can be run alone (`take/film.sh stills my-take`). Before `stage` and `join`, `film.sh` rebuilds whatever a
take lacks that needs no cluster (the live frames from `rec/A.webm`, the repainted pitch and its door frames from
`cards/`). A take's rendered sections stay in `out/`, so after a change only the changed sections are rendered again. The ones that read Sentry need the take's cluster up; the
rest work on the take folder alone, so a take can be rebuilt on any machine with the package. The shipped take
`film/takes/v4.2/` is the one film v4.2 was made from: `take/film.sh stage v4.2 && take/film.sh join v4.2` rebuilds
v4.2 itself with no cluster at all: on the Mac it was made on, byte for byte (an encoder on a machine with another core
count may differ in the last pixels). The stages load their fonts from Google Fonts, so a render needs the network.

## 7. Review, look, list the differences

1. `film/takes/<take>/review/<film>/REPORT.md`: word error rate of the heard voice against the script (v4.2: see `final/`), frozen
   frames, loudness (-16 LUFS integrated), every line against the facts, and Gemini's findings. Findings are leads:
   check each against the frame before acting.
2. **Look at the frames.** Open the film at each section (the plan's `cuts` and `sections` give the times) and check
   that every ring sits on what its label names. A different take moves Sentry's graph: each hop's box in I6 comes
   from the graph (`shots/level2/boxes.json`), so check them one by one.
3. **List every difference from v4.2**: counts (`counts.json` vs `film/takes/v4.2/counts.json`), lines whose words
   changed (`film.plan.json` "script"), length, the frames that look different, the review's numbers.

## 8. Change it

| change | where | then |
|---|---|---|
| a section's words | `film/long/v4/narration.yaml` (`I1…I9`, one key per line; `{N}`-style fields are filled from `counts.json`) | `take/film.sh words <take> I4` (the changed sections) |
| the pitch's lines as re-voiced | `film4.SAY` in `film/long/v4/film4.py` (the pitch's sentence → what the voice says) | `take/film.sh join <take>` (all beats are re-timed) |
| the voice | `common.VOICE` in `film/long/common.py` (ElevenLabs voice id + model); its pace: the `VOICE_TEMPO` line at the top of `film4.py` (1.12; the shell's value is ignored; the cache keeps one file per tempo) | all 86 lines are spoken again (key, credits), then all nine stages, join and review: `take/film.sh words <take>` (~30 min) |
| where a section cuts into the pitch, and its door | `film4.CUT` (a frame of the pitch) and `film4.DOOR` (the rectangle the camera enters) | `take/film.sh cards <take>` (door frames), then `words` |
| a section's pictures | `film4.section()`: each step's `img` (a still), `cam` (the camera's rectangle), `ring`, `label`, `sub`, `tone` | `words <take> <I…>` |
| a still itself | `film/long/v4/shots.js` (Sentry's pages), `film/long/v3/p1/shots.js` (rossoctl), `v3/p3/shots.js` (the policy) | `take/film.sh stills <take>` (needs the take's cluster), then `words` |
| the music | `film/long/v4/score.py` (STORY, HOOD, GLOBAL, PIECES); delete a piece in `v4/music/` to compose it again (key, credits) | `$PYTHON film/long/v4/score.py compose`, then `join` |
| the repainted crops | `film/long/v4/fixes/cards.py` (CARDS: which frames, which lines; the new text comes from `ids.json`) | `take/film.sh cards <take>`, then `join` |
| the review | `film/long/review.py`; its truth file `take/take-facts.template.yaml`; its brief `film/long/v4/review-prompt-onevoice.md` | `take/film.sh facts <take> && take/film.sh review <take>` |
| a changes reel for a reviewer | `film/long/v4/reel/reel.py` (REEL: before/after pairs with times) | `$PYTHON film/long/v4/reel/reel.py before.mp4 after.mp4 reel.mp4` |
| the story (order, a new section) | `film4.CUT`/`DOOR`/`section()` + `narration.yaml` + `score.PIECES`; the pitch itself is not changed | a new take only if a new screen is needed |

A fact said in words often sits in more than one place: "three rules" is in `narration.yaml` (I4 L1, I6 L9, I7 L5), in
on-screen labels in `film4.section()` ("The policy · three rules", "Same three rules") and in
`take/take-facts.template.yaml`. Before changing one, `grep -rn "<the words>" film/long take` and change them together;
the review's claim check flags a narration that disagrees with the facts.

Words that the voice spells differently from the screen: "Rosso C T L", "D G one", "O P A", "P I I", "operational
I D", "Red alone" (said "red", read "read"). `film4.srt()` writes them back the screen's way in the subtitles; add a
pair there when you add one.

## 9. When something fails

| symptom | cause | fix |
|---|---|---|
| `check`: podman machine memory too small | other kind clusters on the same machine | the printed `podman machine set --memory …` (stops every cluster on that machine for a minute), or use fewer clusters |
| `check`: podman disk < 40 GB | the podman machine's disk is full or small | if a kind cluster you own exists: `podman exec <cluster>-control-plane crictl rmi --prune` (its unused images); else grow the disk: `podman machine stop && podman machine set --disk-size 200 && podman machine start` (stops every cluster on it); or remove images you know you own, by exact name (`podman images`, `podman rmi <name>`). Never `podman system prune` / `podman rmi -f`: other clusters' nodes live in the same store |
| `check`: port taken | another cluster or program on 8080/9443 | other ports in `film.env` |
| `sources`: local changes / another repo | the directory is not a clean checkout of the pinned repo | the printed command, or another `SRC` |
| `cluster`: ports differ from film.env | the existing cluster was made with other ports | its ports in `film.env`, or `CONFIRM=<cluster> … recreate` |
| `cluster`: the installer failed | a download, an image pull, a webhook timing out | read `state/<cluster>/platform-install.log`; run `cluster` again (the installer resumes) |
| `sentry`: pods still on another image | a Deployment pinned to another tag | `kubectl -n data-governance set image deploy/<name> '*=data-governance/<receiver\|ui\|classification>:latest'`, then `sentry` |
| `sentry` or `doctor`: OPA's policy rules are not DG-001 DG-002 DG-004; pod `opa` in ContainerCreating | the `opa-policy` ConfigMap is missing or holds another catalog | `setup/sentry-film.sh sentry` compiles and loads it; it needs `uv` |
| `sentry`: migrate fails "Can't locate revision" | the database is newer than this Sentry | `plan` explains; `CONFIRM=data-governance … sentry` |
| `app`: `deploy.sh` preflight refuses | the CLI talks to another cluster, or the token expired | `setup/sentry-film.sh cli`, then `app`; `state/<cluster>/app-deploy.log` names the check |
| `doctor`: agents cannot reach ollama | ollama listens on 127.0.0.1 only, or is not running | `brew services restart ollama`; set `OLLAMA_HOST=0.0.0.0` for it if needed |
| `doctor`: lineage on fewer than 13 | a pod rolled since `instrument` | `take/take.sh relineage <deployment>`, or `instrument` again |
| `doctor`: last pharmacy conversation < 960 s | a refill would land in another session's window | wait the printed seconds |
| `sequence.sh`: `rec_live` "no new trace" | the collector is not forwarding to Sentry, or the app failed | `doctor`; `kubectl -n pharmacy logs deploy/pharmacy-assistant` |
| `film.sh check`: a verdict differs | see § 5 | a new take |
| `film.sh stills`: `getByText('team1')` times out (rossoctl's catalogs) | the platform UI asks for a login | `setup/sentry-film.sh cluster` (sets `ENABLE_AUTH=false`; `doctor` checks it) |
| `film.sh stills`: a locator times out | Sentry's page differs from the pinned console, or the trace list was pushed down by other traffic | look at the page in a browser at the printed trace; the pin decides the console version |
| `film.sh stage`: "this line is not in the voice cache … ELEVENLABS_API_KEY is not set" | a line's words changed (an edit, or a count that differs in this take) | export `ELEVENLABS_API_KEY`, or keep the words as they were; an HTTP 401 means the key is wrong |
| `join` wrote `film-2.mp4`, not `film.mp4` | films are never overwritten: each join takes the next free name | `review` picks the last one; name it yourself: `film.sh join <take> <name>` |

## 10. Undo, clean up

Every change the setup made is one line in `state/<cluster>/changes.log`, with its undo. The take changes the two
report tools' `DEMAND_KEY` (each switch rolls that tool's pod; `take/take.sh key mrn report-dispense export-dispense-batch`
puts the manifest's value back) and empties the app's `fills` table before each refill (`take.sh reset`; a demo table,
refilled by the next refill). To remove everything: `kind delete cluster --name <cluster>` (yours only), `rm -rf $SRC`, the images
`localhost/data-governance/*:film-*` in podman (by exact name: `podman rmi <name>`), and the CLI context
(`rossoctl config delete-context <CLI_CONTEXT>`).
