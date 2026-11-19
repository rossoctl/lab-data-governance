# Sentry on DAM: aiac and dg on an agent's MCP tool calls

This demo puts **Sentry** in front of a [DAM](https://github.com/dam-agents/dam) agent's MCP tool calls.

Sentry has two components:

- **aiac (automated access control).** It decides which tools an agent may use, from the agent's own description and the organisation's policy.
- **dg (data governance).** It decides which data may go where, from Sentry's classifier and risk rules.

Each component is on or off per agent, live, with switches in the agent's DAM settings.

## Why this exists

DAM's agent gateway asks the platform's own `ext_authz` check about every request.
That check sees only the host, method and path.
Every call to a remote MCP server is the same request, for example `POST api.githubcopilot.com /mcp/`, whether it lists issues or closes one, and the check never sees the data in it (DAM #3323).

## How it fits together

```
agent ─▶ DAM gateway ─▶ DAM ext_authz (owner's rules, holds)
                     ─▶ MCP policy enforcement point (PEP) ──▶ Sentry's MCP policy service (PDP)
                     ─▶ credential injection ─▶ api.githubcopilot.com      ├─ aiac: OPA, decisions from the description
                                                                           └─ dg: lineage span ─▶ dg verdict
```

### DAM side (branch `sentry-dam` of DAM, local only)

- **The setting.** The experimental per-agent setting `mcpPolicyEnforcement`, the switch "MCP policy enforcement" under the agent's **Network access**.
  It shows only to an owner who turned on the `mcp-policy-enforcement` experimental feature.
- **What the PEP does.** The agent's gateway adds a second ext_authz in Envoy HTTP mode.
  It runs on every credentialed, non-model chain, after DAM's own check and before the credential is injected.
- **The check switches.** While enforcement is on, the card shows one switch per check that the install declares (Helm `controller.mcpPolicy.checks`).
  Here those are **Automated access control (aiac)** and **Data governance (dg)**.
  Each is on until the owner turns it off (Agent spec `mcpPolicyDisabledChecks`).
  DAM gives the ids no meaning of its own.
- **What the PDP receives.** The whole body, as `POST /check/<agent id>/<aiac,dg|none><path>`.
  The controller sets the agent id and the checks that apply, and the agent cannot change either.
- **The answer.** A 2xx response allows the request.
  Any other status denies it, and the agent receives the reason.

### Sentry's MCP policy service (this directory)

It is one pod in namespace `sentry-dam`, with three containers:

- **`server`** (`policy-service/server.py`, `policy-service/dg_check.py`) is the PDP.
  - It reads each JSON-RPC message strictly. An unreadable body to an MCP host is denied.
  - For each `tools/call`, it asks **aiac** (OPA) and then **dg**. The first deny wins.
  - Handshake messages pass.
- **`opa`** evaluates `opa/mcp.rego` over aiac's decisions.
- **`onboarder`** is aiac's `demo/dam/controller.py`, in the aiac repository.
  - It watches DAM's Agent resources.
  - For every enforcing agent with aiac on, it derives a decision per GitHub MCP tool from the agent's description and the policy (`demo/dam/policy.md`), using aiac's Policy Rules Builder.
  - It re-onboards when the description changes. While an agent is pending, its tool calls are denied.

**dg** is Sentry's data governance stack in namespace `data-governance`: Postgres, receiver, interactions, classification, leg-ready, OPA and verdict.
For each tool call that aiac allows, the PDP does two things, as the Cortex `lineage-telemetry` and `sentry-gate` plugins would:

1. It emits the lineage wire contract's request span (v1.8.1, OTLP protobuf), with the call's arguments.
2. It long-polls the verdict.

A `block` verdict, from shipped rules DG-001 and DG-004, denies the call.

Every reason names the policy and the data or the agent's own words, never a component:

```
Denied by policy: github-mcp.issue_write for agent issue-triage-test is denied: the policy prohibits it,
agent specifically states that it reports on issues and never changes repositories or issues.

Denied by policy: github-mcp.search_issues for agent issue-triage-test is denied: the policy prohibits
sending personal data and restricted data to api.githubcopilot.com, an untrusted external destination,
and the request carries a government ID or a person's name: {"query": "[person.govID] [person.govID]", ...}
```

Everything fails closed. A component that cannot be reached denies.

## Turn components on and off

1. Open the agent's settings, then **Network access**.
2. Under **MCP policy enforcement**, switch **Automated access control (aiac)** or **Data governance (dg)**.

Each change restarts the agent's network access for 5 to 15 seconds.
With aiac off, any tool is allowed, subject to dg.
With dg off, no data is checked.
With aiac off, the onboarder stops deriving decisions for the agent.

## Deploy on a local DAM install

1. **DAM with the PEP.** In the DAM checkout on branch `sentry-dam`, run:

   ```sh
   mise run cluster:install -- --helm-values ~/development/sentry/lab-data-governance/demos/dam/dam-values.yaml
   ```

2. **dg.** Build the images, import them into k3s, and deploy:

   ```sh
   uv lock && docker build -f Containerfile -t docker.io/data-governance/receiver:latest .
   docker build -f Containerfile.classification -t docker.io/data-governance/classification:latest .
   docker save docker.io/data-governance/receiver:latest -o /tmp/r.tar
   docker save docker.io/data-governance/classification:latest -o /tmp/c.tar
   (cd ~/development/dam && mise run cluster:import -- /tmp/r.tar /tmp/c.tar)
   demos/dam/deploy-dg.sh
   ```

3. **aiac image.** Build it in the aiac checkout and import it:

   ```sh
   docker build -f src/aiac/agent/controller/Dockerfile -t localhost/aiac-agent:local src/
   docker save localhost/aiac-agent:local -o /tmp/a.tar
   (cd ~/development/dam && mise run cluster:import -- /tmp/a.tar)
   ```

4. **The policy service.** Run `demos/dam/deploy.sh`.
   It copies dg's verdict secret and the LiteLLM key, never printing them, and seeds `mcp-policy-config` and `mcp-policy-data`.

5. **In DAM**, set up the connection and the agent:
   - **The connection.** Add an MCP server, `github-mcp`, for `https://api.githubcopilot.com/mcp/`.
     Under Advanced, set Header `Authorization` to `Bearer <fine-grained PAT>`.
     The PAT's owner is `s-and-p-team`, with Issues read and write on `demo-public` and `demo-private`.
   - **The agent.** Create an agent with a description, grant it `github-mcp`, and turn on **MCP policy enforcement**.

Use the `s-and-p-team` organisation's repositories, not personal ones.
dg's classifier treats a personal account name, such as an `owner` argument, as a person's name.
That would block every call.

Watch what happens:

```sh
kubectl -n sentry-dam logs deploy/mcp-policy -c onboarder -f   # aiac onboarding
kubectl -n sentry-dam logs deploy/mcp-policy -c server -f      # each decision
```

## Files

| file | what |
|---|---|
| `policy-service/server.py` | the PDP: strict JSON-RPC, components config, aiac then dg |
| `policy-service/dg_check.py` | dg: lineage request span (protobuf), verdict poll, refusal wording |
| `opa/mcp.rego` | aiac's decisions as an OPA policy |
| `opa/build_data.py`, `decisions/` | the seed of `mcp-policy-data` |
| `k8s/policy-service.yaml` | the pod, RBAC and Service |
| `deploy.sh`, `deploy-dg.sh` | deploy the policy service, deploy dg |
| `dam-values.yaml` | DAM Helm values: where the PDP is, its checks (aiac, dg), and gateway egress to it |
