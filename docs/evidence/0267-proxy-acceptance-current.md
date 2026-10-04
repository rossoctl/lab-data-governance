# PR #267: proxy-mode acceptance evidence and current limits

Recorded on 2026-10-04 from the retained 2026-09-28 fresh-cluster logs and
read-only checks of `kind-epic239-proxy-e2e`. This is the evidence available for
epic #239 / issue #246. It does **not** claim a passing post-fix end-to-end gate.

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

## Current database check, 2026-10-04

The existing cluster still contains five sequential single-client control runs
from 2026-09-29. The following read-only query was run inside
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
`send-notification` was deployed but was not among the invoked tools. A fresh
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
traceparent. The shim has **not** been baked into a new live image or subjected
to a post-fix full deploy → instrument → demo → inspect run. Issue #246 remains
open; this record does not mark its acceptance checklist complete.
