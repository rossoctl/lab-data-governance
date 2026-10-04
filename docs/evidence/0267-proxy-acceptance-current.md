# PR #267: proxy-mode acceptance evidence and current limits

Recorded on 2026-10-04 from the retained 2026-09-28 fresh-cluster logs,
read-only checks, and two new live runs on `kind-epic239-proxy-e2e`. Both post-fix
single-client demos passed the one-causal-trace structural checks. Issue #246's
full acceptance gate remains open: lineage status is still `partial`, and the
concurrent-client scenario has not been rerun with the new shim.

## Post-fix PR #267 deploy → instrument → demo, 2026-10-04

The Data Governance PR worktree was at signed-off commit `094f7cf`.
`pyproject.toml` matched the existing checkout, and its lockfile passed
`uv lock --check --offline`. The unchanged classification image was kept at
`sha256:2bf62bbc…`; the receiver/UI/interactions/data-lineage shared image
was rebuilt from the PR at `sha256:b5406959…`.

| Command or operation | Observed result |
| --- | --- |
| `uv lock --check --offline --project .` and `UV_CACHE_DIR=/tmp/pr267-uv-cache uv lock --check --offline --project .` | The first check exited 2 because the sandbox could not create a temporary file under the host uv cache. The retry using a writable temporary cache exited 0 and resolved 83 packages. |
| `podman build -f Containerfile --build-arg 'APP_VERSION=094f7cf (2026-10-04)' -t data-governance/receiver:latest .` | Exited 0; shared PR image `sha256:b5406959…`. Tagged it for receiver/UI and loaded both names into `epic239-proxy-e2e` with `kind load docker-image` (exit 0). Kind failed a fast retag of the UI alias, then loaded that alias successfully. |
| `KIND_CLUSTER=epic239-proxy-e2e bash deploy/dg.sh component install` | Interrupted before any Kubernetes apply while rebuilding the unchanged classification image's large runtime environment. The shared PR image had already been loaded. This was an operator interruption, not a failed application build. |
| `KIND_CLUSTER=epic239-proxy-e2e bash deploy/dg.sh component install --no-build` | Exited 0; applied PR manifests, confirmed the existing `traces/data_governance` collector tee, and rolled receiver, UI, and interactions. `dg.sh component status` reported all deployments ready and tee wired. The three rolled deployments used image `sha256:b5406959…`. |
| `kubectl -n data-governance rollout restart deployment/data-governance-data-lineage` and `rollout status` | Exited 0; data-lineage also picked up shared PR image `sha256:b5406959…`. `dg.sh` does not include it in its install restart set. |
| `python3 /tmp/pr267-reset-demo-workloads.py` (temporary operator script) | It checked the Kind context and all 11 trusted Deployment labels, then restored each app container to its existing `registry.cr-system.svc.cluster.local:5000/agent-examples-snp:latest` base image and removed the old `LINEAGE_PROPAGATE` env entry using a strategic patch. All 11 sequential rollouts exited 0. The following instrumentation would therefore bake the PR shim rather than reattest the old `-otel` image. Sidecars and stores were left in place. |
| `KIND_CLUSTER=epic239-proxy-e2e bash deploy/dg.sh namespace travel-advisor instrument` | Exited 0; one PR two-shim image was built, attested, and loaded as `sha256:6e781fdb…`. All 11 trusted workloads rolled onto it, and each AuthBridge lineage pipeline hot-reloaded and passed the live check. |
| `KIND_CLUSTER=epic239-proxy-e2e bash deploy/dg.sh namespace travel-advisor status` | Exited 0; all 11 rows reported `type=proxy lineage=yes live=yes`. Running agent/tool pods all used shim image `sha256:6e781fdb…`. |
| `SELECT COALESCE(MAX(seq),0) FROM spans` before the demo | Returned `1074`; subsequent SQL used `seq > 1074` to separate this run from earlier data. No database wipe was performed. |
| `APP=travel_advisor bash run-demo.sh` | Exited 0 from the external `demo-client`. It printed fresh A2A context `be90ead21fa44252a4ee39e917516bd7`, booking `BK-3E638E`, authorization `AUTH-8E6F12`, `TaskState.input_required`, and a duplicated final summary. |
| Read-only SQL on the fresh trace | Context `be90ead21fa44252a4ee39e917516bd7` appears on causal trace `951413ab0c80b6b90c43a3b160fc9b29`: **98 distinct spans, one real root, no missing span parents, zero error spans, 27 interactions, one interaction root, and no missing interaction parents**. |
| `GET /api/traces?limit=30` from the running DG UI container | The causal trace was listed first. None of the 12 fresh standalone CONNECT trace IDs appeared in the recent-traces feed. |

