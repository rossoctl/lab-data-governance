# Risk

The risk sub-component computes a governance-facing risk verdict for every
**Interaction leg** as it becomes ready, rolls those verdicts up to a
per-**Trace** verdict, and serves both (plus the rule catalog and dashboard
metrics) over a `/risk/*` HTTP surface. See [`CONTEXT.md`](../../CONTEXT.md)
for the full domain vocabulary (**Interaction risk record**, **Trace risk
record**, **Leg readiness**, **Current version**, **Forest**, **Risk
retrieval**); this document covers how the sub-component runs, is configured,
and is tested.

## Architecture

```mermaid
flowchart TB
    legs[("interaction_legs")]
    payloads[("payload_classifications")]

    LEGREADY["leg_ready processor"]

    subgraph engine["risk engine"]
        EVID["evidence gathering"]
        COMP["risk computation"]
        OPAC["OPA client"]
    end

    OPA["OPA server\n(pinned openpolicyagent/opa:1.19.0)"]
    BUNDLE["opa-policy ConfigMap\n(compiled rule catalog)"]

    IPD[("interaction_policy_decisions")]
    IRR[("interaction_risk_records")]

    TRACETRIGGER["risk.trace_trigger processor"]

    TRR[("trace_risk_records")]

    subgraph api["/risk/* API (data_governance.risk.api)"]
        RROUTES["/risk/interactions*\n/risk/traces*"]
        RULES["/risk/rules*"]
        METRICS["/risk/metrics/*"]
    end

    UI["Risk UI\n(ui/src/pages/risk)"]

    legs --> LEGREADY
    payloads --> LEGREADY
    LEGREADY --> EVID --> COMP
    EVID --> legs
    EVID --> payloads
    COMP --> OPAC
    OPAC <-->|HTTP| OPA
    BUNDLE -.->|mounted at /policies| OPA
    COMP --> IPD
    COMP --> IRR
    IRR -->|pg_notify| TRACETRIGGER --> TRR

    IRR --> RROUTES
    TRR --> RROUTES
    RULES -.-> BUNDLE
    IRR --> METRICS
    TRR --> METRICS
    RROUTES --> UI
    RULES --> UI
    METRICS --> UI

    classDef component fill:#f9e79f,stroke:#b7950b,color:#000
    classDef enginebox fill:#d7bde2,stroke:#76448a,color:#000
    class LEGREADY,TRACETRIGGER,OPA,BUNDLE,UI component
    class EVID,COMP,OPAC enginebox
```

**Pipeline, in order:**

1. **Leg readiness.** `data_governance/processors/leg_ready/driver.py` drains
   `interaction_legs` in `seq` order using the contiguous-prefix readiness
   cursor: a leg is ready once it has no payload, or its payload has a
   `payload_classifications` row. Each ready leg is handed to the risk
   `Observer` outside any transaction (this is where the OPA round-trip
   happens).
2. **Evidence gathering.** `risk/engine/evidence.py` reads the interaction's
   legs, its anchor span's wire facts (`lineage.*`, `url.*` — see
   [`docs/sidecar-wire-contract.md`](../../docs/sidecar-wire-contract.md) §4
   for the attribute contract), and any payload classifications.
3. **Decision reuse-or-refresh.** `risk/engine/compute.py` fingerprints the
   evidence; if it matches the latest cached `interaction_policy_decisions`
   row, that decision is reused. Otherwise `risk/engine/utils.py` builds the
   OPA input and `risk/engine/opa.py` POSTs it to
   `RISK_OPA_BASE_URL + RISK_OPA_DECISION_PATH`, and the new decision is
   cached.
4. **Risk record write.** The compiled policy's fallback decision reports the
   sentinel rule id `"0000"` (`FALLBACK_RULE_ID`) meaning "no catalog rule
   fired"; `compute.py` strips it before storing `triggered_rule_ids`, so it
   never counts as a fired rule on a stored record. A new
   `interaction_risk_records` version is written only if the record differs
   from the latest stored version, atomically with advancing the leg-ready
   cursor.
5. **Trace-risk trigger.** Every `interaction_risk_records` insert fires a
   `pg_notify(dg_interaction_risk_written, ...)`. The
   `risk.trace_trigger` processor drains that stream by record `seq` and
   recomputes the owning trace's risk.
6. **Trace-risk compute.** `risk/engine/trace_compute.py` re-reads the
   trace's current interaction risk records, aggregates them
   (`trace_aggregate.py`), and writes a new `trace_risk_records` version —
   again, only if it differs from the latest.
7. **Serving.** `risk/api/risk_routes.py`, `rules_routes.py`, and
   `metrics_routes.py` expose the records, the rule catalog, and dashboard
   aggregates; the Risk UI (`ui/src/pages/risk/`) consumes them.

