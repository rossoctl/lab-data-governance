"""Shared fake kubectl ConfigMap read/apply handlers for deploy tests."""

# The namespace ConfigMap admission renders every sidecar's config from: as the
# platform ships it, and as `instrument` leaves it.
RUNTIME_CONFIG_WITHOUT_LINEAGE = """pipeline:
  inbound:
    plugins:
    - name: jwt-validation
  outbound:
    plugins:
    - name: token-exchange
"""

RUNTIME_CONFIG_WITH_LINEAGE = """pipeline:
  inbound:
    plugins:
    - name: jwt-validation
    - name: a2a-parser
    - name: mcp-parser
    - name: inference-parser
    - name: lineage-telemetry
      config:
        otel_endpoint: otel-collector.rossoctl-system.svc.cluster.local:4317
        capture_io: false
        namespace_file: /var/run/secrets/kubernetes.io/serviceaccount/namespace
  outbound:
    plugins:
    - name: token-exchange
    - name: a2a-parser
    - name: mcp-parser
    - name: inference-parser
    - name: lineage-telemetry
      config:
        otel_endpoint: otel-collector.rossoctl-system.svc.cluster.local:4317
        capture_io: false
        namespace_file: /var/run/secrets/kubernetes.io/serviceaccount/namespace
"""

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
  # An apply/replace records the wired body. POD_CLOBBER=1 models the operator replacing
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

# ---- Capture the ConfigMap applied or replaced by dg.sh ----------------------
# Store the exact full document, then serve it from subsequent reads unless
# POD_CLOBBER models a later operator replacement.
if [[ ( "$1" == "apply" || "$1" == "replace" ) && ( "$*" == *"-f -"* || "$*" == *"-f-"* ) ]]; then
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
