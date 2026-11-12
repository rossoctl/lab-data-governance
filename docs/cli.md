# `dg.sh` — Data Governance cluster management

## Purpose

`deploy/dg.sh` is the supported operator entry point for installing the Data
Governance component and activating lineage on Rossoctl-managed workloads.

```text
dg.sh                                              # component status
dg.sh component  [install|uninstall|status]
dg.sh component  install [--no-build|--reuse-classification]
dg.sh namespaces [list]
dg.sh namespace  <ns> [instrument|status] [<entity>]
dg.sh namespace  <ns> instrument --enforce|--observe [--pass-host <glob>]... [--wait-ms <n>] [--fallback deny|allow] [--allow-open-default]
dg.sh namespace  <ns> fail-mode set <file>|show|validate <file>
dg.sh namespace  <ns> gate-report [--since <time>]
```

There are no global options. The shim and existing-proxy reconciliation assets
are vendored in `deploy/lineage-attach/`; no Cortex checkout is required.

## Component lifecycle

`dg.sh component install` builds and loads the images, applies the Kubernetes
resources, wires the platform collector tee, and rolls the component workloads.
Use `--no-build` only when the required images are already loaded. Use
`--reuse-classification` when the classification image is in the local container
cache: the script builds and loads the shared receiver/UI image, then loads the
cached classification image into Kind without rebuilding its model or fetching
git-LFS weights. The verdict service runs from that image too: the script refuses
an image built before the service exists, and an older one runs older verdict code,
so rebuild it after changing `data_governance/risk/api/`. The options cannot be
combined.

`dg.sh component uninstall` reverts the collector tee and removes the component.
It deletes the namespace and PVC by default; `--keep-data` preserves the PVC.

`dg.sh component status` is read-only and reports workloads, the receiver
Service, the UI route, and collector-tee state.

## Namespace discovery

`dg.sh namespaces list` lists non-system namespaces enabled for Rossoctl.

## Lineage activation

```sh
./deploy/dg.sh namespace <ns> instrument
./deploy/dg.sh namespace <ns> instrument <entity>
```

The optional entity is selected by `app.kubernetes.io/name`. Without it,
instrumentation selects every agent/tool with a trusted
`rossoctl.io/type=agent|tool` label in the namespace. A namespace with no
trusted agents/tools has nothing to instrument. An explicitly named untrusted
entity fails loudly, with guidance to deploy or import it through Rossoctl.
For namespace-wide activation, the command validates every target first and
then processes tools before agents, alphabetically within each group.

Only the real Rossoctl deployment shape is supported. Every selected entity
must have:

- a trusted `rossoctl.io/type=agent|tool` label;
- a Running pod with an admitted `authbridge-proxy` sidecar;
- the platform `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` contract;
- a lineage-capable AuthBridge plugin catalog;
- a mounted pipeline ConfigMap; and
- exactly one application container whose two-shim image can be attested.

The namespace `authbridge-runtime-config` ConfigMap must exist and be within the
safe reconciliation envelope, no selected entity's AgentRuntime may set
`pluginPreset`, and a run that writes that ConfigMap must preflight every
AgentRuntime target in the namespace.

All selected entities and images are preflighted before the first mutation.
A trusted workload with an Envoy or incomplete proxy fails before mutation.
Bare, legacy component-labelled
workloads are ignored by namespace-wide instrumentation; naming one explicitly
fails before mutation. Data Governance never injects or replaces a sidecar.

