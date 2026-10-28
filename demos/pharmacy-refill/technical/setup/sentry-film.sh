#!/usr/bin/env bash
# sentry-film.sh — everything the film's take needs, on any Mac and any kind cluster: empty or full, fresh or used.
# Each step first reads what is there, says what it found and what it will do, then does only that. Nothing is deleted
# unless you name it in CONFIRM= (the step stops and prints the exact command). Settings: ../film.env.
#
#   setup/sentry-film.sh check        this machine: tools, versions, memory, disk, ports, keys, repo access   (read-only)
#   setup/sentry-film.sh plan         this cluster: what is there, and what `up` would do                     (read-only)
#   setup/sentry-film.sh media <zip>  a git checkout only: the binary parts (voice, music, the v4.2 take's media), from the
#                                     pinned zip (pins.yaml → media); files already here are never overwritten
#   setup/sentry-film.sh sources      the three checkouts at their pins (clone, or fetch + check out)
#   setup/sentry-film.sh cluster      the kind cluster + the agent platform: create, or complete what is missing  ~25 min
#   setup/sentry-film.sh cli          the platform CLI logged in on its own context for this cluster
#   setup/sentry-film.sh sentry       Sentry at its pin (build, load, install or roll) + the take's settings    ~30 min
#   setup/sentry-film.sh app          the pharmacy app at its pin (deploy; another version is replaced)         ~10 min
#   setup/sentry-film.sh instrument   lineage + payload capture on every agent and tool                         ~20 min
#   setup/sentry-film.sh doctor       is everything the take needs in place?        (read-only; exit 0 = ready)
#   setup/sentry-film.sh up           sources → cluster → cli → sentry → app → instrument → doctor
#   CONFIRM=<cluster> setup/sentry-film.sh recreate     delete this kind cluster and build it again (asks nothing else)
#
# Every change is appended to state/<cluster>/changes.log with how to undo it.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); PKG=$(cd "$HERE/.." && pwd)
ENVF=${FILM_ENV:-$PKG/film.env}
if [ ! -f "$ENVF" ]; then
  echo "no $ENVF yet: cp $PKG/film.env.example $PKG/film.env, then read it (the defaults fit a Mac with no other cluster)" >&2; exit 2
fi
set -a; . "$ENVF"; set +a
CLUSTER=${CLUSTER:-sentry-film}; HTTP_PORT=${HTTP_PORT:-8080}; HTTPS_PORT=${HTTPS_PORT:-9443}
KUBECONFIG=${KUBECONFIG:-$HOME/.kube/$CLUSTER.kubeconfig}; CLI_CONTEXT=${CLI_CONTEXT:-$CLUSTER}
SRC=${SRC:-$HOME/sentry-film/src}; LLM_MODEL=${LLM_MODEL:-qwen2.5:7b}
export KUBECONFIG KIND_EXPERIMENTAL_PROVIDER=podman CONTAINER_ENGINE=podman
export PATH="$HOME/.config/rossoctl:$PATH"
CTX=kind-$CLUSTER; NODE=$CLUSTER-control-plane; NS=pharmacy; DGNS=data-governance
STATE=$PKG/state/$CLUSTER; mkdir -p "$STATE"
PLATFORM_DIR=$SRC/kagenti; SENTRY_DIR=$SRC/lab-data-governance; APP_DIR=$SRC/agent-examples-snp

say()  { printf '>> %s\n' "$*"; }
ok()   { printf '   ok    %s\n' "$*"; }
note() { printf '   note  %s\n' "$*"; }
bad()  { printf '   FAIL  %s\n         → %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
die()  { printf 'STOP: %s\n' "$1" >&2; [ -n "${2:-}" ] && printf '      → %s\n' "$2" >&2; exit 1; }
k()    { kubectl --context "$CTX" "$@"; }
run_logged() {   # run_logged <log> <regex|-> <command…>: shows the matching progress lines (all with -), keeps every line in
  # <log>, and returns the command's own exit code (a filter that matches nothing must not hide a failure)
  local log=$1 re=$2 rc; shift 2
  set +e
  if [ "$re" = - ]; then "$@" 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}
  else "$@" 2>&1 | tee "$log" | grep -E --line-buffered "$re"; rc=${PIPESTATUS[0]}; fi
  set -e
  return "$rc"
}
changed() { printf '%s\t%s\t%s\n' "$(date '+%F %T')" "$1" "undo: $2" >> "$STATE/changes.log"; }
pin()  { python3 -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' "$PKG/pins.yaml" "$1" "${2:-sha}"; }
OS=$(uname -s)
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }   # GNU (Linux) / Perl (macOS)
port_taken() {   # port_taken <port>: something on this host listens on it. Linux: ss (lsof without root misses other users' sockets)
  if [ "$OS" = Darwin ]; then lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; fi
}
hint() { [ "$OS" = Darwin ] && echo "$1" || echo "$2"; }   # hint <macOS command> <Linux (dnf) command>
FAILS=0

