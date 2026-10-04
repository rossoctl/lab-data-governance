"""Shared fake kubectl ConfigMap read/apply handlers for deploy tests."""

CONFIGMAP_KUBECTL = r"""
# ---- ConfigMap reads for status and existing-proxy reconciliation ------------
if [[ "$*" == *"get"* && ( "$*" == *"configmap"* || "$*" == *" cm "* || "$*" == *" cm"* ) ]]; then
  cmname=""; prev=""
  for a in "$@"; do
    case "$prev" in configmap|cm|configmaps) cmname="$a"; break ;; esac
    prev="$a"
  done
  if [[ -n "${CM_FORCE_NOTFOUND:-}" && "$cmname" == "$CM_FORCE_NOTFOUND" ]]; then
    printf '%s\n' "Error from server (NotFound): configmaps \"$cmname\" not found" >&2
    exit 1
  fi
  if [[ "${CM_GET_FAILS:-0}" == "1" ]]; then
    printf '%s\n' 'Error from server (InternalError): an error on the server ("") has prevented the request from succeeding' >&2
    exit 1
  fi
  f="$FIXDIR/cm-${cmname}.json"
  # An apply records the wired body. POD_CLOBBER=1 models the operator replacing
  # the ConfigMap later, so reads return the original body instead.
  wired_marker="$FIXDIR/appended-${cmname}"
  if [[ -f "$wired_marker" && "${POD_CLOBBER:-0}" != "1" ]]; then
    f="$FIXDIR/cm-${cmname}-wired.json"
  fi
  if [[ -n "$cmname" && -f "$f" ]]; then
    # The full-doc query must not match a jsonpath query, whose spelling also
    # contains "-o json". The other forms model status's ConfigMap data reads.
    if [[ "$*" == *"-o json "* || "$*" == *"-o json" || "$*" == *"-ojson "* || "$*" == *"-ojson" ]]; then
      cat "$f"; exit 0
    fi
    if [[ "$*" == *"config\.yaml"* || "$*" == *"config.yaml"* ]]; then
      python3 - "$f" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
sys.stdout.write((doc.get("data") or {}).get("config.yaml", ""))
PY
      exit 0
    fi
    python3 - "$f" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
data = doc.get("data") or {}
inner = " ".join(f"{k}:{v}" for k, v in data.items())
sys.stdout.write("map[" + inner + "]")
PY
    exit 0
  fi
  printf '%s\n' "Error from server (NotFound): configmaps \"${cmname}\" not found" >&2
  exit 1
fi

# ---- Capture the ConfigMap applied by dg.sh ---------------------------------
# Store the exact full document, then serve it from subsequent reads unless
# POD_CLOBBER models a later operator replacement.
if [[ "$1" == "apply" && ( "$*" == *"-f -"* || "$*" == *"-f-"* ) ]]; then
  # Stash the applied manifest to a temp FILE and pass its PATH to python as argv
  # — NOT via a pipe, which would collide with the heredoc that feeds python its
  # program on stdin (a heredoc `python3 - <<PY` already occupies stdin).
  applied_f="$(mktemp)"; cat > "$applied_f"
  python3 - "$FIXDIR" "$applied_f" <<'PY'
import json, sys, os
fixdir, applied_f = sys.argv[1], sys.argv[2]
doc = json.load(open(applied_f))   # a full ConfigMap JSON doc
name = (doc.get("metadata") or {}).get("name", "")
if not name:
    sys.exit(0)
os.makedirs(fixdir, exist_ok=True)
open(os.path.join(fixdir, f"appended-{name}"), "w").close()
# Keep the applied document intact for verification and sibling-key assertions.
json.dump(doc, open(os.path.join(fixdir, f"cm-{name}-wired.json"), "w"))
PY
  rm -f "$applied_f"
  exit 0
fi

"""