Rossoctl admission rebuilds each sidecar's `authbridge-config-<name>` from the
namespace `authbridge-runtime-config` on every pod admission (roll, pod delete,
scale-up), so `instrument` converges the parser and `lineage-telemetry` entries
there, preserving the authentication plugins, and never writes the per-workload
ConfigMap. The lineage entry names no `self_id`; each sidecar reports the last
segment of its admitted client id. Because that write reaches every agent/tool in
the namespace at its next admission, only a namespace-wide run makes it;
`instrument <entity>` requires a namespace that already carries the pipeline.
After preflight, `instrument` rolls each application onto the attested image
with `LINEAGE_PROPAGATE=1` first, because Rossoctl may roll every workload in the
namespace when that ConfigMap changes. It then writes the namespace ConfigMap
when needed, rolls each workload whose pod does not yet run the pipeline, and
waits until the newly admitted sidecar reports it. A re-run on a converged
namespace rolls nothing.

The pipeline then reaches every sidecar admitted in the namespace, including
agents/tools deployed later; `status` reports a later proxy-sidecar workload
`live=no` until `instrument` rolls it onto the shim. Anything that rewrites `authbridge-runtime-config`,
such as a platform `helm upgrade` in a chart-managed namespace, removes it at the
next admission; `status` reports that too.

Activation is non-reversible in v1. Redeploying a workload through Rossoctl
restores its application image, but its sidecar keeps the namespace pipeline;
removing that means editing `authbridge-runtime-config`, which reaches every
agent/tool in the namespace. See
[ADR-0031](adr/0031-non-reversible-namespace-lineage-activation.md) and
[ADR-0033](adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md).

## Namespace status

```sh
./deploy/dg.sh namespace <ns> status
./deploy/dg.sh namespace <ns> status <entity>
```

Status is read-only and includes both trusted and legacy component-labelled
agents/tools, including when both occur in the same namespace. It reports
sidecar presence/type and whether `lineage-telemetry` is wired. On trusted
proxy workloads, it also reports whether the application shim, proxy
environment, namespace `authbridge-runtime-config`, and live pipeline have
converged; a namespace ConfigMap without the pipeline is reported even while
running pods still carry it, because the next pod admission would drop it. It
may truthfully report `sidecar=none`; that state is diagnostic and is not
accepted by `instrument`.

## The Sentry gate

```sh
./deploy/dg.sh namespace <ns> instrument --enforce [--pass-host <glob>]... [--wait-ms <n>] [--fallback deny|allow] [--allow-open-default]
./deploy/dg.sh namespace <ns> instrument --observe [--pass-host <glob>]... [--wait-ms <n>] [--fallback deny|allow] [--allow-open-default]
```

`instrument --enforce` does what `instrument` does and adds the Sentry gate to the namespace's
`authbridge-runtime-config`, the source every pod admission renders the sidecar's config from. On both
chains `lineage-telemetry` gets `capture_io: true` (the gate can only judge what lineage captured) and
`export_delay_ms: 10` (the verdict service answers once the request span has left the sidecar). The
outbound chain gets a `sentry-gate` entry after `lineage-telemetry` (the gate requires lineage before it)
with these keys:

