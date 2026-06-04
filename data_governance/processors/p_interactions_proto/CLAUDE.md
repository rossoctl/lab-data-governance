# p_interactions_proto — agent instructions

This directory is the **throwaway prototype** for the P-interactions
processor (the LLM/HTTP-transport reconciliation work driven by
ADR-0011). It is not production code, has no tests, and is exercised
via the CLI in this directory plus the `run-proto-cli.sh` driver.

## Dev loop

The prototype is run inside the existing data-governance receiver pod
on the local kind cluster (`kind-kagenti`, namespace
`data-governance`). It does not have its own deployment — the
receiver pod's image happens to contain the same Python tree, so we
hot-copy edited files into `/app/...` and exec the CLI.

The driver script handles all of this:

```
./run-proto-cli.sh <trace_id> [--scramble] [--rebuild-ui]
```

What it does:

1. Looks up the running receiver pod via label
   `app.kubernetes.io/name=data-governance-receiver`.
2. `kubectl cp`s every `.py` file in this directory into the pod at
   `/app/data_governance/processors/p_interactions_proto/`. Files are
   copied individually (a directory `cp` nests instead of overwriting
   — a kubectl footgun documented in the predecessor handoffs).
3. Execs the CLI module against the given trace_id, with `--scramble`
   passed through if set.
4. If `--rebuild-ui` is set, runs `./deploy/build-and-load.sh` to
   rebuild the container image, then `kubectl rollout restart`s
   `deploy/data-governance-ui` so the new pod runs with the rebuilt
   image. Use this when you've edited `data_governance/api/` (the
   API/UI layer) and need the change reflected in the running UI —
   the UI's uvicorn process imports the API module once at startup,
   so hot-copy alone won't reload it. The receiver pod is **not**
   rebuilt; the prototype CLI runs via hot-copy + exec, so receiver
   restart is unnecessary.

The script is required positional `trace_id` — no env-var fallback,
no hardcoded default. Match the CLI's own contract.

## When to use --rebuild-ui

Only when you've edited code that the running UI process has cached
in memory:

- `data_governance/api/__init__.py` (Starlette routes, SQL queries,
  response shaping)
- `data_governance/api/__main__.py` (entrypoint — rare)

You do NOT need `--rebuild-ui` for:

- Edits in this directory (`procedure.py`, `cli.py`, `anchor_rules.py`,
  `caller_inference.py`, `extractor.py`, `__init__.py`) — those are
  hot-copied per run.
- Edits to `data_governance/api/ui/*.html` or `*.js` — those are read
  from disk per request by the UI server. To pick those up, hot-copy
  them into the UI pod (the script does not do this; do it manually
  with a one-off `kubectl cp`).
- Edits to `data_governance/retrieval/` or `data_governance/db/` —
  these matter for the receiver, but the prototype CLI runs in the
  receiver pod via hot-copy of *only this directory*, so a code
  change in those modules requires `--rebuild-ui` (the script reuses
  the same image for both deployments, so a UI rebuild gives you a
  receiver-image rebuild for free; restart the receiver separately
  if you need it).

## Verifying a run

The reference inventory of what the demo trace
`05c6095d1f863dcb3b209ef4761829e1` should produce is in
`demo/entities.md` and `demo/interactions.md` at the repo root. After
running the CLI, diff its output against those two documents — they
were derived independently from the dl-demo source code and are the
regression target.

The DB tables `entities` and `interactions` carry
`retracted_at` tombstones (ADR-0011 §3); default views (`_print_report`
in `cli.py` and the `/proto/interactions/<trace_id>` endpoint in the
API) filter `retracted_at IS NULL`. To inspect tombstoned rows, query
the DB directly:

```
kubectl --context kind-kagenti -n data-governance exec \
  data-governance-postgres-0 -- \
  psql -U data_governance -d data_governance -c \
  "SELECT kind, natural_key, retracted_at IS NOT NULL AS retracted
   FROM entities ORDER BY kind, natural_key;"
```

## Don't

- Don't add tests to this directory. The prototype's verification
  model is "run the script, diff against demo/*.md".
- Don't bake the prototype into a separate deployment or CI job.
  Production work for this lives outside this directory (per
  ADR-0011's productionization phase, not yet started).
- Don't `kubectl cp` directories — always individual files. The
  script does this correctly; replicate the pattern if you script
  anything else.
- Don't restart the receiver pod expecting it to pick up your local
  edits — it loads from the image, not from /app's hot-copied files,
  on restart. Use the script (which hot-copies, then execs).

## Carry-forward from prior sessions

- Pod label is `app.kubernetes.io/name=...`, NOT bare `app=...`.
- The receiver pod has a vestigial nested
  `/app/data_governance/processors/p_interactions_proto/p_interactions_proto/`
  directory left by a prior session's directory-cp. Inactive
  (Python imports the outer module), but harmless to ignore.
- `kind-kagenti` has a known broken kube-proxy (iptables-restore
  overflow). Sanity-check ClusterIP routing before debugging app
  code if you see service-resolution errors.
