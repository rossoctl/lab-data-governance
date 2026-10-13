# PR #267 spec-review findings

Status: updated 2026-10-04. This document records the Spec-axis findings from
the review of `feat/239-one-trace-epic` against epic #239 and issues #240–#246
and #256. Findings 1, 2, and 4 have updates below; finding 3 was withdrawn.

## 1. Acceptance evidence now records the observed results (gate still open)

Issue #246 requires the proxy-mode acceptance run to verify one causal trace,
one real root, canonical client/agent/tool identities, and
`detected_from='sidecar lineage span'`, and to “record every command, result,
and error.”

The [current evidence record](../evidence/0267-proxy-acceptance-current.md)
lists the retained fresh-cluster commands and errors, the five pre-run
sequential traces, and two single-client live runs from PR commit `094f7cf`.
The first new demo, immediately after rollout, produced one causal trace with
98 spans, 27 interactions, one real root, no missing parents or span errors,
and 14 canonical entities, all detected from sidecar lineage spans. A warm-pod rerun
produced one 76-span trace with 21 interactions and the same intact root and
parent structure. Both stored lineage statuses remain `partial`; four separate
standalone CONNECT traces in the first run had only request spans. A subsequent
live replay with two concurrent OAuth clients produced two separate 76-span,
21-interaction causal traces with distinct client identities and no misplaced
`create_booking` observations. The `partial` lineage statuses are diagnostic
and do not fail the one-trace gate (ADR-0028 D6). Issue #246's full checklist
remains open for a complete fresh-deploy command record: the post-fix runs
reused the existing namespace, and some earlier fresh-cluster setup/recovery
commands were not retained exactly.

## 2. Concurrent MCP session collision fixed in the shim (originally P1)

The original `deploy/lineage-attach/rossoctl_turnspan.py` stored W3C carriers
in a process-global map keyed only by JSON-RPC request ID. IDs are unique only
within an MCP session, and a pre-fix two-client run plus real-SDK reproducer
observed wrong turn attribution. Commit `d799c1a` places the carrier on the
individual request's transport metadata instead. The focused test and a probe
using the deployed `mcp==1.27.0` SDK showed two sessions with the same ID each
kept its own traceparent. A post-fix live concurrent replay then produced two
overlapping, structurally intact causal traces with their own contexts and
client identities. See the [evidence record](../evidence/0267-proxy-acceptance-current.md).

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
exactly one `TurnSpanMiddleware`. The single-client live demo now passed the
one-trace structural checks; issue #246's full acceptance gate remains open.
