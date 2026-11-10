# Sentry enforcement — experiment runbook

Put the Sentry gate in front of a running app, watch a verdict refuse what the policy blocks and let the rest through,
take the verdict service away and see what the fail mode does, shape it with a fail-mode table, and try it in observe
mode first. Every command, in order, with what it prints. A person or an agent follows it top to bottom.

Conventions: `$` lines are typed; the `→` line after one is what to expect. Every `→` line is a real output from the run
of 2026-11-08 on a kind cluster (lab-data-governance `2c29889` = PRs #344 + #345 + #352, cortex fork `sentry` with s-and-p-team/cortex#187
`610fa556`), except the `gate-report` lines of §§ 5–6 and § 8's done line, which are from the 2026-11-10 run below; ids, ms
and revisions are that run's. One cluster, one terminal. The whole of §§ 2–7 is also one command,
`run-all.sh` (§ 8), which checks every step against the outcome written here. It was run again from this folder on
2026-11-09 with a gate image built by § 0's `gate-image.sh` from the fork's `sentry` at `6820b399`: 16 PASS, 0 FAIL; and on
2026-11-10, with every dg.sh step also checked on its exit status: 28 PASS, 0 FAIL.

What the pieces are, in one paragraph: the app's sidecars carry two plugins on the outbound chain, `lineage-telemetry`
(records the request and stamps it) and `sentry-gate` after it. The gate holds each outbound request and asks the
data-governance **verdict service** for that exchange's verdict, which DG computes from the payload's labels and the
policy (OPA). `allow` → the request leaves; `block` → refused, with a reply that says what was flagged. No verdict in
`wait_ms` → the **fallback** decides (`deny` by default). All of it is configured by one command, `dg.sh namespace <ns>
instrument --enforce`, which writes the gate into the namespace's `authbridge-runtime-config` and rolls the sidecars.

## 0. What you need

Everything comes from GitHub; nothing else is assumed. Four clones side by side, and podman, kind, kubectl, git, python3:

```sh
git clone https://github.com/rossoctl/rossoctl.git                            # the platform (main)
git clone -b enforcement https://github.com/rossoctl/lab-data-governance.git  # data governance + this runbook (deploy/enforcement-runbook/)
git clone -b sentry https://github.com/s-and-p-team/cortex.git                # the sidecar with the Sentry gate
git clone https://github.com/s-and-p-team/agent-examples-snp.git              # the app, apps/pharmacy_refills (main; a private repository)
```

Then, in this order (the gate image must be set before the app's pods are created: `instrument --enforce` refuses a
sidecar whose image has no gate, and only a pod created after step 3 gets the new image):

