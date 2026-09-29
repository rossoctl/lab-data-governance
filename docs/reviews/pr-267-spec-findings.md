# PR #267 spec-review findings

Status: deferred for discussion. This document records the Spec-axis findings
from the review of `feat/239-one-trace-epic` against epic #239 and issues
#240–#246 and #256. It does not resolve or accept any finding.

## 1. Full live-acceptance record is incomplete (P1)

Issue #246 requires the proxy-mode acceptance run to verify one causal trace,
one real root, canonical client/agent/tool identities, and
`detected_from='sidecar lineage span'`, and to “record every command, result,
and error.”

The PR body records the successful aggregate result—98 spans, 27 interactions,
four agents, six invoked tools, the authenticated client, and no failed lineage
outcomes—but the branch contains only the worked-example recipe in `README.md`.
Issue #246's checklist remains open, and its only issue comment records the
earlier failed run. The reproducible command-by-command successful run and its
errors are therefore not attached to the implementation or acceptance issue.

## 2. Concurrent MCP sessions can cross-wire trace contexts (P1)

`deploy/lineage-attach/rossoctl_turnspan.py` stores W3C carriers in the
process-global `_mcp_carriers` map using only a JSON-RPC request ID as the key.
Request IDs are unique within a session, not across independent `BaseSession`
instances. Two concurrent sessions that use the same ID can overwrite or
consume each other's carrier, joining a tool call to the wrong turn or
fragmenting its trace. That conflicts with #239's one-causal-trace goal.

Discussion should cover a key that includes session or transport identity and
a behavioral test with interleaved sessions that reuse the same request ID.

## 3. Mixed namespaces can bypass fail-closed preflight (P1)

`enumerate_entities` in `deploy/dg.sh` currently selects `trusted or legacy`.
When at least one trusted Rossoctl Deployment exists, legacy component-labelled
agent/tool Deployments are omitted entirely. Instrumentation can therefore
mutate the trusted workloads without detecting a bare workload in the same
namespace.

Epic #239 says to preflight all workloads before the first mutation and to fail
closed when a workload was not deployed through Rossoctl or lacks the expected
proxy. Discussion should determine whether enumeration must use the union of
trusted and legacy candidates before trusted-proxy preflight rejects invalid
members.

## 4. Import-string Uvicorn applications receive no turn span (P2)

The `uvicorn.Config.__init__` patch in
`deploy/lineage-attach/rossoctl_turnspan.py` wraps callable application objects
but deliberately leaves import strings untouched. With the common
`uvicorn module:app` form, Uvicorn resolves the application after this hook and
runs it without `TurnSpanMiddleware`. Propagation remains active, but one turn
can fragment into multiple traces, despite #244 requiring the startup hook to
create the turn span.

Discussion should cover wrapping after `Config.load()` resolves import strings,
including Uvicorn factory mode, and adding behavioral coverage for both callable
and import-string applications.
