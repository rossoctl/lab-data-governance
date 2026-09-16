# ADR-0033 acceptance gate — live proxy one-trace measurement (#246)

**Status: FAIL (gate not passed) — the design must bend before the epic ships.**
Date: 2026-09-15. Cluster: long-lived Kind `rossoctl` (podman provider), k8s v1.35.0.

This is the [ADR-0033](../adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md)
hard acceptance gate (issue #246, step 7 of epic #239): run the full
wipe → instrument → demo → inspect loop (root `CLAUDE.md` §2–4) on the **proxy**
sidecar and assert the travel_advisor demo produces **one trace** (not the
11-fragment baseline), every entity `detected_from='sidecar lineage span'`.

The 1-trace result had only ever been measured on an *envoy* sidecar. ADR-0033
called out that "the proxy entry-hop mint may fold the inbound hop differently"
and that a gate failure "reshapes the design (a proxy entry-hop fix, or an
envoy-default fallback) — not something to paper over." **This run found a
different, earlier failure: the proxy sidecar cannot be attached to an
outbound-MCP-initiating agent at all — its app crash-loops before the demo can
run.**

## What was exercised

- data-governance epic branch `feat/239-one-trace-epic` @ `1405437` (steps 1–6
  merged): vendored `deploy/lineage-attach/` kit, ADR-0033 proxy-default
  `instrument_entity`, include-only iptables, two-shim `-otel` bake.
- travel_advisor deployed **bare** (no sidecar, no `--with-telemetry`); all 11
  agents/tools confirmed `sidecar=none` before instrument; agents reported the
  expected peer counts (research 0, payment 0, booking 1, travel-advisor 2).
- Instrument driven by the new CLI: `dg.sh namespace travel-advisor instrument`
  (the ADR-0033 path, replacing the old manual `instrument-one.sh` loop).

## Prerequisites resolved along the way (not part of the gate result)

1. **kube-proxy `sendmsg: Message too long`** wedged all ClusterIP routing (65
   Services overflow the netlink socket send buffer). Root cause: the *host*
   `net.core.wmem_default`/`wmem_max` shipped at the distro default 212992 (~208KB).
   Fix: `sudo sysctl -w net.core.wmem_default=16777216 net.core.wmem_max=16777216`
   (+ `rmem_*`, `optmem_max`) then restart the kube-proxy DaemonSet. `wmem_default`
   is load-bearing (a netlink socket doing no `setsockopt(SO_SNDBUF)` gets the
   *default*). A pod restart suffices — no node/cluster recreate. Both `iptables`
   and `nftables` kube-proxy modes fail identically below the buffer; it is not a
   backend choice.