| # | need | how | check | pass |
|---|---|---|---|---|
| 1 | a kind cluster with the platform | rossoctl `scripts/kind/setup-rossoctl.sh` | `kubectl config current-context` | your cluster's context (`kind-rossoctl` by default) |
| 2 | data governance from the `enforcement` branch | lab-data-governance `docs/LIVE-E2E.md` § 0's checks and § 2 (`deploy/build-and-load.sh`, `kubectl apply -f deploy/k8s/`, the verdict secret, `deploy/create-opa-configmap.sh`, restart) | `kubectl -n data-governance get deploy data-governance-verdict` | `2/2` |
| | the shipped policy | same step | `kubectl -n data-governance get cm opa-policy -o jsonpath='{.data.rules_source\.json}' \| python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'` | `1.0.0` |
| 3 | a sidecar image that carries the gate | `deploy/enforcement-runbook/gate-image.sh <the cortex clone>`: builds `cmd/cortex/Dockerfile` with the `full` profile's build tags (the platform's default `envoy` profile has no gate), loads it into kind, sets the platform's `images.authbridge`; its first line is the undo | `kubectl -n rossoctl-system get cm rossoctl-platform-config -o jsonpath='{.data.config\.yaml}' \| grep '^  authbridge:'` | `localhost/authbridge:gate-<the clone's commit>` |
| 4 | the app, `pharmacy_refills`, with lineage on | agent-examples-snp `apps/pharmacy_refills/RUNBOOK.md` §§ 1–3 (`deploy.sh --app pharmacy_refills --mode proxy`, then `deploy/dg.sh namespace pharmacy instrument` from the lab-data-governance clone) | `kubectl -n pharmacy get pods --no-headers \| grep -c '2/2'` | `13` |
| | the app's sidecars on the gate image | step 3 before step 4 | `kubectl -n pharmacy get pod -l app.kubernetes.io/name=report-dispense -o jsonpath='{.items[0].spec.containers[?(@.name=="authbridge-proxy")].image}'` | the image of step 3 (here `localhost/authbridge:gate-610fa556`) |

`env.sh` finds the lab-data-governance checkout itself (this folder's repository) and writes every run's outputs
outside it, under `$TMPDIR/enforcement-runbook/<time>`, one directory for the terminal that sources it (`RUN=` set before sourcing overrides it) and
one of its own for every `run-all.sh`, named on its first line. Export `KUBECONFIG`, `EXPECT_CONTEXT`
(default `kind-rossoctl`) or `NS` (default `pharmacy`) first if yours differ, then:

```sh
$ cd lab-data-governance/deploy/enforcement-runbook
$ source env.sh                         # refuses if kubectl is on another cluster
$ ./pipeline.sh report-dispense         # one sidecar's live pipeline, as it echoes it
```

Before the gate, the outbound chain ends in `lineage-telemetry` and `capture_io` may be `False` (the instrument step's
privacy default); § 2 turns it on, because the verdict is computed from the body. On a namespace that is already gated
the line ends in `sentry-gate`, as in § 2's output; § 2 is then idempotent.

**The cast.** Callers: two of the app's MCP tools, `report-dispense` and `schedule-delivery`; `call.sh` runs `call.py`
inside the tool's own container, so the request goes through that tool's sidecar exactly as the tool's own calls do.
Destinations: the app's three outside vendors, `demand-forecast.example:8000` (V), `copay-assist.example:8000` (H3),
`sms-gateway.example:8000` (H2). They answer `{"received": true}` and log nothing; "delivered" means that answer came
back. Payloads (`call.py`, new values every call): `clean` = a drug and two dates; `pii` = the same plus a patient name,
birth date and id; `big` = clean plus a 6 KB note (beyond lineage's 4 KB capture, so the body is cut); `plain` = a
non-MCP JSON POST; `connect` = an HTTPS request (a CONNECT tunnel the gate cannot read).

## 1. Preflight (read-only)

```sh
$ kubectl -n data-governance get pods --no-headers | grep -vc Running
→ 0
$ curl -s -o /dev/null -w '%{http_code}\n' http://dg.localtest.me:8080/risk/rules        # the DG API from the host
→ 200
$ for h in demand-forecast copay-assist sms-gateway; do kubectl -n pharmacy get pod -l app.kubernetes.io/name=$h -o jsonpath="$h {.items[0].spec.hostname}.{.items[0].spec.subdomain}{'\n'}"; done
→ demand-forecast demand-forecast.example
→ copay-assist copay-assist.example
→ sms-gateway .
```

The last line matters: a vendor answers on its `.example` name only when its pod is a hostname under the headless
Service `example`. `sms-gateway` is not (the app manifest gives it a plain Service), so a request that the gate *lets
through* to `sms-gateway.example` ends in a proxy **502**. That does not affect this runbook's steps, which only refuse
requests to it; it explains a 502 if you send one there yourself.

## 2. The gate on

```sh
$ DG namespace pharmacy instrument --enforce --pass-host host.containers.internal --fallback deny
→ >> instrument --enforce: fail_mode rows from pharmacy/dg-fail-mode: 0, sources: 0, revision: none
→ >> instrument --enforce: verdict_url http://data-governance-verdict.data-governance.svc:8080/enforce/v1
…  (it checks each workload's live pipeline and rolls those not on it: 2–4 min the first time)
$ settle                                 # waits until every pharmacy deployment has rolled
$ DG namespace pharmacy status | grep -m1 sentry-gate
→ namespace pharmacy: sentry-gate mode=enforce pass_hosts=host.containers.internal wait_ms=2000 fallback=deny fail_mode_rows=0 sources=0 revision=none
$ ./pipeline.sh report-dispense
→ report-dispense p9xh5 gate-610fa556 out=token-exchange>a2a-parser>mcp-parser>inference-parser>lineage-telemetry>sentry-gate capture_io=True/True delay=10/10 mode=enforce secret=[REDACTED] gate={"pass_hosts":["host.containers.internal"],"verdict_url":"http://data-governance-verdict.data-governance.svc:8080/enforce/v1","wait_ms":2000}
```

What changed: `sentry-gate` is last on the outbound chain, `capture_io` is on, lineage's `export_delay_ms` is 10. The
pass list names hosts the gate never asks about (here the model endpoint on the host; add your LLM's host). Run the
same command again: it writes nothing and rolls nothing. Say `--fallback deny` here: `instrument` **keeps** the
namespace's current fallback and pass list unless they are given, so an earlier `--fallback allow` would otherwise
survive into this run.

## 3. A verdict decides

```sh
$ ./call.sh 3.1 report-dispense clean demand-forecast.example:8000
→ 3.1       report-dispense->demand-forecast.example:8000 clean: wire=200 delivered=True | gate allow/verdict_allow {'waited_ms': '242'}
$ ./call.sh 3.2 report-dispense pii demand-forecast.example:8000
→ 3.2       report-dispense->demand-forecast.example:8000 pii: wire=200 delivered=False | gate deny/verdict_block {'waited_ms': '167', 'rules': 'DG-001,DG-004'}
```

The refused call's reply is what the agent's model reads (an MCP `tools/call` refusal is a 200 JSON-RPC result with
`isError`, so clients hand it on):

```sh
$ tail -1 $RUN/rows.jsonl | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["text"][:330])'
→ {"id":"38778","jsonrpc":"2.0","result":{"content":[{"text":"Sentry gate (data governance) refused this request to demand-forecast.example. Nothing was delivered.\nFlagged parts are marked [category]:\n{\"date_of_birth\": \"[person.dates]\", \"drug\": \"[medical.terms] [medical.data]\", \"external_id\": \"[person.operationalID]\", ...
```

Where the decision is recorded: the gate's own record is on the caller's sidecar (`call.py` reads it from the session
API, `127.0.0.1:9094/v1/sessions`, and puts it in `rows.jsonl` under `records`): action, reason, the exchange id, the
labels and the rules that decided. DG's view of the same exchange is in its UI, `http://dg.localtest.me:8080/ui/traces`
(the newest trace whose caller is `report-dispense`).

```sh
$ ./call.sh 3.3 report-dispense connect demand-forecast.example:8000
→ 3.3       report-dispense->demand-forecast.example:8000 connect: wire=None delivered=False | gate allow/not_inspected {'kind': 'tunnel'}
```

An HTTPS tunnel cannot be read, so the gate records `not_inspected` and lets the tunnel open (here the TLS handshake then
fails, because the vendor speaks plain HTTP). This is a known limit: an encrypted destination is not judged.

## 4. No verdict: the fallback

Take the verdict service away:

```sh
$ verdict 0
$ ./call.sh 4.1 report-dispense clean demand-forecast.example:8000
→ 4.1       report-dispense->demand-forecast.example:8000 clean: wire=200 delivered=False | gate deny/fallback_deny {'no_verdict': 'verdict_error', 'error_kind': 'transport', 'waited_ms': '1'}
$ ./call.sh 4.2 report-dispense plain demand-forecast.example:8000
→ 4.2       report-dispense->demand-forecast.example:8000 plain: wire=503 delivered=False | gate deny/fallback_deny {'no_verdict': 'verdict_error', 'error_kind': 'transport', 'waited_ms': '1'}
```

Fail closed: even a harmless payload is refused, and the reply says no verdict arrived ("No verdict arrived, so nothing
was judged and nothing is flagged."). A connection refused costs ~1 ms; a slow service costs up to `wait_ms`.

Fail open is an explicit opt-in, refused without its flag:

```sh
$ DG namespace pharmacy instrument --enforce --fallback allow
→ error: instrument --enforce: --fallback allow lets every request the pass list does not name leave unjudged while the verdict service is slow or down; pass --allow-open-default to accept that. Mutating nothing.
$ DG namespace pharmacy instrument --enforce --fallback allow --allow-open-default; settle
$ ./call.sh 4.5 report-dispense clean demand-forecast.example:8000
→ 4.5       report-dispense->demand-forecast.example:8000 clean: wire=200 delivered=True | gate allow/fallback_allow {'no_verdict': 'verdict_error', 'error_kind': 'transport', 'waited_ms': '1'}
$ ./call.sh 4.6 report-dispense big demand-forecast.example:8000
→ 4.6       report-dispense->demand-forecast.example:8000 big: wire=200 delivered=False | gate deny/verdict_error {'error_kind': 'transport', 'waited_ms': '1', 'body_cut': 'true'}
```

The last line is the guarantee to remember: **a body lineage could not capture whole is never delivered on the
fallback**, whatever the fallback says, because nothing judged it.

## 5. The fail-mode table

One fallback for every destination is coarse. A fail-mode document sets `wait_ms` and `fallback` per destination host,
per source workload, or per pair; `docs/doc-3a.json` and `docs/doc-3b.json` are the two used here.

```sh
$ cat docs/doc-3a.json
→ {"rows": [{"destination": "demand-forecast.example", "fallback": "allow"}]}
$ DG namespace pharmacy fail-mode validate docs/bad-open.json; echo rc=$?          # a typo is refused before anything is written
→ … rc=1
$ DG namespace pharmacy fail-mode set docs/doc-3a.json
→ >> fail-mode set: pharmacy/dg-fail-mode written, revision 0f23b7b2e399…; the gates take it at the next 'dg.sh namespace pharmacy instrument'
$ DG namespace pharmacy instrument --enforce --fallback deny --allow-open-default; settle
$ DG namespace pharmacy status | grep -m1 sentry-gate
→ namespace pharmacy: sentry-gate mode=enforce pass_hosts=host.containers.internal wait_ms=2000 fallback=deny fail_mode_rows=1 sources=0 revision=0f23b7b2e399
$ ./call.sh 5.3 report-dispense clean demand-forecast.example:8000      # the service is still down
→ 5.3       … gate allow/fallback_allow {'no_verdict': 'verdict_error', …, 'fail_mode_destination': 'demand-forecast.example', 'fail_mode_revision': '0f23b7b2e399'}
$ ./call.sh 5.4 report-dispense clean copay-assist.example:8000
→ 5.4       … delivered=False | gate deny/fallback_deny {…, 'fail_mode_revision': '0f23b7b2e399'}
```

Every request the fallback decided names the row (`fail_mode_destination`) and the document's revision, so a refusal
can be traced to the exact table in force. Rows by source and by pair (`doc-3b.json`: `report-dispense` gets `allow`,
except toward `sms-gateway.example`):

```sh
$ DG namespace pharmacy fail-mode set docs/doc-3b.json
$ DG namespace pharmacy instrument --enforce --allow-open-default; settle
$ ./call.sh 5.7 report-dispense clean demand-forecast.example:8000     → allow/fallback_allow, fail_mode_source=report-dispense
$ ./call.sh 5.8 schedule-delivery clean demand-forecast.example:8000   → deny/fallback_deny  (no row names this source)
$ ./call.sh 5.9 report-dispense clean sms-gateway.example:8000         → deny/fallback_deny, fail_mode_source + fail_mode_destination (the pair row)
$ ./call.sh 5.10 report-dispense clean copay-assist.example:8000       → allow/fallback_allow, fail_mode_source=report-dispense
$ DG namespace pharmacy gate-report
→ gate-report namespace=pharmacy since=all held until=2026-11-10T16:49:16Z mode=enforce revision=520c21748aca
→ source             destination              refused  fallback_allow  would_refuse  body_cut  body_not_captured
→ report-dispense    copay-assist.example     0        1               0             0         0
→ report-dispense    demand-forecast.example  0        1               0             0         0
→ report-dispense    sms-gateway.example      1        0               0             0         0
→ schedule-delivery  demand-forecast.example  1        0               0             0         0
→ by reason:
→   report-dispense    copay-assist.example     fallback_allow  row source=report-dispense                                  520c21748aca  1
→   report-dispense    demand-forecast.example  fallback_allow  row source=report-dispense                                  520c21748aca  1
→   report-dispense    sms-gateway.example      fallback_deny   row source=report-dispense destination=sms-gateway.example  520c21748aca  1
→   schedule-delivery  demand-forecast.example  fallback_deny   default                                                     520c21748aca  1
→ revisions seen: 520c21748aca 4 (current 520c21748aca)
```

Per key the most specific row wins: pair > destination > source > the gate's own value. `gate-report` counts, per source
and destination, what was refused, what the fallback let through, and what observe mode would have refused, and under
it each outcome with the row (or `default`; `-` for a fault or a marked body, which no row decides) and the revision
it ran with; it reads
the sidecars' own records, so a pod roll empties it.

## 6. Observe first

Before enforcing on a real namespace, run the same table in observe mode: nothing is refused, and what would have been
is recorded.

```sh
$ DG namespace pharmacy instrument --observe --allow-open-default; settle
$ ./call.sh 6.2 schedule-delivery clean demand-forecast.example:8000
→ 6.2       schedule-delivery->demand-forecast.example:8000 clean: wire=200 delivered=True | gate deny/fallback_deny SHADOW {'no_verdict': 'verdict_error', 'error_kind': 'transport', 'waited_ms': '1', 'fail_mode_revision': '520c21748aca'}
$ DG namespace pharmacy gate-report
→ … mode=observe revision=520c21748aca
→ schedule-delivery  demand-forecast.example  0        0               1             0         0
→ by reason:
→   schedule-delivery  demand-forecast.example  fallback_deny shadow  default  520c21748aca  1
```

## 7. Reset

```sh
$ verdict 2
$ kubectl -n pharmacy delete cm dg-fail-mode
$ DG namespace pharmacy instrument --enforce --fallback deny; settle
$ ./call.sh 7.2 report-dispense clean demand-forecast.example:8000
→ 7.2       … delivered=True | gate allow/verdict_allow {'waited_ms': '180'}
$ ./call.sh 7.3 report-dispense pii demand-forecast.example:8000
→ 7.3       … delivered=False | gate deny/verdict_block {'waited_ms': '326', 'rules': 'DG-001,DG-004'}
```

To take the gate off entirely there is no `dg.sh` verb: restore the namespace's `authbridge-runtime-config` from a copy
taken before § 2 (`kubectl -n pharmacy get cm authbridge-runtime-config -o yaml > before.yaml`, then `kubectl apply -f
before.yaml` and roll the sidecars).

## 8. The whole experiment in one command

```sh
$ ./run-all.sh                     # §§ 2–7, ~25 min; one PASS/FAIL line per step, ends in § 7's state
→ === done 2026-11-10 18:55:32: 28 PASS, 0 FAIL                 (19 min)
```

One run at a time per namespace: two runs flip the same gate and each reads the other's state (the script refuses a
second run while one holds its lock). Its first line names its directory (`run=`, its `$RUN`): `summary.txt` there holds the lines, `$RUN/rows.jsonl` the evidence (the caller's reply and the sidecar's own records per
call), `$RUN/*.out` every dg.sh output. Exit code 0 only when every step matched.

## 9. When something is off

| symptom | cause | fix |
|---|---|---|
| `instrument --enforce` refuses "the gate lacks …" before writing | the sidecar image has no gate, or predates its keys (`fallback`, `fail_mode`, `sources`, `fail_mode_revision`) | § 0 step 3 from the fork's `sentry` branch (at or after s-and-p-team/cortex#187), then recreate the app's pods (`kubectl -n pharmacy delete pod --all`) |
| every call `deny/verdict_pending` near `wait_ms` | DG has not decided in time: classification is an LLM call; first calls after an idle DG are slow. Or DG's database is not answering: a read still waiting at `wait_ms` is answered `pending` too | retry; check `kubectl -n data-governance logs deploy/data-governance-verdict` and that the DG database pod is Running; raise `--wait-ms` (max 3000) |
| every call `deny/verdict_error`, `error_kind=transport` | the verdict service is down or unreachable | `kubectl -n data-governance get deploy data-governance-verdict`; § 4 is this on purpose |
| `deny/verdict_error`, `error_kind` other than `transport` | a setup fault (wrong URL, secret, TLS): never a fallback | the record's `error` field names it |
| a delivered call ends in `wire=502` | the destination does not resolve (see § 1, `sms-gateway`) | not the gate; check the host |
| pods `Pending` after a roll | node CPU full (each sidecared pod requests 200m) | scale other apps to 0 |
| DG pods crash-loop after a rebuild, migrate init fails | the database is at a migration the branch does not have | the DG database must be rebuilt (LIVE-E2E step 2) |
| `gate-report` shows nothing | the sidecars were rolled since the calls | it reads only what each sidecar still holds |

What is **not** covered by the gate today: responses (a `block` decided on a response reaches the app), streamed bodies
(`text/event-stream`), inbound requests, URL query and path, HTTPS destinations (§ 3.3).
