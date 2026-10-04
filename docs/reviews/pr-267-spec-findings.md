# PR #267 spec-review findings

Status: updated 2026-10-04. This document records the Spec-axis findings from
the review of `feat/239-one-trace-epic` against epic #239 and issues #240–#246
and #256. Findings 1, 2, and 4 have updates below; finding 3 remains for
discussion.

## 1. Acceptance evidence now records the observed results (gate still open)

Issue #246 requires the proxy-mode acceptance run to verify one causal trace,
one real root, canonical client/agent/tool identities, and
`detected_from='sidecar lineage span'`, and to “record every command, result,
and error.”

The [current evidence record](../evidence/0267-proxy-acceptance-current.md) now
lists the retained fresh-cluster command results and errors, the current
read-only database checks, and the pre-fix concurrent failure. It distinguishes
the PR body's historical 98-span / 27-interaction claim (whose raw query output
was not retained) from the five current sequential traces: 76 spans, 21
interactions, one root, 14 entities, zero span errors, and a `partial`
lineage status each. The MCP fix has passed a real-SDK collision probe, but no
post-fix full live gate has been run. Issue #246's checklist remains open; the
record does not claim that its remaining acceptance work has passed.

## 2. Concurrent MCP session collision fixed in the shim (originally P1)

The original `deploy/lineage-attach/rossoctl_turnspan.py` stored W3C carriers
in a process-global map keyed only by JSON-RPC request ID. IDs are unique only
within an MCP session, and a pre-fix two-client run plus real-SDK reproducer
observed wrong turn attribution. Commit `d799c1a` places the carrier on the
individual request's transport metadata instead. The focused test and a probe
using the deployed `mcp==1.27.0` SDK showed two sessions with the same ID each
kept its own traceparent. A full post-fix live acceptance run is still pending;
see the [evidence record](../evidence/0267-proxy-acceptance-current.md).

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

## 4. Import-string Uvicorn applications now receive a turn span (originally P2)

The original `uvicorn.Config.__init__` patch wrapped callable application
objects but skipped `uvicorn module:app` strings, which Uvicorn resolves later.
The shim now patches `Config.load()` and wraps the resolved ASGI app. A focused
test and a non-mutating probe against the deployed Uvicorn 0.44.0 runtime cover
app objects, import strings, factories, and already-wrapped apps. Each gets
exactly one `TurnSpanMiddleware`. A full post-fix live acceptance run remains
pending under issue #246.
