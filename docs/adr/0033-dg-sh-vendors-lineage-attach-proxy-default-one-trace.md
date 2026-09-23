---
status: accepted
---

# `dg.sh` vendors the lineage-attach capability and defaults to a proxy lineage sidecar, for a self-contained one-trace `instrument`

> **Revision (issue #256, 2026-09-23):** This decision still governs bare,
> component-labelled workloads. Trusted Rossoctl workloads carrying
> `rossoctl.io/type=agent|tool` and an admission-injected enforcing proxy use a
> stricter convergence flow: preflight every producer and application image,
> roll the shimmed application first, then reconcile the freshly regenerated
> ConfigMap and verify `/v1/pipeline` hot reload without a second rollout. Auth
> plugins and the platform sidecar remain platform-owned. Existing shim images
> are re-attested from their contents rather than trusted by an `-otel` tag;
> listener ports/backend are derived from the admitted pod's platform contract,
> and those values plus the exact auth-plus-managed plugin sequences are part of
> convergence. A later platform
> reconciliation can still remove the overlay, which `dg.sh status` reports as
> `live=no` with a reason.

`dg.sh namespace <ns> instrument` activates AuthBridge-sidecar lineage on a
namespace's agents/tools so their traffic produces the facts-only spans this
component consumes. ADR-0032 decided `dg.sh` **drives the EXTERNAL cortex #852
lineage-attach kit** (via `--cortex-local-path`), injects an **envoy** lineage
sidecar on a no-sidecar entity, and **skips** any entity that already carries a
sidecar. This ADR **reverses the first two of those** and refines the third:

1. **Vendor the whole capability into this repo** (`deploy/lineage-attach/`) and
   **retire `--cortex-local-path`** — `dg.sh` relies only on its own local
   resources; no cortex checkout is required.
2. **Default the injected sidecar to a lineage-only `proxy` sidecar**, choosing
   `envoy` only when the namespace is already envoy-configured.
3. **Re-enable in-place activation** of an entity that already has a sidecar
   (append the `lineage-telemetry` plugin), as a **best-effort, verify-after-roll**
   step — not the unconditional skip ADR-0032 settled on.

It also brings the "one trace" outcome fully in scope: a second, app-source-free
**turn-span shim** is baked into the app image alongside the propagate-only shim,
because the sidecar work alone does not collapse the demo's fragments into one
trace (see the "One trace needs two shims" section).

This **supersedes ADR-0032**. ADR-0031 (non-reversible, mode-preserving) stays
accepted for the legacy path: its in-place activation appends a plugin and
never removes, replaces, or mode-switches a sidecar. Issue #256 supersedes only
that additive claim for trusted Rossoctl proxies: it may replace the application
image and canonicalize DG-managed listener/parser/lineage fields, but it still
never changes sidecar mode or removes/replaces platform-owned auth plugins.

## Context

### What the kit is, and why ADR-0032 drove it externally

The cortex #852 kit (`authbridge/lineage-attach/`) is an **envoy-sidecar-only**
attach kit: a generator (`attach-lineage.sh`), a live applier
(`sidecar-patch.sh`, six read-only preconditions → dry-run → apply → print a
reverse-patch back-out), and a propagate-only Python shim (`build-otel-shim.sh` +
`Dockerfile.otel-shim` + `lineage-propagate-hook.py`, env-gated on
`LINEAGE_PROPAGATE=1`). ADR-0032 chose to **drive** it from a sibling cortex
checkout rather than vendor it, on the reasoning that the kit was reviewed and
hardened and the dev cluster already had cortex on disk.

Three facts about that arrangement drive this reversal:

1. **The kit cannot inject a proxy sidecar.** Its ConfigMap hardcodes `mode:
   envoy-sidecar` and its patch adds `envoy-proxy` as a native `initContainer`
   sidecar (+ `proxy-init`). There is no proxy path in it at all.
2. **`instrument` was narrowed to no-sidecar-only** (ADR-0032 revised) because
   editing an operator/webhook-injected sidecar's per-workload config ConfigMap
   is **clobbered on the next roll** (the CM is operator-owned and regenerated),
   and the platform's **enforcing** proxy sidecar (jwt / token-exchange) **401s**
   the demo's unauthenticated MCP/A2A calls.
3. **Driving an external kit means `dg.sh` is not self-contained** — `instrument`
   requires a cortex checkout the operator must provide and keep in step.

### One trace needs two shims (not just a sidecar)

Measured live (2026-09-14): with only the sidecar + the kit's propagate-only
shim, the travel_advisor demo produces **11 traces / 98 spans** — one main tree
plus a fragment per OpenAI-Agents tool call. The sidecar cannot fix this: the
fragmenting call carries a **valid but wrong (startup) `traceparent`**, and the
wire contract (v1.7.0 §3.3) forwards a valid traceparent byte-for-byte. The
correction has to happen at the **producer** of the header — in-process
instrumentation — not the sidecar.

The fix is a **second, app-source-free shim** (`rossoctl_turnspan`): a uvicorn
ASGI middleware that opens one span per request scope seeded from the inbound W3C
context, plus a patch of MCP's `send_request` / `_handle_post_request` to
re-attach the turn context around the tool-call POST (MCP posts from a long-lived
task whose contextvars predate the turn span). With **both** shims in the same
image the demo collapses to **1 trace / 98 spans / 14 entities** (all agents +
all tools). The trap: the turn-span shim WITHOUT the propagate hook's
`initialize()` leaves httpx instrumentation OFF and produces **27** fragments —
worse. So the two shims are complementary and must ship in one image.

The turn-span shim was previously an **unsanctioned scratch prototype** (never in
cortex, tracked by no subproject). This ADR gives it a reviewed, tracked home.

## Decision

### 1. Vendor the whole lineage-attach capability; retire `--cortex-local-path`

The kit's scripts, `Dockerfile.otel-shim`, and Python hooks are vendored **as-is**
into `deploy/lineage-attach/`, adapted where this ADR requires (the proxy path,
the allowlist iptables, the turn-span bake). `dg.sh` shells out to its **own local
copies**; the `--cortex-local-path` / `CORTEX_LOCAL_PATH` option and the
`resolve_kit_dir` preflight are removed. The data-governance extension stands
alone: no sibling cortex checkout is required for any verb.

Vendored **as-is** (not folded into `dg.sh`) so the reviewed, input-validating,
hardened surface is preserved and stays independently runnable and testable;
`dg.sh` keeps its thin orchestration role (preflights, decision table,
crash-loop watch). This is the deliberate reversal of ADR-0032 Decision 1, whose
own trade-off note said the vendoring alternative "honors the self-contained goal
but re-owns reviewed code and drifts" — the priority is now self-containment of
the DG extension over avoiding a vendored copy.

### 2. The injected sidecar defaults to a lineage-only proxy; envoy only when the namespace is already envoy-configured

The owner-split for a targeted entity becomes (all auto-detected, no flag):

| current state | action | owner |
|---|---|---|
| no sidecar, namespace **not** envoy-configured | inject a **lineage-only `proxy`** sidecar (+ two-shim image for a Python app) | DG proxy injector (new) |
| no sidecar, namespace **already** envoy-configured | inject an **`envoy`** lineage sidecar (+ two-shim image) | vendored envoy path |
| sidecar present, **no** `lineage-telemetry` in its pipeline | **append** `lineage-telemetry` in place (best-effort — Decision 3) | `dg.sh` in-place edit |
| sidecar present, `lineage-telemetry` **already** wired | **no-op** | — |

The injected proxy carries **only** the `lineage-telemetry` plugin — it is
**auth-free**, deliberately NOT the platform's enforcing token-exchange/jwt
proxy, which would 401 the demo's unauthenticated calls. "Already
envoy-configured" is detected from the namespace (the presence of the platform
`envoy-config` ConfigMap), not a flag.