2. **The proxy path needs a sidecar image at wire contract v1.7.0.** The vendored
   `sidecar-patch-proxy.sh` sets `mode: proxy-sidecar` and emits a `namespace_file`
   producer key (v1.7.0, ADR-0033's required-namespace decision). This needs the
   `cmd/authbridge-proxy` image (NOT `cmd/authbridge-envoy`, which is envoy-only —
   RECIPE.md §1 names the envoy image and is a doc gap for the proxy path) built
   from a cortex tree that carries BOTH #761's lineage plugin AND the
   `namespace`/`namespace_file` config field. That field is only on cortex
   `lane/lineage-namespace` @ `3fda5ce5` ("Feat: Emit lineage.self.namespace;
   require namespace (contract v1.7)") — **NOT on cortex `main`** (main is
   pre-v1.7.0; the plugin `DisallowUnknownFields` boot-crashes on `namespace_file`).
   Built `authbridge-proxy:lineage-v170` from that commit; verified `namespace_file`
   + `lineage.self.namespace` in the binary.

## The gate finding (the actual result)

With the correct v1.7.0 `authbridge-proxy` image, `instrument`:

- **Attaches cleanly to MCP-server tools** (inbound only). `get-weather` and the
  other tools reached `sidecar=present type=proxy lineage=yes` and their pods ran
  **2/2 Running** healthy; the sidecar logged `lineage-telemetry: initialized
  self_id=<tool> namespace=travel-advisor`, proxy-init set up iptables, forward
  (:8081) + transparent (:8082) listeners came up.

- **Fails on an agent that initiates outbound MCP at startup.** `payment-agent`
  (framework `openai_agents`) crash-looped: the sidecar was healthy, but the
  **app container** exited 1 during `build()`:

  ```
  openai_agents.py:158  _connect_with_retry → MCP session.initialize()
  rossoctl_turnspan.py:178 _send_request
  mcp/shared/session.py:292 send_request → response_stream_reader.receive()
  asyncio.exceptions.CancelledError: Cancelled via cancel scope …
  ```

  The agent's cold-start MCP `initialize()` to a tool on :8000 is redirected
  through the transparent proxy (`OUTBOUND_PORTS_INCLUDE=8080,8000`, redirect →
  :8082). The `initialize()` request never gets its response back within the
  MCP client's session timeout, so `_connect_with_retry` exhausts its attempts
  and `build()` crashes. `instrument` correctly halted (fail-loud) at
  `payment-agent` and did NOT proceed; every partial attach was backed out and
  the namespace restored to fully bare.

- The demo was therefore **never run** and no trace was produced — the gate
  fails at the *instrument* step, upstream of trace inspection.

### Suspected mechanism (narrowed — code-level investigation)

MCP Streamable HTTP opens a long-lived server→client SSE GET channel plus POST
requests; `initialize()`'s reply returns as a `text/event-stream` response. The
cortex forward-proxy response phase (`authlib/listener/forwardproxy/server.go`,
`Handler()` response branch ~L460–517) chooses between two SSE relay paths on the
`Content-Type: text/event-stream` response:

- **byte-for-byte `streamPassthrough`** (L494–516) — taken only when the pipeline
  has **no** `StreamingResponder`. Its comment explicitly says re-framing "would
  drop the event:/id:/retry: lines that generic SSE clients (e.g. an MCP
  Streamable HTTP client) depend on. Fixes #642" — i.e. THIS is the path an MCP
  client needs.
- **`handleStreamingResponse` re-framing** (L488–492, via `sseframe.NewReader`) —
  taken when the pipeline **has** `StreamingResponders`.

The dg.sh-generated proxy config wires `a2a-parser`, `mcp-parser`,
`inference-parser` on both inbound and outbound — and those parsers **are**
`StreamingResponders`. So the payment-agent's MCP traffic takes the **re-framing**
path, not the byte-for-byte passthrough the #642 fix added for MCP clients. The
re-framer does flush per frame, so the exact failure (does re-framing corrupt the
`initialize()` handshake, or is it a timeout/idle-reader interaction on the
long-lived channel?) still needs a cortex-side packet-level repro — but the
suspect is now specific: **the parser-carrying (StreamingResponder) pipeline
routes MCP SSE onto the re-framing path rather than the #642 passthrough.** That
is a cortex proxy/pipeline concern, not a dg.sh or kit concern.

Whether this is a **tunable timeout** or a **fundamental re-framing
incompatibility** is the deciding question between:

- **proxy entry-hop / streaming fix** — make the proxy correctly relay MCP
  Streamable-HTTP so an outbound-initiating agent's cold-start `initialize()`
  completes; then re-run the gate; or
- **envoy-default fallback** — for agents that initiate MCP, prefer the envoy
  path (the earlier one-trace measurement referenced by #239 was on envoy) and
  reserve the proxy path for the inbound-only tools / A2A hops it handles cleanly.
  NOT re-verified this session: exercising the envoy branch needs the platform
  `envoy-config` ConfigMap copied into the ad-hoc `travel-advisor` namespace (it
  is rendered only into chart-managed team namespaces), which this run could not
  do. Re-running the gate on the envoy path (copy `envoy-config` in →
  `instrument` takes the envoy branch → demo → inspect) is the recommended next
  measurement to confirm the fallback holds under the v1.7.0/turn-span image.

## Reproduction (short form)

```sh
# 0. host + kube-proxy (once): raise net.core.wmem_default/max to 16MiB; restart kube-proxy
# 1. redeploy DG, wipe span DB, reset cursor, restart interactions+classification (CLAUDE.md §1–2)
# 2. delete + bare-redeploy travel_advisor (APP=travel_advisor bash deploy.sh); confirm sidecar=none, 2 peers
# 3. build the v1.7.0 proxy sidecar image from cortex lane/lineage-namespace @ 3fda5ce5:
cd cortex/authbridge
podman build -f cmd/authbridge-proxy/Dockerfile -t docker.io/library/authbridge-proxy:lineage-v170 .
podman build -f proxy-init/Dockerfile.init -t docker.io/library/proxy-init:lineage-v170 proxy-init/
kind load docker-image docker.io/library/authbridge-proxy:lineage-v170 --name rossoctl
kind load docker-image docker.io/library/proxy-init:lineage-v170  --name rossoctl
# 4. instrument (proxy path):
cd data-governance
export SIDECAR_IMAGE=docker.io/library/authbridge-proxy:lineage-v170 \
       PROXY_INIT_IMAGE=docker.io/library/proxy-init:lineage-v170 \
       CONTAINER_TOOL=podman KIND_CLUSTER_NAME=rossoctl
bash deploy/dg.sh namespace travel-advisor instrument
# → tools attach 2/2; payment-agent (openai_agents) crash-loops on cold-start MCP initialize()
```

## Verdict

The ADR-0033 proxy one-trace gate is **NOT passed**. The proxy sidecar breaks the
cold-start outbound-MCP connect for `openai_agents` agents, so the demo cannot
run on an all-proxy instrumentation. Per ADR-0033 the design bends — a proxy
MCP-streaming fix or an envoy-default fallback — before epic #239 ships. This
run also surfaced two blocking prerequisites that must be addressed regardless:
the host netlink-buffer requirement and the v1.7.0 sidecar-image source (with the
RECIPE.md doc gap pointing at the envoy image for the proxy path).
