# PR #267 spec-review findings

Status: updated 2026-10-04. This document records the Spec-axis findings from
the review of `feat/239-one-trace-epic` against epic #239 and issues #240–#246
and #256. Findings 1, 2, and 4 have updates below; finding 3 was withdrawn.

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

## 3. Mixed-namespace finding withdrawn

Issue #256 scopes instrumentation to workloads with a trusted
`rossoctl.io/type=agent|tool` label. Its fail-closed preflight applies to all
selected trusted workloads, not every legacy component-labelled Deployment in
the namespace. A bare legacy workload beside a trusted one does not veto
instrumentation. The earlier finding incorrectly treated it as a required
veto. Instrumentation now selects only trusted workloads, while read-only
`status` lists both trusted and legacy-labelled candidates for diagnosis.

## 4. Import-string Uvicorn applications now receive a turn span (originally P2)

The original `uvicorn.Config.__init__` patch wrapped callable application
objects but skipped `uvicorn module:app` strings, which Uvicorn resolves later.
The shim now patches `Config.load()` and wraps the resolved ASGI app. A focused
test and a non-mutating probe against the deployed Uvicorn 0.44.0 runtime cover
app objects, import strings, factories, and already-wrapped apps. Each gets
exactly one `TurnSpanMiddleware`. A full post-fix live acceptance run remains
pending under issue #246.