The wire contract is **v1.7.0**, which makes `namespace` (or `namespace_file`) a
**required** producer config key (§6): the `lineage-telemetry` plugin refuses to
start without it, and an older producer image rejects a config that carries it
(unknown keys are a boot error — image and config change together). Every sidecar
config `dg.sh` generates or edits — the injected proxy's, the injected envoy's,
and the in-place append (Decision 3) — must therefore supply the workload's
namespace. `dg.sh` sets **`namespace_file`** to the pod's projected
serviceaccount namespace (`/var/run/secrets/kubernetes.io/serviceaccount/namespace`),
the one source that stays correct in a config templated or copied across
namespaces, rather than hardcoding `namespace`. The vendored kit's envoy path
already writes its `NAMESPACE`; the new proxy path and the in-place append must
match it, or the producer boot-crashes (caught by the crash-loop watch, but
avoided by construction).

The injected proxy captures the app's egress via **transparent iptables in
include-only (allowlist) mode**: only the HTTP hops the proxy can parse — **A2A
`8080` and MCP `8000`** by default — are redirected into it; every other port
(Postgres, SMTP, object store, the LLM `:443` TLS tunnel) passes through
untouched. This is the inverse of the kit's `OUTBOUND_PORTS_EXCLUDE` denylist and
is **fail-safe**: a port not on the allowlist stays direct (correct) rather than
being redirected into a proxy that cannot parse it (broken). It reproduces the
"irreducible opaque legs" shape a healthy trace already has. It is implemented as
a **new `OUTBOUND_PORTS_INCLUDE` path in the vendored `init-iptables.sh`**,
additive — the existing denylist path is left intact for the envoy branch.