The causal trace contains the external client, all four agents, six invoked
tools, and three services: **14 entities**, each with
`detected_from='sidecar lineage span'`. It has ten distinct workload
`lineage.self.id` values. `send-notification` was deployed and instrumented
but not remotely invoked. The stored `lineage_trace_status` is `partial`.

The observation window contained **118 spans across 13 trace IDs**. The other
12 traces were standalone `CONNECT` observations to the LLM gateway,
PostgreSQL, and MailHog, all with `lineage.parent.source=none`; eight had
request/response pairs and four had only a request span when checked. They are
not branches of the 98-span causal trace. This sequential run does not verify
the concurrent MCP-session fix under live cluster traffic.

The key read-only database check used the pre-run `seq=1074` boundary and
the fresh context ID to identify the workflow trace. Its structural counts
can be repeated with:

```sql
SELECT trace_id, COUNT(*) AS spans,
       COUNT(*) FILTER (WHERE parent_id IS NULL) AS roots,
       COUNT(*) FILTER (WHERE error IS TRUE) AS errors
FROM spans WHERE seq > 1074 GROUP BY trace_id ORDER BY spans DESC;
SELECT COUNT(*) AS missing_span_parents FROM spans s
WHERE s.trace_id = '951413ab0c80b6b90c43a3b160fc9b29'
  AND s.parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM spans p
                  WHERE p.trace_id = s.trace_id AND p.span_id = s.parent_id);
SELECT COUNT(*) AS interactions,
       COUNT(*) FILTER (WHERE parent_interaction_id IS NULL) AS roots
FROM interactions WHERE trace_id = '951413ab0c80b6b90c43a3b160fc9b29';
SELECT COUNT(*) AS missing_interaction_parents FROM interactions i
WHERE i.trace_id = '951413ab0c80b6b90c43a3b160fc9b29'
  AND i.parent_interaction_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM interactions p
                  WHERE p.trace_id = i.trace_id AND p.id = i.parent_interaction_id);
SELECT status FROM lineage_trace_status
WHERE trace_id = '951413ab0c80b6b90c43a3b160fc9b29';
SELECT e.kind, e.namespace, e.natural_key, e.detected_from
FROM entities e JOIN (
  SELECT DISTINCT entity_id FROM entity_spans
  WHERE trace_id = '951413ab0c80b6b90c43a3b160fc9b29'
) seen ON seen.entity_id = e.id ORDER BY e.kind, e.natural_key;
```

### Warm-pod rerun

The same deployed pods ran a second `APP=travel_advisor bash run-demo.sh`
without another rollout. Immediately before it, `SELECT COALESCE(MAX(seq),0)
FROM spans` returned `1196`. The demo exited 0 with fresh context
`94db5b0c6deb45d1a0a80bae9f39467a`. Read-only SQL using `seq > 1196`
linked that context to trace `08e27b57b8faaae6d6d813cbab5db650`: **76
spans, one root, zero span errors, zero missing parents, and 21 interactions**.
Its stored lineage status was again `partial`.

The cold first run had 28 `tools/list` spans, eight `create_booking` spans,
and four `get-flights` → MinIO HTTP spans. The warm rerun had **8, 8, and 2**
respectively; all other 58 spans were unchanged. The 20-span reduction in
`tools/list` is consistent with tool discovery after the earlier fresh rollout.
The earlier anomalous 72-span concurrent trace had only four
`create_booking` spans, with the other call attributed to its 80-span
companion. This rerun therefore produced the same 76-span shape as the older
sequential controls, rather than repeating that concurrent attribution error.

## Fresh-cluster deploy → instrument → demo, 2026-09-28

The retained `script` logs include timestamps, output, and exit codes. They do
not include the exact shell invocation for every platform setup/recovery step;
the command column below gives exact commands where the log or runbook identifies
them and labels the rest as an operation. No credential values are recorded.