# ---------------------------------------------------------------------------------------------------------- check
need() {   # need <command> <what it is for> <how to install>
  if command -v "$1" >/dev/null 2>&1; then ok "$1 ($2)"; else bad "$1 missing ($2)" "$3"; fi
}
step_check() {
  say "this machine ($(uname -sm)), settings from $ENVF"
  [ "$OS" = Darwin ] || note "only macOS (Apple Silicon) is proven; Linux (RHEL 9, rootless podman) was run once (RUNBOOK § 0, “On Linux”); Docker Desktop does not work (the app's deploy.sh is podman-only)"
  [ "$OS" = Darwin ] && need brew "installs the rest" "https://brew.sh"
  need podman "the container engine and the kind node's host" "$(hint "brew install podman && podman machine init --cpus 8 --memory 16384 --disk-size 200 && podman machine start" "sudo dnf install -y podman")"
  need kind "the Kubernetes cluster" "$(hint "brew install kind" "curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64 && sudo install kind /usr/local/bin/")"
  need kubectl "talks to the cluster" "$(hint "brew install kubectl" "curl -fsSLO https://dl.k8s.io/release/\$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl && sudo install kubectl /usr/local/bin/")"
  need helm "the platform installer uses it" "$(hint "brew install helm" "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash")"
  need git "the three checkouts" "$(hint "brew install git" "sudo dnf install -y git")"
  need git-lfs "Sentry's classifier weights (~500 MB) and the pitch film" "$(hint "brew install git-lfs" "sudo dnf install -y git-lfs") && git lfs install"
  need uv "Sentry's Python env: it compiles OPA's policy, and the film's OPA panels" "$(hint "brew install uv" "curl -LsSf https://astral.sh/uv/install.sh | sh")"
  need curl "health checks" "$(hint "brew install curl" "sudo dnf install -y curl")"
  [ "$OS" = Darwin ] || need ss "port checks (iproute)" "sudo dnf install -y iproute"
  need ffmpeg "the film: cuts, music, subtitles, loudness" "$(hint "brew install ffmpeg" "not in RHEL's repos; a static build (has libx264 + ebur128): curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar xJ -C /tmp && sudo install -m755 /tmp/ffmpeg-*-amd64-static/ff{mpeg,probe} /usr/local/bin/  (EPEL + RPM Fusion also work once their GPG keys are imported)")"
  need ollama "the agents' language model, on this machine" "$(hint "brew install ollama && brew services start ollama" "sudo dnf install -y zstd && curl -fsSL https://ollama.com/install.sh | sh  (the installer needs zstd; it makes a systemd unit 'ollama'; then the OLLAMA_HOST line below)")"
  need node "the browser recordings (Playwright)" "$(hint "brew install node" "sudo dnf module reset -y nodejs && sudo dnf module install -y nodejs:20")"
  if command -v node >/dev/null 2>&1 && [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
    bad "node $(node --version) is too old: Playwright 1.61 needs ≥ 18 (npm only warns EBADENGINE)" "$(hint "brew upgrade node" "sudo dnf module reset -y nodejs && sudo dnf module install -y nodejs:20  (RHEL 9's default stream is 16)")"; fi
  local asset; asset=$(pin rossoctl_cli asset 2>/dev/null | sed "s/Darwin-arm64/$(uname -s)-$(uname -m | sed 's/aarch64/arm64/')/") || true   # no yaml yet: the python3 line below says so
  need rossoctl "the agent platform's CLI (the app's deploy imports through it)" \
    "curl -fsSLO $(pin rossoctl_cli url)/download/$(pin rossoctl_cli release)/$asset && mkdir -p ~/.config/rossoctl && tar xzf $asset -C ~/.config/rossoctl  (a public release: no GitHub login needed)"
  local py=${PYTHON:-python3.11}
  if command -v "$py" >/dev/null 2>&1; then
    local miss; miss=$("$py" - <<'PY'
import importlib.util as u
print(" ".join(m for m in ("yaml", "numpy", "PIL", "faster_whisper") if not u.find_spec(m)))
PY
)
    [ -z "$miss" ] && ok "$py with yaml, numpy, Pillow, faster-whisper (the film build)" \
      || bad "$py lacks: $miss" "$py -m pip install $([ "$OS" = Darwin ] || echo "--user ")pyyaml numpy Pillow faster-whisper"
  else bad "$py missing (the film build)" "$(hint "brew install python@3.11" "sudo dnf install -y python3.11 python3.11-pip"), or set PYTHON= in film.env"; fi
  python3 -c 'import yaml' 2>/dev/null && ok "python3 with yaml (these scripts)" || bad "python3 lacks yaml (these scripts read pins.yaml)" "$(hint "python3 -m pip install pyyaml" "sudo dnf install -y python3-pyyaml")"
  if [ -d "$PKG/node_modules/playwright" ] && (cd "$PKG" && node -e 'require("playwright").chromium.launch().then(b => b.close()).catch(e => { console.error(e.message.split("\n")[0]); process.exit(1) })') >/dev/null 2>&1; then
    ok "Playwright in the package, headless Chromium launches"
  else bad "Playwright or its Chromium not ready (node_modules/playwright, a headless launch)" "(cd $PKG && npm install && npx playwright install $(hint "chromium" "chromium && sudo dnf install -y nss atk at-spi2-atk cups-libs libxkbcommon alsa-lib mesa-libgbm pango cairo libXcomposite libXdamage libXrandr libXfixes  (--with-deps is apt-only)")))"; fi
  if command -v ffmpeg >/dev/null 2>&1; then
    local fl en; fl=$(ffmpeg -hide_banner -filters 2>/dev/null || true); en=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)
    case "$fl" in *ebur128*) case "$en" in *libx264*) ok "ffmpeg has libx264 and ebur128" ;; *) bad "ffmpeg lacks libx264" "$(hint "brew reinstall ffmpeg" "RHEL's ffmpeg-free has no libx264: the RPM Fusion ffmpeg, or a static build")" ;; esac ;;
      *) bad "ffmpeg lacks the ebur128 filter" "$(hint "brew reinstall ffmpeg" "the RPM Fusion ffmpeg, or a static build")" ;; esac
  fi
  if command -v podman >/dev/null 2>&1; then
    if podman info >/dev/null 2>&1; then
      local mem cpus disk free
      mem=$(podman info --format '{{.Host.MemTotal}}' 2>/dev/null || echo 0); mem=$((mem / 1048576))
      cpus=$(podman info --format '{{.Host.CPUs}}' 2>/dev/null || echo 0)
      # this cluster with the app and Sentry uses ~7 GiB, plus ~2 GiB while Sentry's images build; other running
      # clusters keep what they use now (podman stats)
      local others used=0 n u; others=$(kind get clusters 2>/dev/null | grep -vx "$CLUSTER" | tr '\n' ' ' || true)
      for n in $others; do u=$(podman stats --no-stream --format '{{.MemUsage}}' "$n-control-plane" 2>/dev/null | awk '{v=$1; m=1; if (v ~ /GB$/) m=1000; sub(/[A-Za-z]+$/,"",v); printf "%d", v*m}')
        used=$((used + ${u:-0})); done
      local want=$((used + 9500))
      [ "$mem" -ge "$want" ] && ok "podman machine: ${mem} MiB, $cpus CPUs${others:+; other clusters use ~${used} MB ($others)}" \
        || bad "podman machine has ${mem} MiB; this cluster needs ~9.5 GiB${others:+ beside the ~${used} MB the other clusters use ($others)}" \
               "podman machine stop && podman machine set --memory $(( (want + 1023) / 1024 * 1024 )) && podman machine start  (stops every cluster on it for a minute)"
      if [ "$OS" = Darwin ]; then free=$(podman machine ssh df -BG --output=avail /var 2>/dev/null | tail -1 | tr -dc 0-9 || true)
      else free=$(df -BG --output=avail "$(podman info --format '{{.Store.GraphRoot}}')" 2>/dev/null | tail -1 | tr -dc 0-9 || true); fi   # Linux: no machine, podman's own store
      if [ -n "$free" ]; then
        [ "$free" -ge 40 ] && ok "podman disk: ${free} GB free (the Sentry build needs ~35)" \
          || bad "podman disk: only ${free} GB free; building Sentry's classifier needs ~35 GB" \
                 "$(cluster_exists && echo "unused images in this cluster's node: podman exec $NODE crictl rmi --prune; or " )$(hint "grow the machine's disk: podman machine stop && podman machine set --disk-size 200 && podman machine start (stops every cluster on it)" "free space on the filesystem of $(podman info --format '{{.Store.GraphRoot}}')"). Never 'podman system prune' / 'rmi -f': other clusters' nodes live in that store"
      fi
    else bad "podman is installed but does not answer (podman info)" "$(hint "podman machine start" "podman info  (shows the error; rootless podman needs subuid/subgid for $USER)")"; fi
  fi
  if command -v ollama >/dev/null 2>&1; then
    if curl -fsS -m 5 http://localhost:11434/api/tags 2>/dev/null | grep -q "\"$LLM_MODEL\""; then ok "ollama serves $LLM_MODEL"
    elif curl -fsS -m 5 http://localhost:11434/api/tags >/dev/null 2>&1; then bad "ollama runs but has no $LLM_MODEL" "ollama pull $LLM_MODEL"
    else bad "ollama is not serving on :11434" "$(hint "brew services start ollama  (or: ollama serve)" "sudo systemctl enable --now ollama")"; fi
    # Linux: the kind node reaches the host at host.containers.internal = the host's own address, not 127.0.0.1
    if [ "$OS" != Darwin ] && port_taken 11434 && ! ss -Hltn 'sport = :11434' 2>/dev/null | grep -qE '(\*|0\.0\.0\.0|\[::\]):11434'; then
      bad "ollama listens on 127.0.0.1 only; the agents in the kind node cannot reach it" \
          "sudo mkdir -p /etc/systemd/system/ollama.service.d && printf '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0\n' | sudo tee /etc/systemd/system/ollama.service.d/host.conf && sudo systemctl daemon-reload && sudo systemctl restart ollama"
    fi
  fi
  if [ "$OS" != Darwin ]; then   # the kind node shares the host kernel's inotify limits (kind "known issues"); RHEL's 128 runs out
    local ii iw; ii=$(sysctl -n fs.inotify.max_user_instances 2>/dev/null || echo 0); iw=$(sysctl -n fs.inotify.max_user_watches 2>/dev/null || echo 0)
    [ "$ii" -ge 512 ] && [ "$iw" -ge 524288 ] && ok "inotify limits $ii instances / $iw watches" \
      || bad "inotify limits $ii / $iw are too low for the kind node (the platform's controller crash-loops: 'too many open files')" \
             "printf 'fs.inotify.max_user_instances = 512\nfs.inotify.max_user_watches = 524288\n' | sudo tee /etc/sysctl.d/99-kind-inotify.conf && sudo sysctl --system"
  fi
  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    local p; for p in "$HTTP_PORT" "$HTTPS_PORT"; do
      if port_taken "$p"; then
        bad "port $p is taken (the new cluster's gateway needs it)" "pick free ports: HTTP_PORT= / HTTPS_PORT= in film.env ($(hint "lsof -nP -iTCP:$p -sTCP:LISTEN" "ss -ltnp 'sport = :$p'") shows who has it)"
      else ok "port $p free for the new cluster"; fi
    done
  fi
  local r d; for r in platform lab-data-governance agent-examples-snp; do
    case $r in platform) d=$PLATFORM_DIR ;; lab-data-governance) d=$SENTRY_DIR ;; *) d=$APP_DIR ;; esac
    if git ls-remote "$(pin $r repo)" HEAD >/dev/null 2>&1; then ok "read access: $(pin $r repo)"
    elif [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$(pin $r)" ] && [ -z "$(git -C "$d" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
      ok "no read access to $(pin $r repo), but $d is a clean checkout at its pin (sources keeps it; no fetch needed)"
    else bad "no read access to $(pin $r repo)" "ask its owners for read access, then: $(hint "brew install gh" "sudo dnf install -y gh") && gh auth login && gh auth setup-git; or put a clean checkout at the pin in $d"; fi
  done
  [ -n "${ELEVENLABS_API_KEY:-}" ] && ok "ELEVENLABS_API_KEY set (new or changed lines, music)" \
    || note "ELEVENLABS_API_KEY not set: the film rebuilds from the voice cache; a changed line or new music needs it"
  [ -n "${GEMINI_API_KEY:-}" ] && ok "GEMINI_API_KEY set (the film review's model pass)" \
    || note "GEMINI_API_KEY not set: review.py runs every check but the Gemini pass"
  if [ -n "$(ls "$PKG/film/long/tts11" 2>/dev/null)" ]; then ok "the media parts are here (voice cache, music, the v4.2 take)"
  else bad "the media parts are missing (a git checkout carries the text only)" "download $(pin media file) from $(pin media url), then: setup/sentry-film.sh media <that zip>"; fi
  [ "$FAILS" = 0 ] && say "check passed" || die "$FAILS item(s) above" "fix them, then run check again"
}

# ---------------------------------------------------------------------------------------------------------- media
step_media() {
  local z=${1:-} want; want=$(pin media sha256)
  [ -n "$z" ] || die "media needs the zip" "download $(pin media file) from $(pin media url) (sign in to Box), then: setup/sentry-film.sh media <path to it>"
  [ -s "$z" ] || die "no file $z"
  say "checking $z against pins.yaml (sha256)"
  [ "$(sha256 "$z" | cut -d' ' -f1)" = "$want" ] || die "$z is not the pinned media zip (its sha256 differs)" "download it again from $(pin media url)"
  local t; t=$(mktemp -d); trap "rm -rf '$t'" EXIT INT TERM       # the path itself: the function's local is gone at exit
  unzip -q "$z" -d "$t" || die "the zip does not unpack"
  rsync -a --ignore-existing --exclude '.claude/' "$t/sentry-technical-film/" "$PKG/"
  rm -rf "$t"; trap - EXIT INT TERM
  ok "media unpacked into $PKG (files already here were kept as they are)"
}

# -------------------------------------------------------------------------------------------------------- sources
repo_id() { echo "$1" | sed -E 's#^(ssh://)?git@github\.com[:/]#https://github.com/#; s#\.git$##; s#/$##' | tr '[:upper:]' '[:lower:]'; }   # https / ssh / .git: one name
checkout() {   # checkout <pins key> <dir>
  local repo sha; repo=$(pin "$1" repo); sha=$(pin "$1")
  if [ ! -d "$2/.git" ]; then
    say "clone $repo → $2"; mkdir -p "$(dirname "$2")"; git clone -q "$repo" "$2"; changed "cloned $2" "rm -rf $2"
  fi
  [ "$(repo_id "$(git -C "$2" remote get-url origin)")" = "$(repo_id "$repo")" ] \
    || die "$2 is a checkout of $(git -C "$2" remote get-url origin), not $repo" "move it away, or set SRC= in film.env to another directory"
  if [ -n "$(git -C "$2" status --porcelain --untracked-files=no)" ]; then
    die "$2 has local changes; this script never overwrites them" "commit or stash them (git -C $2 stash), or set SRC= in film.env to a fresh directory"
  fi
  git -C "$2" cat-file -e "$sha^{commit}" 2>/dev/null || git -C "$2" fetch -q origin
  git -C "$2" cat-file -e "$sha^{commit}" 2>/dev/null || die "commit ${sha:0:9} is not on $repo" "pins.yaml names a commit the remote does not have; check pins.yaml"
  if [ "$(git -C "$2" rev-parse HEAD)" != "$sha" ]; then git -C "$2" checkout -q --detach "$sha"; changed "$2 checked out at ${sha:0:9}" "git -C $2 checkout -"; fi
  ok "$1 at ${sha:0:9} in $2"
}
step_sources() {
  say "the three checkouts, under $SRC"
  checkout platform "$PLATFORM_DIR"; checkout lab-data-governance "$SENTRY_DIR"; checkout agent-examples-snp "$APP_DIR"
  git -C "$SENTRY_DIR" lfs install --local >/dev/null
  git -C "$SENTRY_DIR" lfs pull --include="classification/model/**"
  local w; w=$(git -C "$SENTRY_DIR" lfs ls-files --size --include="classification/model/**" | head -1)
  find "$SENTRY_DIR/classification/model" -name model.safetensors -size +100M | grep -q . \
    && ok "classifier weights present ($w)" || die "the classifier weights are an LFS pointer" "git -C $SENTRY_DIR lfs pull (needs git-lfs and network)"
  local pf=$PKG/film/pitch-2m35/master/sentry_pitch_demo.mp4 want; want=$(pin pitch sha256)
  if [ "$(sha256 "$pf" 2>/dev/null | cut -d' ' -f1)" != "$want" ]; then
    say "the pitch film (2:35, 55 MB, Git LFS on the lab-data-governance branch $(pin pitch branch))"
    mkdir -p "$(dirname "$pf")"; curl -fsSL -o "$pf.part" "$(pin pitch url)" || die "download failed: $(pin pitch url)" "check the network; or git lfs pull it from that branch"
    [ "$(sha256 "$pf.part" | cut -d' ' -f1)" = "$want" ] || die "the downloaded pitch is not the pinned film (sha256 differs)" "pins.yaml → pitch; the branch may have moved its LFS object"
    mv "$pf.part" "$pf"
  fi
  ok "the pitch film: $pf"
  if ! cmp -s "$HERE/files/uv.lock" "$SENTRY_DIR/uv.lock" 2>/dev/null; then
    cp "$HERE/files/uv.lock" "$SENTRY_DIR/uv.lock"; ok "uv.lock: the lockfile the film's images were built with (the repo ignores it)"
  fi
}
at_pin() { [ "$(git -C "$2" rev-parse HEAD 2>/dev/null)" = "$(pin "$1")" ] || die "$2 is not at the pin of $1" "setup/sentry-film.sh sources"; }

# -------------------------------------------------------------------------------------------------------- cluster
cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; }
kubeconfig_has() {   # an existing cluster made elsewhere: write its context into KUBECONFIG (once; nothing else is touched)
  cluster_exists || return 0
  kubectl --kubeconfig "$KUBECONFIG" config get-contexts "$CTX" >/dev/null 2>&1 && return 0
  mkdir -p "$(dirname "$KUBECONFIG")"; kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null 2>&1 \
    && changed "context $CTX written into $KUBECONFIG" "kubectl --kubeconfig $KUBECONFIG config delete-context $CTX"
}
cluster_ports() { podman port "$NODE" 2>/dev/null | sed -En 's#^(30080|30443)/tcp -> [0-9.]*:([0-9]+)$#\1 \2#p'; }   # -E: BSD and GNU sed
platform_gaps() {   # prints one line per missing platform piece (empty = complete)
  k get ns rossoctl-system >/dev/null 2>&1 || { echo "the platform (namespace rossoctl-system)"; return; }
  local d; for d in rossoctl-system/rossoctl-controller-manager rossoctl-system/rossoctl-backend rossoctl-system/rossoctl-ui \
                    rossoctl-system/otel-collector rossoctl-system/http-istio cr-system/registry istio-system/istiod; do
    [ "$(k -n "${d%%/*}" get deploy "${d#*/}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" -ge 1 ] 2>/dev/null \
      || echo "deployment $d not ready$(k -n "${d%%/*}" logs deploy/"${d#*/}" --previous --tail=200 2>/dev/null | grep -m1 -oE '"error":"[^"]{1,120}' | sed 's/^"error":"/ — last error: /')"
  done
  [ "$(k -n keycloak get sts keycloak -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = 1 ] || echo "keycloak not ready"
  k -n rossoctl-system get secret rossoctl-api-oauth-secret >/dev/null 2>&1 || echo "the CLI's service account (secret rossoctl-api-oauth-secret)"
  k get csidriver csi.spiffe.io >/dev/null 2>&1 || echo "SPIRE (csidriver csi.spiffe.io)"
  podman exec "$NODE" grep -q registry.cr-system.svc.cluster.local /etc/hosts 2>/dev/null || echo "the node's route to the in-cluster registry"
}
step_cluster() {
  if cluster_exists; then
    say "kind cluster $CLUSTER exists"
    local ports; ports=$(cluster_ports | tr '\n' ' ')
    case " $ports " in *" 30080 $HTTP_PORT "*) ok "gateway on host port $HTTP_PORT" ;;
      *) die "cluster $CLUSTER publishes its gateway as [$ports], not $HTTP_PORT as film.env says" \
             "set HTTP_PORT/HTTPS_PORT in film.env to the cluster's ports, or rebuild it: CONFIRM=$CLUSTER setup/sentry-film.sh recreate" ;; esac
    kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null 2>&1 || true
    k get nodes >/dev/null 2>&1 || die "cluster $CLUSTER does not answer" \
      "podman start $NODE (a stopped node), or rebuild it: CONFIRM=$CLUSTER setup/sentry-film.sh recreate"
    local gaps; gaps=$(platform_gaps)
    if [ -z "$gaps" ]; then ok "the agent platform is complete"; platform_settings; return; fi
    say "the platform lacks:"; echo "$gaps" | sed 's/^/     - /'
    say "re-running the platform installer on the existing cluster (it adds what is missing, keeps the rest)"
  else
    say "no kind cluster $CLUSTER: creating it with the agent platform, gateway on host ports $HTTP_PORT/$HTTPS_PORT (~25 min)"
    local p; for p in "$HTTP_PORT" "$HTTPS_PORT"; do
      port_taken "$p" && die "host port $p is taken" "choose free ports in film.env (HTTP_PORT, HTTPS_PORT)"
    done
    changed "created kind cluster $CLUSTER" "kind delete cluster --name $CLUSTER"
  fi
  at_pin platform "$PLATFORM_DIR"
  sed -e "s/@HTTP_PORT@/$HTTP_PORT/; s/@HTTPS_PORT@/$HTTPS_PORT/" "$HERE/kind-config.yaml.in" > "$STATE/kind-config.yaml"
  mkdir -p "$(dirname "$KUBECONFIG")"
  _platform() { cd "$PLATFORM_DIR" && KIND_CONFIG="$STATE/kind-config.yaml" bash scripts/kind/setup-rossoctl.sh --cluster-name "$CLUSTER" \
     $(pin platform flags) --rossoctl-values "$HERE/platform-values.yaml"; }
  run_logged "$STATE/platform-install.log" - _platform \
    || die "the platform installer failed" "read $STATE/platform-install.log (its last lines name the step); run this step again, it resumes"
  local gaps; gaps=$(platform_gaps)
  [ -z "$gaps" ] || die "after the installer, still missing: $(echo "$gaps" | tr '\n' ';')" "read $STATE/platform-install.log"
  ok "the agent platform is complete"; platform_settings
}
host_dns() {   # Linux: pods resolve host.containers.internal (the agents' ollama) through CoreDNS. On macOS the podman
               # machine's DNS answers it; rootless podman on Linux writes it only into the node's /etc/hosts, which
               # CoreDNS does not read, so the agents' LLM calls fail (the sidecar answers 502).
  [ "$OS" = Darwin ] && return 0
  local ip cf; ip=$(podman exec "$NODE" sh -c "awk '/host.containers.internal/ {print \$1; exit}' /etc/hosts" 2>/dev/null)
  [ -n "$ip" ] || { note "the node has no host.containers.internal in /etc/hosts; the agents cannot reach ollama"; return 0; }
  cf=$(k -n kube-system get cm coredns -o jsonpath='{.data.Corefile}')
  if grep -q "$ip host.containers.internal" <<<"$cf"; then ok "CoreDNS resolves host.containers.internal → $ip (ollama on this host)"; return; fi
  k -n kube-system get cm coredns -o yaml > "$STATE/coredns-before.yaml"
  python3 -c 'import json,sys
cf=sys.stdin.read(); ip=sys.argv[1]
blk="    hosts {\n       %s host.containers.internal host.docker.internal\n       fallthrough\n    }\n" % ip
print(json.dumps({"data":{"Corefile":cf.replace("    forward .", blk + "    forward .", 1)}}))' "$ip" <<<"$cf" > "$STATE/coredns-patch.json"
  k -n kube-system patch cm coredns --type merge --patch-file "$STATE/coredns-patch.json" >/dev/null
  k -n kube-system rollout restart deploy/coredns >/dev/null; k -n kube-system rollout status deploy/coredns --timeout=120s >/dev/null
  changed "CoreDNS: host.containers.internal → $ip (pods reach ollama on this host)" "kubectl -n kube-system apply -f $STATE/coredns-before.yaml"
  ok "CoreDNS resolves host.containers.internal → $ip (the node's own mapping; pods reach ollama on this host)"
}
platform_settings() {   # pins.yaml → platform.ui_auth: the platform UI's login (its catalogs are shot for the film)
  host_dns
  local want have; want=$(pin platform ui_auth)
  have=$(k -n rossoctl-system get deploy rossoctl-backend -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="ENABLE_AUTH")].value}')
  if [ "$have" = "$want" ]; then ok "platform UI login ENABLE_AUTH=$want"; return; fi
  k -n rossoctl-system set env deploy/rossoctl-backend ENABLE_AUTH="$want" >/dev/null
  k -n rossoctl-system rollout status deploy/rossoctl-backend --timeout=300s >/dev/null
  changed "rossoctl-backend ENABLE_AUTH ${have:-(unset)} → $want (the UI's catalogs without a login)" "kubectl -n rossoctl-system set env deploy/rossoctl-backend ENABLE_AUTH=${have:-true}"
  ok "platform UI login ENABLE_AUTH: ${have:-(unset)} → $want (the film shoots its catalogs without a login)"
}
step_recreate() {
  [ "${CONFIRM:-}" = "$CLUSTER" ] || die "recreate deletes the kind cluster $CLUSTER and everything in it" "CONFIRM=$CLUSTER setup/sentry-film.sh recreate"
  cluster_exists && { say "deleting kind cluster $CLUSTER"; kind delete cluster --name "$CLUSTER"; changed "deleted kind cluster $CLUSTER" "none (setup/sentry-film.sh up rebuilds it)"; }
  step_cluster
}