### 3. In-place activation is re-enabled as a best-effort, verify-after-roll append

An entity that already has a sidecar without `lineage-telemetry` gets the plugin
**appended** to its pipeline, its existing auth plugins left **exactly as-is**
(append, never strip). Because a webhook/operator-injected sidecar's per-workload
CM is operator-owned and regenerated on the roll, this is **best-effort**:

- After the append + roll settles, `dg.sh` **verifies** the plugin is actually
  live in the running sidecar's pipeline. If it is gone, `dg.sh` **warns loudly**
  that the operator clobbered the CM (and points at the durable future options),
  rather than reporting a success it did not achieve.
- If the existing sidecar carries auth plugins (jwt-validation / token-exchange),
  `dg.sh` **warns** that lineage will record 401s for unauthenticated callers —
  a property of that pipeline, not something appending lineage can fix.

This is the "try (ii) first" choice. Two durable alternatives are recorded but
not built: (i) patch the Deployment so the **operator itself** re-renders the
plugin (needs operator support), and (iii) skip operator-injected sidecars
specifically while still configuring `dg.sh`-injected and template-baked ones.

### 4. Both shims are baked into the same image; the turn-span shim lives in this repo, not the demo app

The turn-span shim (`rossoctl_turnspan.py` + its `.pth`) is vendored into
`deploy/lineage-attach/` as reviewed siblings of `lineage-propagate-hook.py`, and
`Dockerfile.otel-shim` is extended to install **both** shims into the app's
site-packages. One bake produces one `-otel` image carrying activation **and** the
turn span — making the activation-less 27-fragment trap structurally unreachable.
`build-otel-shim.sh`'s attestation is extended to assert the turn-span module is
importable. The **demo application source is never touched** — both shims attach
through the environment (a `.pth` at interpreter start), exactly as the
propagate-only shim already does.

### 5. Idempotency is by lineage-plugin detection — no marker

`instrument` places no marker on what it injects. Re-run safety comes from
**detecting whether `lineage-telemetry` is already wired in the sidecar's
pipeline**: wired → no-op; sidecar-but-not-wired → in-place append (Decision 3);
no sidecar → inject (Decision 2). This is origin-agnostic — a `dg.sh`-injected
proxy and an operator-injected sidecar are treated the same once lineage is
present, because a wired sidecar needs no further action regardless of who
injected it. It requires extending sidecar detection to **inspect the plugin
pipeline** (not merely "a sidecar exists"); `dg.sh namespace <ns> status`
surfaces a per-entity **`lineage=yes/no`** so the non-reversible action stays
observable (ADR-0031 decision 2).

## Why

- **Self-containment beats drift-avoidance here.** ADR-0032 optimized for not
  re-owning reviewed code; the user's requirement is that the DG extension rely
  on its own resources. Vendoring as-is keeps the reviewed surface while cutting
  the cortex-checkout dependency — the exact trade ADR-0032 weighed and now
  resolves the other way.