| key | value |
|---|---|
| `verdict_url` | the verdict service, `http://data-governance-verdict.data-governance.svc:8080/enforce/v1` (`DG_VERDICT_URL` overrides it) |
| `verdict_secret` | the shared secret, read from Secret `data-governance/dg-verdict-secret` and written inline; `/v1/pipeline` masks it |
| `pass_hosts` | the `--pass-host` globs, repeatable (`path.Match` syntax, port stripped and case-folded; a glob the gate would refuse, such as an unclosed `[`, is a usage error); left out when there are none |
| `wait_ms` | `--wait-ms <n>`, 1..3000: how long the gate holds a request for a verdict; left out without `--wait-ms`, which is the gate's default, 2000 |
| `fallback` | `--fallback deny` or `allow`: what a request gets when a slow or dead verdict service gives no verdict (`deny` refuses it with a 503, `allow` delivers it unjudged); left out without `--fallback`, which is the gate's default, `deny` |
| `fail_mode` | the destination rows of the namespace's fail-mode document ([below](#the-fail-mode-document)), each with only the keys it has; left out when there is no document or it has none |
| `sources` | one slice per source workload the document names: its defaults and its rows ([below](#the-fail-mode-document)); left out when no row names a source |
| `fail_mode_revision` | the [revision](#the-revision) of the namespace's fail-mode document, which the gate writes into the record of every request that got no verdict; left out when there is no document |

Every other gate key keeps the gate's default (`deny_on` `[block]`, `pass_single_label`
true, so a cluster short name always passes). An existing `sentry-gate` entry anywhere in the source is
replaced by this one. `--pass-host` replaces the pass list; without it a re-run keeps the list the
namespace's gate carries, so emptying it means removing `pass_hosts` from the gate's entry in
`authbridge-runtime-config` by hand. `--wait-ms` and `--fallback` likewise replace the gate's wait and
fallback, and a re-run without them keeps the ones the namespace's gate carries (each says so on stderr).

`--fallback allow` is refused, mutating nothing, unless `--allow-open-default` is given too: while the
verdict service is slow or down, every request the pass list does not name leaves unjudged. A re-run that
keeps an `allow` already in the gate does not ask for the flag again. A fail-mode row with `fallback: allow`
needs the flag the same way, on every run while the document holds that row.

Then each workload's live `/v1/pipeline` is checked against the gated pipeline (lineage before the gate,
the lineage values above, the gate's URL, pass list, wait, fallback, `fail_mode` rows (as a set by destination),
`sources` slices (by source, each slice's rows as a set by destination; a slice `/v1/pipeline` masks, for a source
named like a secret's key such as `chaos-monkey`, by its presence), `fail_mode_revision` and mode, a secret present; a
`wait_ms` absent counts as 2000, a `fallback` absent as `deny`), and each one not on it
is rolled and verified. A second run with the same flags writes nothing and rolls nothing.

`--observe` writes the same with the gate's entry in the pipeline's own observe mode (`on_error:
observe`): the gate asks and waits as when enforcing, and every refusal it would make is recorded and not
made. `--enforce` on an observing namespace switches it back.

It refuses, mutating nothing, when a sidecar's image has no `sentry-gate` that takes `verdict_url`,
`verdict_secret`, `pass_hosts`, `fallback`, `fail_mode`, `sources` and `fail_mode_revision` (read from the image's `/v1/plugins`), when the
namespace's fail-mode document is not valid (it names `fail-mode validate`), when the verdict Secret is empty,
and when an `<entity>` is named: the gate is a namespace setting. `instrument` without `--enforce` or
`--observe` refuses on a namespace whose source carries the gate, mutating nothing, and names the run that
keeps it: the lineage reconciler appends lineage after every entry it does not manage, so it would put the
gate before lineage. No verb removes the gate.

`status` on a gated namespace prints the gate's mode, pass list, wait, fallback, the number of its
`fail_mode` rows and of its `sources` slices, and the first 12 characters of its revision (`namespace <ns>:
sentry-gate mode=enforce pass_hosts=a.example,b.example wait_ms=2000 fallback=deny fail_mode_rows=2 sources=1
revision=9f86d081884c`, `2000` and `deny` too when the entry has none, `revision=none` when it has no revision)
and checks the source and each live pipeline against the gated pipeline in that mode, with that pass list,
wait and fallback and the rows and slices of the namespace's fail-mode document; when the source has drifted, also after a `fail-mode set` not yet applied, it
advises the run with the namespace's own `--enforce` or `--observe`.

The secret is copied, not referenced: whoever can read ConfigMaps in a gated namespace can read it, and
after `component uninstall` and `install` draw a new one, every gated namespace refuses with
`verdict_unauthorized` until its `instrument --enforce` (or `--observe`) runs again. The secret reaches
no command line and no log of `dg.sh`.

### The fail-mode document

```sh
./deploy/dg.sh namespace <ns> fail-mode validate <file>
./deploy/dg.sh namespace <ns> fail-mode set <file>
./deploy/dg.sh namespace <ns> fail-mode show
```

The fail-mode document overrides the gate's wait and fallback for some requests: those to a destination
host, those from a source workload, or those from a workload to a host. It is JSON, kept verbatim as key
`table` of ConfigMap `dg-fail-mode` in the app namespace:

```json
{"rows": [
  {"source": "desk", "wait_ms": 100},
  {"destination": "partner.example", "wait_ms": 50, "fallback": "deny"},
  {"source": "desk", "destination": "partner.example", "fallback": "allow"},
  {"destination": "slow-tool.example", "wait_ms": 3000}
]}
```

| key | value |
|---|---|
| `source` | a workload: not empty, no whitespace, no `/`. It is the workload name lineage reports as `self_id` for the requests the workload sends, compared exactly |
| `destination` | a host: not empty, no port, no scheme, no `/`, no whitespace, no wildcard (`*`, `?`, `[`). The gate takes a row when the request's host, port stripped, case-folded and without a trailing dot, equals it folded the same way; nothing else matches (no glob, no suffix) |
| `wait_ms` | 1..3000: how long the gate holds a request for a verdict, instead of its default |
| `fallback` | `deny` or `allow`: what a request gets when the wait ends with no verdict, instead of the gate's fallback |

A row names a source, a destination or both, and gives `wait_ms` or `fallback` or both, and no other key.
So there are three kinds of row: a destination row covers every request to that host, a source row every
request from that workload, a pair row (both named) the requests from that workload to that host. Each
(source, destination) appears once in a document. Per key, the most specific row wins: pair > destination >
source > the gate's own value, so a destination row (the host's risk) holds over a source row (the
workload's tolerance), and only a pair row overrides it for one workload. What no row gives keeps the
gate's value. A workload no row names needs nothing: its requests take the destination rows and the gate's
values, including a workload added after the document was set.

`instrument` renders the destination rows as the gate's `fail_mode`, and per source a slice under
`sources`: its defaults are the source row's keys, its rows, for each destination with a destination row or
a pair row of that source, the pair's keys over the destination row's. The gate fills a slice row's missing
key from the slice's defaults, and those from its own keys. The document above renders to:

```yaml
fail_mode:
- destination: partner.example
  wait_ms: 50
  fallback: deny
- destination: slow-tool.example
  wait_ms: 3000
sources:
  desk:
    wait_ms: 100
    fail_mode:
    - destination: partner.example
      wait_ms: 50
      fallback: allow
    - destination: slow-tool.example
      wait_ms: 3000
```

so a request from `desk` to `partner.example` waits 50 ms and is delivered on no verdict, from `desk` to
`slow-tool.example` waits 3000 ms, from `desk` anywhere else waits 100 ms, and from any other workload to
`partner.example` waits 50 ms and is refused.

`fail-mode validate <file>` checks a file against these rules, one line per problem naming the rule, and
never touches the cluster. `fail-mode set <file>` validates the file, and only a valid one is written
to `dg-fail-mode`. `fail-mode show` prints the namespace's document, or says there is none.

The gates take a document at the next `instrument --enforce` (or `--observe`), which renders its rows and
slices into the gate's entry; that run, not `set`, rolls the pods. Deleting `dg-fail-mode` with `kubectl` and re-running `instrument`
removes the rows and slices.

#### The revision

A document's revision is the sha256 of its bytes, as `set` keeps them (nothing normalised: a changed
line ending or trailing space is another revision), in lowercase hex. `fail-mode set` prints it, `fail-mode
show` prints it on stderr, and `python3 deploy/fail_mode.py revision <file>` gives it for a file before it is
set. `instrument` writes it into the gate's entry as `fail_mode_revision`, after `sources`, and the gate
writes it into the record of every request that got no verdict (a fallback, a marked body, a fault), so a
record read after the document changed still names the document that decided it. A pod on another revision
is rolled like a pod on other rows, and a document set but not yet instrumented shows in `status` as drift.

## The gate's report

```sh
./deploy/dg.sh namespace <ns> gate-report [--since <time>]
```

`gate-report` reads, read-only, every sidecar's session API (`kubectl exec <pod> -c authbridge-proxy --
wget 127.0.0.1:9094/v1/sessions`, paging back to the window's start) and counts what the namespace's gates
did with the requests that got no verdict, per source workload and destination:

| column | records |
|---|---|
| `refused` | `fallback_deny`, and the faults refused whatever the fallback (`verdict_unauthorized`, `verdict_error`, `verdict_empty`, `no_lineage_stamp`, `no_exchange_key`) |
| `fallback_allow` | delivered unjudged on the fallback |
| `would_refuse` | the refusals of either kind, and of a marked body, recorded under `--observe` (the framework marks them shadow): what `--enforce` would have refused |
| `body_cut`, `body_not_captured` | a body lineage marked, refused whatever the fallback, by its mark |

The source is the `self_id` lineage wrote into its record of the same request, the name a fail-mode row's
`source` matches (the gate's record names none), else the workload whose sidecar the record was read from; the
destination is the request's host, port stripped, case-folded and without a trailing dot, as the gate matches a row. Below the table
is every record by reason (a marked body under its cause and its mark, `shadow` for an observed one), with the setting
that decided it (for a fallback, the fail-mode row the gate names or `default`, the namespace's own; `-` for a fault
or a marked body, which no setting decides) and the revision it ran with; then the
revisions the records name, beside the namespace's current one, also on the header line. A refusal by a
verdict is not counted here; it is the verdict service's.

`--since` takes an RFC 3339 time or a span back from now (`90m`, `1h`, `30s`); without it the window is
everything the sidecars hold. A sidecar keeps its records in its process, so a roll empties them: read a
window before the roll that ends it, `instrument` included. It also keeps only its most recently active sessions
(100 by default), so the records of an older session are gone even inside the window. A sample, with every kind of record in it:

```text
gate-report namespace=desk since=all held until=2026-11-04T13:00:00Z mode=enforce revision=9f86d081884c
sidecar booking pod=booking-7f9c-abc12 read=yes events=40 no_verdict_records=0
sidecar desk pod=desk-5d4b-xk2p9 read=yes events=212 no_verdict_records=7
source  destination        refused  fallback_allow  would_refuse  body_cut  body_not_captured
desk    partner.example    1        1               1             1         0
desk    slow-tool.example  1        0               1             0         1
by reason:
  desk  partner.example    fallback_allow                   default                            1a2b3c4d5e6f  1
  desk  partner.example    fallback_deny shadow             row destination=partner.example    1a2b3c4d5e6f  1
  desk  partner.example    no_lineage_stamp                 -                                  9f86d081884c  1
  desk  partner.example    verdict_error body_cut           -                                  9f86d081884c  1
  desk  slow-tool.example  fallback_deny                    row destination=slow-tool.example  9f86d081884c  1
  desk  slow-tool.example  verdict_busy body_not_captured   -                                  9f86d081884c  1
  desk  slow-tool.example  verdict_pending body_cut shadow  -                                  9f86d081884c  1
revisions seen: 1a2b3c4d5e6f 2, 9f86d081884c 5 (current 9f86d081884c)
```

Each Running pod of a workload is read, so a workload with replicas has a `sidecar` line per pod and its counts
are their sum. A workload with no Running pod, or a pod whose session API cannot be read, is named `read=no` with the reason and the
rest are still read; when no sidecar can be read, the report says so and fails. A namespace with no gate in its
source is refused.

## Typical flow

1. Install Rossoctl and Data Governance.
2. Deploy/import agents and tools through the Rossoctl UI or CLI.
3. Run `./deploy/dg.sh namespace <ns> instrument`.
4. Run the workload.
5. Verify with `./deploy/dg.sh namespace <ns> status` and the Data Governance UI.

Stores and external demo clients are not agents/tools and can remain outside
the sidecar-managed graph.