# ------------------------------------------------------------------------------------------------------------ cli
step_cli() {
  local sec; sec() { k -n rossoctl-system get secret rossoctl-api-oauth-secret -o "jsonpath={.data.$1}" | base64 -d; }
  local token; token=$(curl -fsS -m 30 -X POST "http://keycloak.localtest.me:$HTTP_PORT/realms/$(sec KEYCLOAK_REALM)/protocol/openid-connect/token" \
     -d grant_type=client_credentials -d "client_id=$(sec CLIENT_ID)" --data-urlencode "client_secret=$(sec CLIENT_SECRET)" \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])') \
     || die "no token from Keycloak at keycloak.localtest.me:$HTTP_PORT" "is the cluster's gateway on $HTTP_PORT? (curl -I http://keycloak.localtest.me:$HTTP_PORT)"
  local api="http://rossoctl-api.localtest.me:$HTTP_PORT/api/v1/"
  local have; have=$(rossoctl config get-contexts --json 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin); print(next((c.get("server","") for c in (d.get("contexts") or []) if c.get("name")==sys.argv[1]), ""))' "$CLI_CONTEXT" || true)
  if [ -z "$have" ]; then rossoctl config create-context --name "$CLI_CONTEXT" --server "$api" >/dev/null; changed "CLI context $CLI_CONTEXT created" "rossoctl config delete-context $CLI_CONTEXT"
  elif [ "$have" != "$api" ]; then die "the CLI context '$CLI_CONTEXT' points at $have, not this cluster ($api)" "set CLI_CONTEXT= in film.env to an unused name"; fi
  rossoctl login --context "$CLI_CONTEXT" --token "$token" >/dev/null
  ok "CLI logged in on context $CLI_CONTEXT → $api (the current context is not switched — a CLI with no context before makes this one current; the token is short-lived, app runs this again)"
}