| Command or operation | Observed result and errors |
| --- | --- |
| Platform Kind setup | First attempt exited 1: host port `8080` was in use. Retry exited 0. |
| `bash deploy/dg.sh component install` | Exited 0; Data Governance component installed. |
| `APP=travel_advisor bash deploy.sh --mode proxy` | Initial attempt exited 2: `authbridge-runtime-config` was not reconciled within 120 seconds. |
| Registry enablement and collector tee restoration | Both recorded operations exited 0. |
| Proxy deploy retry | Exited 2: `cr-system/registry` was missing at preflight. |
| Proxy deploy retry | Exited 1: `search-destinations` rollout timed out. |
| Platform SPIRE installation | First attempt exited 1 waiting for OIDC discovery/Tornjak pods; retry exited 0. Collector tee was restored again (exit 0). |
| Proxy deploy retry | Exited 2: `rossoctl` login/platform was unavailable. |
| Final `APP=travel_advisor bash deploy.sh --mode proxy` retry | Exited 0. The log confirms `mode=proxy`, instrumentation off, and Rossoctl agent/tool import. |
| `bash deploy/dg.sh namespace travel-advisor instrument` | Exited 0. All 11 selected workloads rolled onto the attested application image and reported a hot-reloaded live lineage pipeline. Each admission ConfigMap `kubectl apply` emitted the expected missing `last-applied-configuration` warning; none failed. |
| `APP=travel_advisor bash run-demo.sh` | Exited 0 from the external `demo-client`. The client printed fresh A2A context `79bc8e6752c44c9787ef059d73afe9a9`, booking `BK-3E638E`, and authorization `AUTH-8E6F12`. It also printed `TaskState.input_required` and a duplicated final summary. |
| `bash deploy/dg.sh namespace travel-advisor status` | Exited 0; all 11 agent/tool rows reported `type=proxy lineage=yes live=yes`. Final pod listing showed each agent/tool pod `2/2 Running`. |

The PR body and issue #256 report a separate 98-span / 27-interaction trace
(`b93a0a9aefdebadb845ca903127c1055`) with canonical identities. The retained
logs above do not include the SQL output behind those counts, and that trace is
no longer in the current database after later resets. Treat those figures as a
historical report, not as independently rechecked results of this log set.

## Pre-run database check, 2026-10-04

Before the new live run, the cluster contained five sequential single-client
control runs from 2026-09-29. The following read-only query was run inside
`statefulset/data-governance-postgres` with
`psql -U data_governance -d data_governance`:

```sql
SELECT s.trace_id,
       COUNT(DISTINCT s.span_id) AS spans,
       COUNT(DISTINCT i.id) AS interactions,
       COUNT(DISTINCT es.entity_id) AS entities,
       COUNT(DISTINCT s.span_id) FILTER (WHERE s.parent_id IS NULL) AS roots,
       COUNT(DISTINCT s.span_id) FILTER (WHERE s.error IS TRUE) AS errors,
       MAX(lts.status) AS lineage_status
FROM spans s
LEFT JOIN interactions i ON i.trace_id = s.trace_id
LEFT JOIN entity_spans es ON es.trace_id = s.trace_id
LEFT JOIN lineage_trace_status lts ON lts.trace_id = s.trace_id
WHERE s.trace_id IN (
  '5b5ad61016bd0f66f88e9e1263539c7e',
  '6bd184e1eb7dcbe5535712557d58afc3',
  'a56dd830636a56fd45fa8f2f9872b104',
  'fc583a07cb0062ed593dfd89fba19303',
  '62ef3a7647064b8ad359cc032a0d21d6'
)
GROUP BY s.trace_id ORDER BY s.trace_id;
```

Every row returned **76 spans, 21 interactions, 14 entities, one real root,
zero `error=true` spans, and `lineage_status=partial`**. In the first trace,
joining `entities` through `entity_spans` returned one client
(`travel-advisor-demo-client`), four namespaced agents, six namespaced invoked
tools, and three services. All 14 had `detected_from='sidecar lineage span'`.
`send-notification` was deployed but was not among the invoked tools. A pre-run
read-only `bash deploy/dg.sh namespace travel-advisor status` also exited 0 with
all 11 workloads `type=proxy lineage=yes live=yes`.

Zero span errors do not make these traces fully healthy: their lineage status
is `partial`. The sequential run notes also record repeated downstream peer
session IDs and duplicated final summaries. These observations are separate
from the MCP carrier collision fixed in PR #267.

## Concurrent baseline and post-fix boundary

Before the carrier fix, two simultaneous clients using distinct OAuth identities
exited successfully but produced trace
`3932e9aa7fc750f8e8a4eaed99548b5d` (80 spans, 22 interactions) and trace
`1012e58419d90920993716e15492fe8e` (72 spans, 20 interactions). Both have
one root, 14 entities, zero span errors, and `lineage_status=partial` in the
current DB. The first booking turn had two `create_booking` calls in one trace
and zero in the other; the sequential control has one in each turn. The
pre-fix real-SDK reproducer showed two MCP sessions both using request ID `0`:
the first transport consumed the second turn's carrier, and the second lost it.

After the code fix, a non-mutating probe using the deployed `mcp==1.27.0` SDK
and the patched shim showed each of those two sessions carrying its own W3C
traceparent. The new live image and sequential demo above verify the one-trace
path, but do not repeat the two-client collision scenario. Issue #246 remains
open; this record does not mark its full acceptance checklist complete.