For a worked end-to-end reproduction against a live cluster and a real
upstream app, see
[`docs/RISK-E2E-REPRODUCE.md`](../../docs/RISK-E2E-REPRODUCE.md).

## Rule catalog and OPA

Rules are authored as JSON against
`risk/rules/schema/policy.schema.json` and shipped baked into the package at
`risk/rules/_policy_data/rules_source.json` — this repo only *serves* that
file (`risk/rules/catalog.py`), it does not author it. `risk/rules/rego.py`
compiles the catalog into a Rego module (one `triggered_rules contains
"<rule_id>"` block per rule, plus a `policy_decision` rule implementing the
catalog's combining semantics — `first_fires` or `most_restrictive`).

To ship a catalog change to a running cluster:

```sh
# edit data_governance/risk/rules/_policy_data/rules_source.json, then:
./deploy/create-opa-configmap.sh
kubectl -n data-governance rollout restart deployment/opa
```

This compiles the catalog, creates/updates the `opa-policy` ConfigMap (the
compiled Rego plus the raw catalog JSON), and OPA reloads it from its mounted
`/policies` volume. OPA itself runs a pinned stock image
(`docker.io/openpolicyagent/opa:1.19.0`, see
[`deploy/k8s/95-opa.yaml`](../../deploy/k8s/95-opa.yaml)) — no custom image,
no rules baked into the container.

## Running the processors

Both processors are plain Python entry points; in-cluster they run as the
`data-governance/receiver:latest` image with a `command:` override (no
dedicated image), one replica each (no inter-pod lock over the shared
cursor):

```sh
# leg-ready: gates and computes interaction-level risk
export DATABASE_URL='postgresql://user:pass@host:5432/data_governance'
python -m data_governance.processors.leg_ready

# risk.trace_trigger: recomputes trace-level risk on every new interaction record
python -m data_governance.processors.risk.trace_trigger
```

See [`deploy/k8s/85-leg-ready.yaml`](../../deploy/k8s/85-leg-ready.yaml) and
[`deploy/k8s/91-risk-trace-trigger.yaml`](../../deploy/k8s/91-risk-trace-trigger.yaml)
for the k8s manifests, and
[`deploy/k8s/README.md`](../../deploy/k8s/README.md) for the full
build/load/apply/restart procedure shared by every component in this repo.

Set `RISK_ENGINE_OBSERVER=off` to run `leg_ready` without the risk engine
wired in (readiness draining only, no OPA calls, no risk records written).

## Configuration

All risk config is env-backed module-level constants in
[`risk/config.py`](config.py) — no pydantic, no config files. The full set,
grouped by concern (defaults in parentheses):

**OPA client** (`risk/engine/opa.py`)
- `RISK_OPA_BASE_URL` (`http://opa:8181`)
- `RISK_OPA_DECISION_PATH` (`/v1/data/data_governance/policy_decision`)
- `RISK_OPA_TIMEOUT_SECONDS` (`5`)
- `RISK_OPA_MAX_RETRIES` (`2`)

**Engine wiring** (`processors/leg_ready/__main__.py`)
- `RISK_ENGINE_OBSERVER` (`on`; set `off`/`0`/`false` to disable)
- `RISK_INTERNAL_URL_WHITELIST_PATTERNS` (empty) — hostname glob patterns
  treated as internal destinations when the engine builds anchor facts

**Trace-risk trigger** (`processors/risk/trace_trigger/`)
- `RISK_TRACE_TRIGGER_CHANNEL_NAME` (`dg_interaction_risk_written`)
- `RISK_TRACE_TRIGGER_POLL_FALLBACK_INTERVAL_SECONDS` (`10`)
- `RISK_FANOUT_BATCH_SIZE` (`100`)
- `RISK_TRACE_AGGREGATION_RISK_LEVEL_MODE` (`severity_max`)
- `RISK_TRACE_AGGREGATION_ENFORCEMENT_TYPE_MODE` (`severity_max`)

**Alerting thresholds** (consumed by the alerts/metrics layer)
- `RISK_ALERT_MIN_RISK_LEVEL` (`low`)
- `RISK_ALERT_DEDUP_STRATEGY` (`time_window,risk_threshold`)
- `RISK_ALERT_DEDUP_TIME_WINDOW_MINUTES` (`5`)
- `RISK_ALERT_MAX_DAILY_ALERTS` (`1000`)
- `RISK_ALERT_VOLUME_WARNING_PCT` (`90`)
- `RISK_STORAGE_RETENTION_DAYS` (`365`)

**API pagination limits** (`risk/api/*_routes.py`) — each has a `_DEFAULT_LIMIT` /
`_MAX_LIMIT` pair: `RISK_API_RISK_RULES_*` (50/200),
`RISK_API_RISK_INTERACTIONS_*` (50/500), `RISK_API_RISK_INTERACTIONS_HISTORY_*`
(50/500), `RISK_API_RISK_TRACES_*` (50/200),
`RISK_API_RISK_TRACES_HISTORY_*` (50/200),
`RISK_API_METRICS_TOP_RULES_*` (10/50), `RISK_API_METRICS_TOP_TRACES_*` (10/50)

**Metrics refresh**
- `RISK_METRICS_REFRESH_INTERVAL_SECONDS` (`300`)

**Shared with every processor in this repo**
- `DATABASE_URL` (required), `LOG_LEVEL` (`INFO`),
  `LEG_READY_METRICS_PORT` (`9094`),
  `RISK_TRACE_TRIGGER_METRICS_PORT` (`9095`)

`tests/risk/test_config.py` covers every default and its env-override.

## Testing

```sh
# unit + integration tests, OPA-dependent tests excluded (the default)
uv run pytest

# include the OPA-dependent tests (needs a running podman/docker daemon —
# spins up a real openpolicyagent/opa container via testcontainers)
uv run pytest -m opa
```

OPA-dependent tests are marked `@pytest.mark.opa` (module-level in
`tests/risk/rules/test_rego_opa.py`) and are **deselected by default** via
`pyproject.toml`'s `addopts = "-ra -m 'not opa'"`. Run them explicitly with
`-m opa` when changing anything under `risk/rules/` (the JSON→Rego compiler,
the combining semantics, or the catalog itself) — these tests bridge the
compiler's output against a live OPA evaluating the real, shipped catalog,
which the mocked/pure-Python tests cannot verify.

Relevant test locations:
- `tests/risk/test_config.py` — config defaults and overrides
- `tests/risk/rules/test_rego_opa.py` — compiler-to-live-OPA bridge (`-m opa`)
- `tests/risk/engine/test_anchor_facts.py` — anchor-fact extraction, evidence
  gathering (no OPA needed)
- `tests/risk/engine/test_compute.py`, `test_trace_compute.py`, `test_utils.py`
  — engine unit tests
- `tests/processors/risk/trace_trigger/test_driver.py`,
  `tests/processors/leg_ready/` (via `tests/processors/leg_ready/test_risk_observer.py`)
  — processor-level driver tests
- `tests/deploy/test_risk_manifests.py`, `test_opa_manifest.py` — k8s manifest
  validation

## Database tables

| Table | Written by | Purpose |
| --- | --- | --- |
| `interaction_policy_decisions` | `risk/engine/compute.py` | Cached OPA decisions, keyed by evidence fingerprint |
| `interaction_risk_records` | `risk/engine/compute.py` | Versioned per-interaction risk verdicts |
| `trace_risk_records` | `risk/engine/trace_compute.py` | Versioned per-trace aggregated risk verdicts |
| `alerts` | (schema only in this increment) | Alert records for the dashboard/alerting surface |

All are append-only/versioned, FK-free by convention (mirrors the derived
`interactions`/`entities` tables — see `CONTEXT.md`'s **Interaction risk
record** entry). `processor_state` rows for `leg_ready` and
`risk_trace_trigger` are created on first cursor advance, not seeded by
migration.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/risk/interactions` | List current-version interaction risk records |
| `GET` | `/risk/interactions/{interaction_id}` | Current-version record for one interaction |
| `GET` | `/risk/interactions/{interaction_id}/history` | Every version, oldest-first |
| `GET` | `/risk/traces` | List current-version trace risk records |
| `GET` | `/risk/traces/{trace_id}` | The **Forest**: trace risk + nested interactions + legs |
| `GET` | `/risk/traces/{trace_id}/history` | Every trace-risk version, oldest-first |
| `GET` | `/risk/rules` | List the rule catalog |
| `GET` | `/risk/rules/categories` | Rule category counts |
| `GET` | `/risk/rules/{rule_id}` | One rule's detail |
| `GET` | `/risk/metrics/summary` | Dashboard summary |
| `GET` | `/risk/metrics/risk-distribution` | Records by risk level |
| `GET` | `/risk/metrics/enforcement-distribution` | Records by enforcement type |
| `GET` | `/risk/metrics/top-rules` | Most-frequently-triggered rules |
| `GET` | `/risk/metrics/top-traces` | Highest-risk traces |
| `GET` | `/risk/metrics/risk-by-category` | Records by rule category |

All routes are registered on the same Starlette app as `/api/*`
(`data_governance.api.build_app()`), but the import direction is one-way —
`data_governance.api` may import from `data_governance.risk.api`, never the
reverse — so the risk surface stays usable by non-UI consumers.