# --------------------------------------------------------------------------------------------------------- sentry
rev_of_image() { podman exec "$NODE" crictl inspecti -o json "$1" 2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["info"]["imageSpec"]["config"]["Labels"].get("org.opencontainers.image.revision",""))
except Exception: print("")'; }
sentry_revs() {   # one line per DG pod container: <deploy> <revision label of its image>
  local p; for p in $(k -n $DGNS get pods -o jsonpath='{range .items[*]}{.metadata.name}={.status.containerStatuses[0].imageID}{"\n"}{end}' 2>/dev/null | grep -Ev '^(opa|data-governance-postgres)'); do
    echo "${p%%=*} $(rev_of_image "${p#*=}")"; done
}
migration_state() {   # prints "<db head> <code head>"; empty db head = no database yet
  local db code
  db=$(k -n $DGNS exec data-governance-postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d data_governance -At -c "select version_num from alembic_version"' 2>/dev/null || true)
  code=$(sed -n 's/^revision: str = "\(.*\)"/\1/p' "$SENTRY_DIR"/data_governance/db/migrations/versions/*.py 2>/dev/null | sort | tail -1)
  echo "${db:--} ${code:--}"   # revisions are "00NN_<name>": they sort by number
}
build_sentry() {
  local sha tag; sha=$(pin lab-data-governance); tag=film-${sha:0:7}
  say "building Sentry's two images from ${sha:0:9} (~25 min the first time; the classifier image is large)"
  local ver; ver="${sha:0:7} ($(git -C "$SENTRY_DIR" show -s --format=%cd --date=short HEAD))"
  # UV_HTTP_TIMEOUT: the classifier's torch pulls the CUDA wheels (nvidia-cudnn-cu13 ~700 MB); uv's 30 s default times
  # out on one of them on a cold cache. The full build output goes to state/<cluster>/sentry-build-*.log.
  say "  receiver/ui image (log: $STATE/sentry-build-receiver.log)"
  podman build -f "$SENTRY_DIR/Containerfile" --build-arg "APP_VERSION=$ver" --label "org.opencontainers.image.revision=$sha" \
    -t "localhost/data-governance/receiver:$tag" "$SENTRY_DIR" > "$STATE/sentry-build-receiver.log" 2>&1 \
    || { tail -15 "$STATE/sentry-build-receiver.log"; die "the receiver image did not build" "read $STATE/sentry-build-receiver.log; run sentry again"; }
  say "  classifier image, torch + ~4 GB of wheels (log: $STATE/sentry-build-classification.log; progress: tail -f it)"
  podman build -f "$SENTRY_DIR/Containerfile.classification" --env UV_HTTP_TIMEOUT=600 --label "org.opencontainers.image.revision=$sha" \
    -t "localhost/data-governance/classification:$tag" "$SENTRY_DIR" > "$STATE/sentry-build-classification.log" 2>&1 \
    || { tail -15 "$STATE/sentry-build-classification.log"; die "the classifier image did not build" "read $STATE/sentry-build-classification.log (a network timeout: run sentry again)"; }
  local n src dst
  for n in receiver ui classification; do   # the manifests name data-governance/<n>:latest; containerd reads that as docker.io/…
    src=localhost/data-governance/$([ $n = classification ] && echo classification || echo receiver):$tag; dst=docker.io/data-governance/$n:latest
    podman tag "$src" "$dst"
    say "loading $dst into the node (an image archive: kind load is unreliable under podman)"
    podman save "$dst" | podman exec -i "$NODE" ctr -n k8s.io images import - >/dev/null
  done
  changed "Sentry images ${sha:0:7} loaded into $NODE as docker.io/data-governance/{receiver,ui,classification}:latest" "load the earlier images again (their tags were overwritten)"
  # the 6.6 GB import saturates the node's disk: etcd times out and the API server restarts (exit 137) for a minute or
  # two afterwards; kubectl apply then fails "failed to download openapi". Wait until it answers steadily.
  say "waiting for the API server to settle after the image import"
  local i n=0; for i in $(seq 1 60); do
    if k get --raw /readyz >/dev/null 2>&1 && k get --raw /openapi/v2 >/dev/null 2>&1; then n=$((n + 1)); [ $n -ge 6 ] && break; else n=0; fi; sleep 5
  done
  [ $n -ge 6 ] || die "the API server did not settle within 5 min after the image import" "kubectl --context $CTX -n kube-system get pods; run sentry again"
}
step_sentry() {
  at_pin lab-data-governance "$SENTRY_DIR"
  [ -f "$SENTRY_DIR/uv.lock" ] || die "no uv.lock in $SENTRY_DIR" "setup/sentry-film.sh sources (it puts the package's lockfile there)"
  local sha; sha=$(pin lab-data-governance)
  if k get ns $DGNS >/dev/null 2>&1; then
    local m; m=$(migration_state); say "Sentry is installed; database at migration ${m%% *}, the pinned code's newest is ${m##* }"
    if [ "${m%% *}" != - ] && [ "${m%% *}" \> "${m##* }" ]; then
      [ "${CONFIRM:-}" = $DGNS ] || die "Sentry's database is at migration ${m%% *}, newer than this Sentry (${m##* }); this Sentry cannot run on it" \
        "keep that Sentry and use another cluster (CLUSTER= in film.env), or drop Sentry's data and start it fresh: CONFIRM=$DGNS setup/sentry-film.sh sentry"
      say "CONFIRM=$DGNS: dumping, then deleting Sentry with its data"
      k -n $DGNS exec data-governance-postgres-0 -- sh -c 'pg_dump -U "$POSTGRES_USER" -d data_governance' | gzip > "$STATE/dg-before-delete.sql.gz"
      (cd "$SENTRY_DIR" && kubectl config use-context "$CTX" >/dev/null && bash deploy/dg.sh component uninstall)
      changed "Sentry uninstalled with its data (dump: $STATE/dg-before-delete.sql.gz)" "reinstall that Sentry, then psql < the dump"
    fi
  fi
  local revs; revs=$(sentry_revs 2>/dev/null || true)
  if [ -n "$revs" ] && ! echo "$revs" | awk '{print $2}' | grep -vqx "$sha"; then ok "every Sentry pod runs ${sha:0:9}"
  else
    [ -n "$revs" ] && { say "Sentry pods not at ${sha:0:9}:"; echo "$revs" | awk -v s="$sha" '$2!=s {print "     - "$1" "($2==""?"(no revision label: an image built elsewhere)":substr($2,1,9))}'; }
    k -n $DGNS get deploy -o jsonpath='{range .items[*]}{.metadata.name}={.spec.template.spec.containers[0].image}{"\n"}{end}' > "$STATE/dg-images-before.txt" 2>/dev/null || true
    build_sentry
    say "installing / rolling Sentry (dg.sh component install --no-build: manifests, collector tee, roll)"
    _dg_install() { cd "$SENTRY_DIR" && kubectl config use-context "$CTX" >/dev/null && KIND_CLUSTER="$CLUSTER" bash deploy/dg.sh component install --no-build; }
    run_logged "$STATE/sentry-install.log" '^>>|^error|^Error' _dg_install || die "dg.sh component install failed" "read $STATE/sentry-install.log (its last lines: $(tail -2 "$STATE/sentry-install.log" | tr '\n' ' '))"
    revs=$(sentry_revs); [ -n "$revs" ] || die "no Sentry pods after the install" "read $STATE/sentry-install.log"
    echo "$revs" | awk '{print $2}' | grep -vqx "$sha" && die "after the roll some Sentry pods still run another image: $(echo "$revs" | tr '\n' ' ')" \
      "kubectl --context $CTX -n $DGNS get pods; a pod pinned to another tag needs: kubectl -n $DGNS set image deploy/<name> '*=data-governance/<receiver|ui|classification>:latest'"
    ok "every Sentry pod runs ${sha:0:9}"
  fi
  sentry_settings
}
sentry_settings() {   # the take's Sentry settings (pins.yaml → settings), each saved before it is changed
  local line d kv name want have
  python3 -c 'import yaml,sys
for d,kv in yaml.safe_load(open(sys.argv[1]))["settings"]["env"].items():
    for k,v in kv.items(): print(d, k, v)' "$PKG/pins.yaml" | while read -r d name want; do
    have=$(k -n $DGNS get deploy "$d" -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$name\")].value}")
    if [ "$have" = "$want" ]; then ok "$d $name=$want"
    else k -n $DGNS set env deploy/"$d" "$name=$want" >/dev/null; ok "$d $name: ${have:-(unset)} → $want"
      changed "$d $name ${have:-(unset)} → $want" "kubectl -n $DGNS set env deploy/$d $([ -n "$have" ] && echo "$name=$have" || echo "$name-")"; fi
  done
  have=$(k -n $DGNS get deploy data-governance-ui -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')
  want=$(pin settings ui_memory)
  [ "$have" = "$want" ] && ok "Sentry console memory $want" || { k -n $DGNS set resources deploy/data-governance-ui --limits=memory="$want" >/dev/null
    ok "Sentry console memory ${have:-(none)} → $want (the Data lineage reads need it)"; changed "data-governance-ui memory ${have} → $want" "kubectl -n $DGNS set resources deploy/data-governance-ui --limits=memory=$have"; }
  if k -n $DGNS rollout status deploy --timeout=600s >/dev/null 2>&1; then ok "Sentry's deployments ready"
  else note "not ready yet: $(k -n $DGNS get deploy --no-headers | awk '{split($2,a,"/"); if (a[1]!=a[2]) print $1}' | tr '\n' ' ')(OPA waits for its policy, next)"; fi
  opa_policy
}
opa_policy() {   # OPA's policy: compiled from Sentry's rule catalog by its own script (dg.sh component install does not make it)
  local want rules; want=$(pin settings opa_rules); rules=$(opa_rules)
  if [ "$rules" = "$want" ]; then ok "OPA serves the policy: $rules"; return; fi
  say "OPA's policy (${rules:-none served}): compiling Sentry's rule catalog into the opa-policy ConfigMap"
  [ -x "$SENTRY_DIR/.venv/bin/python" ] || { say "Sentry's Python env, once (uv sync)"; (cd "$SENTRY_DIR" && uv sync --frozen --no-dev --python 3.11 >/dev/null); }
  k -n $DGNS get cm opa-policy -o yaml > "$STATE/opa-policy-before.yaml" 2>/dev/null || true
  (cd "$SENTRY_DIR" && kubectl config use-context "$CTX" >/dev/null && bash deploy/create-opa-configmap.sh) | tail -2
  changed "opa-policy ConfigMap from Sentry's catalog (deploy/create-opa-configmap.sh)" "kubectl -n $DGNS apply -f $STATE/opa-policy-before.yaml (empty: delete the ConfigMap)"
  k -n $DGNS rollout restart deploy/opa deploy/data-governance-ui >/dev/null   # both read it once at start
  k -n $DGNS rollout status deploy/opa --timeout=300s >/dev/null; k -n $DGNS rollout status deploy/data-governance-ui --timeout=300s >/dev/null
  rules=$(opa_rules); [ "$rules" = "$want" ] && ok "OPA serves the policy: $rules" \
    || die "OPA serves '${rules:-nothing}', want $want" "kubectl --context $CTX -n $DGNS logs deploy/opa; deploy/create-opa-configmap.sh in $SENTRY_DIR"
}
opa_rules() {   # the rule ids in the policy OPA serves (through a port-forward: a NetworkPolicy keeps OPA private)
  local port=$((20000 + RANDOM % 20000)) pf
  kubectl --context "$CTX" -n $DGNS port-forward svc/opa $port:8181 >/dev/null 2>&1 & pf=$!   # kubectl itself: $! is the process kill stops
  sleep 3
  curl -fsS -m 10 "http://localhost:$port/v1/policies" 2>/dev/null | python3 -c 'import json,re,sys
print(" ".join(sorted(set(re.findall(r"DG-0[0-9][0-9]", " ".join(p["raw"] for p in json.load(sys.stdin)["result"]))))))' 2>/dev/null || true
  kill $pf 2>/dev/null || true
}

# ------------------------------------------------------------------------------------------------------------ app
FP='import hashlib,pathlib,sys; r=pathlib.Path(sys.argv[1]); [print(hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(r)) for p in sorted(r.rglob("*.py")) if "__pycache__" not in p.parts and (p.parent==r or p.relative_to(r).parts[0] in ("agents","tools"))]'
app_rev() {   # the app's .py files in four pods (an agent, a tool, the records service, the demo client) vs the pinned checkout
  k -n $NS get deploy records >/dev/null 2>&1 || { echo absent; return; }
  local want spec d c out=""; want=$(python3 -c "$FP" "$APP_DIR/apps/pharmacy_refills")
  for spec in pharmacy-assistant:agent export-dispense-batch:mcp records:records demo-client:client; do
    d=${spec%%:*}; c=${spec#*:}
    [ "$(k -n $NS exec deploy/$d -c $c -- python3 -c "$FP" /app/app 2>/dev/null)" = "$want" ] || out="$out $d"
  done
  [ -z "$out" ] && echo same || echo "other code in:$out"
}
step_app() {
  at_pin agent-examples-snp "$APP_DIR"
  if k get ns $NS >/dev/null 2>&1; then
    local r; r=$(app_rev)
    if [ "$r" = same ] && [ -z "$(k -n $NS get pods --no-headers 2>/dev/null | awk '{split($2,a,"/"); if (a[1]!=a[2] && $3!="Completed") print}')" ]; then
      ok "the pharmacy app runs the pinned code (namespace $NS)"; return; fi
    say "namespace $NS holds another pharmacy app ($r), or it is not ready"
    [ "${CONFIRM:-}" = $NS ] || die "replacing it deletes namespace $NS (its pods, its database, its Keycloak clients)" "CONFIRM=$NS setup/sentry-film.sh app"
    step_cli
    k get ns $NS -o yaml > "$STATE/pharmacy-ns-before.yaml"
    _undeploy() { cd "$APP_DIR" && APP=pharmacy_refills KIND_CLUSTER="$CLUSTER" ROSSOCTL_CONTEXT="$CLI_CONTEXT" bash undeploy.sh; }
    run_logged "$STATE/app-undeploy.log" '^(>>|==|ERROR|error)' _undeploy || die "undeploy.sh failed" "read $STATE/app-undeploy.log"
    changed "pharmacy app removed (undeploy.sh)" "deploy the earlier version again"
  else step_cli; fi
  say "the pharmacy app at $(pin agent-examples-snp | cut -c1-9) through the platform's proxy mode (deploy.sh, ~10 min)"
  _deploy() { cd "$APP_DIR" && ROSSOCTL_CONTEXT="$CLI_CONTEXT" KIND_CLUSTER="$CLUSTER" bash deploy.sh --app pharmacy_refills --mode proxy; }
  run_logged "$STATE/app-deploy.log" '^(>>|==|ERROR|error|\[FAIL\])' _deploy \
    || die "deploy.sh failed (its last lines: $(tail -3 "$STATE/app-deploy.log" | tr '\n' ' '))" "read $STATE/app-deploy.log; apps/pharmacy_refills/RUNBOOK.md in $APP_DIR lists its preflight messages"
  changed "pharmacy app deployed (namespace $NS)" "(cd $APP_DIR && APP=pharmacy_refills KIND_CLUSTER=$CLUSTER ROSSOCTL_CONTEXT=$CLI_CONTEXT bash undeploy.sh)"
  [ "$(app_rev)" = same ] && ok "the pods run the pinned code" || die "the pods do not run the pinned code ($(app_rev))" "read $STATE/app-deploy.log"
}

# ----------------------------------------------------------------------------------------------------- instrument
lineage_count() { local n=0 c cm; for cm in $(k -n $NS get cm -o name 2>/dev/null | grep authbridge-config-); do
  c=$(k -n $NS get "$cm" -o jsonpath='{.data.config\.yaml}'); echo "$c" | grep -q lineage-telemetry && echo "$c" | grep -q 'capture_io: true' && n=$((n + 1)); done; echo $n; }
step_instrument() {
  at_pin lab-data-governance "$SENTRY_DIR"
  local want; want=$(k -n $NS get cm -o name 2>/dev/null | grep -c authbridge-config- || true)
  [ "$want" -ge 13 ] || die "only $want sidecar configs in $NS (the app has 13 agents and tools)" "setup/sentry-film.sh app"
  if [ "$(lineage_count)" = "$want" ] && [ -s "$STATE/restore/authbridge-config-report-dispense.json" ]; then
    ok "lineage + capture on all $want agents and tools; restore copies saved"; return; fi
  say "Sentry's lineage wiring on every agent and tool (dg.sh namespace $NS instrument, ~20 min)"
  _instrument() { cd "$SENTRY_DIR" && kubectl config use-context "$CTX" >/dev/null && KIND_CLUSTER="$CLUSTER" bash deploy/dg.sh namespace $NS instrument; }
  run_logged "$STATE/instrument.log" '^>>|^error|^Error' _instrument || die "dg.sh namespace $NS instrument failed" "read $STATE/instrument.log (its last lines: $(tail -2 "$STATE/instrument.log" | tr '\n' ' '))"
  changed "lineage wiring + payload capture on $NS (dg.sh namespace $NS instrument; take.sh capture)" "no uninstrument verb: redeploy the app (CONFIRM=$NS setup/sentry-film.sh app); capture alone: kubectl apply -f $STATE/authbridge-configs-capture-off.yaml"
  "$PKG/take/take.sh" capture | tail -1
  [ "$(lineage_count)" = "$want" ] && ok "lineage + capture on all $want; restore copies in $STATE/restore/ (take.sh relineage uses them)" \
    || die "lineage + capture on $(lineage_count) of $want" "read $STATE/instrument.log; run this step again"
  "$PKG/take/take.sh" pin
}

# --------------------------------------------------------------------------------------------------------- doctor
step_doctor() {
  FAILS=0; say "cluster $CLUSTER ($CTX in $KUBECONFIG), gateway :$HTTP_PORT"
  k get nodes >/dev/null 2>&1 && ok "cluster answers" || { bad "cluster does not answer" "setup/sentry-film.sh cluster"; return 1; }
  local g; g=$(platform_gaps); [ -z "$g" ] && ok "agent platform complete" || bad "platform: $(echo "$g" | tr '\n' ';')" "setup/sentry-film.sh cluster"
  [ "$(k -n rossoctl-system get deploy rossoctl-backend -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="ENABLE_AUTH")].value}')" = "$(pin platform ui_auth)" ] \
    && ok "platform UI without a login (its catalogs are shot)" || bad "the platform UI asks for a login; the catalog stills cannot be shot" "setup/sentry-film.sh cluster"
  curl -fsS -m 10 -o /dev/null "http://dg.localtest.me:$HTTP_PORT/ui/traces" && ok "Sentry's console at http://dg.localtest.me:$HTTP_PORT/ui/traces" \
    || bad "Sentry's console does not answer on :$HTTP_PORT" "setup/sentry-film.sh sentry"
  local sha revs; sha=$(pin lab-data-governance); revs=$(sentry_revs 2>/dev/null || true)
  [ -n "$revs" ] && ! echo "$revs" | awk '{print $2}' | grep -vqx "$sha" && ok "Sentry pods at ${sha:0:9}" || bad "Sentry pods not all at ${sha:0:9}" "setup/sentry-film.sh sentry"
  k -n rossoctl-system get cm otel-collector-config -o jsonpath='{.data}' 2>/dev/null | grep -q 'traces/data_governance' \
    && ok "the platform's collector forwards to Sentry" || bad "the collector does not forward to Sentry" "setup/sentry-film.sh sentry (dg.sh wires it)"
  local d name want have
  while read -r d name want; do have=$(k -n $DGNS get deploy "$d" -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$name\")].value}")
    [ "$have" = "$want" ] && ok "$name=$want" || bad "$d $name=${have:-(unset)}, want $want" "setup/sentry-film.sh sentry"
  done < <(python3 -c 'import yaml,sys
for d,kv in yaml.safe_load(open(sys.argv[1]))["settings"]["env"].items():
    for k,v in kv.items(): print(d, k, v)' "$PKG/pins.yaml")
  [ "$(k -n $DGNS get deploy data-governance-ui -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')" = "$(pin settings ui_memory)" ] \
    && ok "console memory $(pin settings ui_memory)" || bad "console memory is not $(pin settings ui_memory)" "setup/sentry-film.sh sentry"
  local want_rules; want_rules=$(pin settings opa_rules); [ "$(opa_rules)" = "$want_rules" ] && ok "OPA's policy: $want_rules" \
    || bad "OPA's policy rules are not $want_rules" "setup/sentry-film.sh sentry (it compiles Sentry's rule catalog into the opa-policy ConfigMap)"
  local r; r=$(app_rev 2>/dev/null || echo absent); [ "$r" = same ] && ok "pharmacy runs the pinned code" || bad "pharmacy: $r" "setup/sentry-film.sh app"
  local nr; nr=$(k -n $NS get pods --no-headers 2>/dev/null | awk '{split($2,a,"/"); if (a[1]!=a[2] && $3!="Completed") print $1}' | tr '\n' ' ')
  [ -z "$nr" ] && ok "pharmacy pods ready" || bad "pharmacy pods not ready: $nr" "kubectl --context $CTX -n $NS get pods"
  local n; n=$(lineage_count); [ "$n" -ge 13 ] && ok "lineage + capture on $n agents and tools" \
    || bad "lineage + capture on $n of 13" "take/take.sh relineage <deploy> after a roll, or setup/sentry-film.sh instrument"
  [ -s "$STATE/restore/authbridge-config-report-dispense.json" ] && ok "restore copies for take.sh relineage" || bad "no restore copies in $STATE/restore (they are per checkout)" "copy them from the checkout that ran instrument on this cluster (its state/$CLUSTER/restore/), or run setup/sentry-film.sh instrument (~20 min)"
  k -n $NS exec deploy/records -c records -- python3 -c "import urllib.request;urllib.request.urlopen('http://host.containers.internal:11434/api/tags',timeout=5)" >/dev/null 2>&1 \
    && ok "the cluster reaches ollama on this host (probed from the records pod: no sidecar, so no trace)" || bad "the cluster cannot reach ollama at host.containers.internal:11434" "$(hint "brew services restart ollama" "sudo systemctl restart ollama"); OLLAMA_HOST=0.0.0.0 if it listens on 127.0.0.1 only (setup/sentry-film.sh check prints the Linux drop-in); Linux: a pod must resolve host.containers.internal (setup/sentry-film.sh cluster adds it to CoreDNS)"
  local last
  if ! last=$("$PKG/take/take.sh" since 2>/dev/null); then bad "could not read when the last pharmacy conversation was" "kubectl --context $CTX -n $DGNS get pod data-governance-postgres-0"
  elif [ -z "$last" ]; then ok "no pharmacy conversation yet"
  elif [ "$last" -ge 960 ]; then ok "last pharmacy conversation ${last}s ago (a refill needs > 15 min)"
  else bad "the last pharmacy conversation was ${last}s ago" "wait $((960 - last)) s: a refill inside another run's 15-minute label window inherits its labels"; fi
  [ "$FAILS" = 0 ] && say "ready" || { say "$FAILS check(s) failed"; return 1; }
}

# ----------------------------------------------------------------------------------------------------------- plan
step_plan() {
  say "cluster $CLUSTER, settings $ENVF"
  if ! cluster_exists; then note "cluster: absent → cluster creates it (host ports $HTTP_PORT/$HTTPS_PORT) and installs the platform"
    note "sentry, app, instrument: fresh installs"; return; fi
  k get nodes >/dev/null 2>&1 || { note "cluster: exists but does not answer → podman start $NODE, or CONFIRM=$CLUSTER recreate"; return; }
  local g; g=$(platform_gaps); [ -z "$g" ] && ok "platform: complete → kept" || note "platform: missing $(echo "$g" | tr '\n' ';') → cluster re-runs the installer"
  if k get ns $DGNS >/dev/null 2>&1; then
    local m sha revs; m=$(migration_state); sha=$(pin lab-data-governance); revs=$(sentry_revs 2>/dev/null || true)
    if [ "${m%% *}" != - ] && [ "${m%% *}" \> "${m##* }" ]; then note "sentry: database at ${m%% *}, newer than the pin's ${m##* } → needs CONFIRM=$DGNS (drops its data)"
    elif [ -n "$revs" ] && ! echo "$revs" | awk '{print $2}' | grep -vqx "$sha"; then ok "sentry: at ${sha:0:9} → kept (settings checked)"
    else note "sentry: another version → rebuilt at ${sha:0:9} and rolled; its data is kept (migration ${m%% *} → ${m##* })"; fi
  else note "sentry: absent → installed"; fi
  if k get ns $NS >/dev/null 2>&1; then local r; r=$(app_rev)
    [ "$r" = same ] && ok "app: at the pin → kept" || note "app: $r → replaced, needs CONFIRM=$NS (deletes namespace $NS)"
  else note "app: absent → deployed"; fi
  local others; others=$(k get ns --no-headers 2>/dev/null | awk '{print $1}' | grep -vE '^(kube-|default|local-path|cert-manager|cr-system|istio|keycloak|rossoctl-system|spire|zero-trust|data-governance|pharmacy|team1|team2|tekton-|shipwright-)' | tr '\n' ' ' || true)
  [ -n "$others" ] && note "other namespaces (left alone; their traffic also reaches Sentry): $others"
  return 0
}

case "${1:-}" in
  check|sources|recreate) "step_$1" ;;
  media) step_media "${2:-}" ;;
  plan|cluster|cli|sentry|app|instrument|doctor) kubeconfig_has; "step_$1" ;;
  up) step_sources; kubeconfig_has; step_cluster; step_cli; step_sentry; step_app; step_instrument; step_doctor ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 2 ;;
esac