- **Proxy-default matches the demo's reality without a mode switch.** The demo's
  entities are bare (no sidecar); a lineage-only proxy captures their HTTP hops
  without introducing auth that would 401 them, and without recreating pods into
  a namespace-wide envoy mode (ADR-0031's footgun). Envoy stays available,
  auto-selected where the namespace is already set up for it.
- **The allowlist is the fail-safe egress model.** "Redirect only A2A/MCP" cannot
  break a non-HTTP leg by forgetting to exclude it; the denylist can. Owning the
  vendored `init-iptables.sh` makes this a local change, not a cortex PR.
- **One trace requires the turn span.** The headline goal ("one trace") is
  unreachable with sidecar work alone; baking both shims into one image is the
  only combination proven to reach it, and doing it in the bake makes the
  worse-than-baseline trap impossible.
- **No marker keeps `instrument` from mutating more than it must** and still gives
  a correct, origin-agnostic idempotent re-run, since the wired-lineage state
  carries the whole signal.

## Consequences

- `dg.sh` no longer accepts `--cortex-local-path`; `instrument` needs no cortex
  checkout. Any script or runbook passing that flag must drop it.
- The supported one-trace path for the travel_advisor demo is: bare no-sidecar
  deploy → `instrument` injects a lineage-only proxy with the two-shim image and
  the A2A/MCP allowlist. The demo app carries no instrumentation of its own.
- In-place activation on an operator-injected sidecar may silently revert on the
  next operator-driven roll; `dg.sh` surfaces this by verify-after-roll rather
  than hiding it. Durable in-place activation (options i/iii) is future work.
- **This design's one-trace outcome on the proxy path is a required acceptance
  gate, not an assumption.** The 1-trace result was measured on an *envoy*
  sidecar; the proxy entry-hop mint may fold the inbound hop into the tree
  differently. The work is done only when a live wipe→instrument→demo→inspect run
  on the **proxy** sidecar shows one trace (not the 11-fragment baseline) with
  every entity `detected_from='sidecar lineage span'`. If it does not, the design
  bends (a proxy entry-hop fix, or an envoy-default fallback) rather than shipping.
- For the legacy #239 path, ADR-0031's two decisions survive: activation is
  additive (append, never remove/replace, never mode-switch) and non-reversible
  in v1. Its Decision 1 wording ("never edits an existing sidecar") is refined
  by this ADR's Decision 3 — appending a plugin is an edit, but an additive one.
  The trusted #256 exception is scoped above: it remains mode-preserving and
  auth-preserving while replacing only its application/DG-managed fields.

## Alternatives considered

- **Keep driving the external kit (ADR-0032 as-is)** — rejected: fails the
  self-containment requirement; the kit also cannot inject a proxy.
- **Vendor only the proxy + turn-span delta, keep driving the kit for envoy** —
  rejected: leaves the cortex-checkout dependency for the envoy branch, so the
  extension is still not self-contained.
- **Absorb the kit's logic into `dg.sh` functions** — rejected: re-owns and
  re-reviews the hardened generator/patch surface for no gain over vendoring the
  scripts as-is.
- **Inject a full-auth proxy (jwt + token-exchange + lineage)** — rejected for the
  demo: 401s the unauthenticated MCP/A2A calls unless a full Keycloak/routes setup
  is stood up, which the demo does not need and the lineage flow does not require.
- **Egress capture via HTTP_PROXY + app port relocation** (the scratch
  proxy-sidecar recipe) — rejected in favor of transparent iptables: relocation
  is Deployment surgery and only captures proxy-aware clients; the allowlist
  captures the parseable hops transparently and leaves everything else direct.
- **Egress capture via the kit's existing exclude denylist** — rejected: the
  per-app "remember to exclude every non-HTTP port or it breaks" footgun; the
  allowlist is fail-safe.
- **Keep the unconditional no-sidecar-only skip (ADR-0032 revised)** — rejected:
  the user wants an already-sidecar'd entity brought onto lineage where possible;
  best-effort in-place append with verify-after-roll does that honestly, and the
  demo never exercises the in-place path anyway (its entities are bare).
- **Mark what `instrument` injects (annotation/CM name) for idempotency** —
  rejected: unnecessary, because detecting the wired lineage plugin is a
  sufficient, origin-agnostic idempotency signal; a marker would add mutation for
  no decision it enables.
- **Ship the turn-span shim in the demo app source** — rejected: the demo app is
  kept clean; the shim attaches through the environment like the propagate hook,
  touching no app source.
