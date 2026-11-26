# Sentry on DAM: every MCP tool call, by the agent's intent and the company's policy

This demo puts **Sentry** in front of a [DAM](https://github.com/dam-agents/dam) agent's MCP tool calls.
A call goes through only if both of these allow it:

- **The agent's intent: its description**, which its owner writes.
  Sentry decides each operation from it, down to what one tool does: `issue_write` closing an issue is decided apart from `issue_write` reassigning it.
- **The company security policy**, which an operator writes once, in plain language, on DAM's Operator page.
  Sentry splits it into rules about who may do what, decided by the Keycloak roles of the person who asked the agent, and rules about which data may go where, decided by data classification.

Sentry has two components, aiac (automated access control management) and dg (data governance).
Which one decides a rule follows from the rule; an agent has one switch, **Sentry**, and every reason names the agent's description or the company policy.

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

- **The switch.** The experimental per-agent setting `mcpPolicyEnforcement`, shown under the agent's **Network access** with the name the install gives it (Helm `controller.mcpPolicy.name`, here "Sentry").
  It shows only to an owner who turned on the `mcp-policy-enforcement` experimental feature.
- **What the PEP does.** The agent's gateway adds a second ext_authz in Envoy HTTP mode, on every credentialed, non-model chain, after DAM's own check and before the credential is injected.
  It sends the whole body as `POST /check/<agent id><path>`; the controller sets the agent id, which the agent cannot change.
  A 2xx response allows the request; any other status denies it, and the agent receives the reason.
- **Whom the agent acts for.** DAM's chat relay already stamps the signed-in person on every prompt.
  It also records, per agent, the people whose turns are open (a prompt opens a turn, a steer joins it, the prompt's answer closes it).
  Sentry reads that list at `GET /internal/mcp-policy/agents/<id>/acting-for`, with a token (Helm `controller.mcpPolicy.serviceTokenSecret`).
  A call is allowed only when every person on the list may make it, so the agent cannot gain anyone's permissions.
- **The company security policy.** A card on the Operator page, stored with a version; Sentry reads it at `GET /internal/mcp-policy/company-policy`.
- **The people.** Keycloak realm roles `developer` and `tester` (Helm `keycloak.extraRealmRoles`), and the users `alice` (Alice Mayer, developer) and `bob` (Bob Segal, tester); `dev` is the operator.

### Sentry's MCP policy service (this directory)

One pod in namespace `sentry-dam`, three containers:

- **`server`** (`policy-service/`) is the PDP.
  - It reads each JSON-RPC message strictly; an unreadable body to an MCP host is denied.
  - For each `tools/call` it derives the call's operations (`operations.py`, from the shape of the arguments only), reads whom the agent acts for from DAM, and asks OPA.
  - When OPA allows and the company policy has data rules, data governance judges the data (`dg_check.py`): it emits the lineage request span and polls dg's verdict, and only the dg rules the company policy maps onto can deny.
- **`opa`** evaluates `opa/mcp.rego`: the agent's standing, its description's decision for each operation, then the company policy's role rules.
- **`onboarder`** is aiac's `demo/dam/controller.py`, in the aiac repository.
  - For every enforcing agent, it decides each operation from the agent's description and the organisation policy (`demo/dam/policy.md`), with aiac's Policy Rules Builder.
  - It splits each new version of the company policy once, with one LLM pass (`demo/dam/company_policy.py`), into role rules on operations and agents, and data rules mapped onto dg's catalogue (DG-001, ...); a sentence neither kind expresses is kept as unmatched.
  - It reads the holders of the role rules' roles from Keycloak (client `sentry-policy`) on every loop, so a change of who holds a role needs no LLM.
  - While an agent or the company policy is being decided, calls are denied.

Every reason names the agent's description or the company policy, never a mechanism:

```
Denied by policy: github-mcp.issue_write.close for agent issue-workbench is denied: the policy prohibits it,
agent specifically states that it never closes issues.

Denied by policy: github-mcp.issue_write.close for agent qa-signoff, asked by alice, is denied: the company
security policy states "The QA sign-off agent is used by testers only."
```

Everything fails closed: DAM, OPA, Keycloak or data governance unreachable is a deny.

## The story

Two agents, owned by Alice, with Bob as an editor on both:

- **`issue-workbench`**: "Helps the team work on issues in the s-and-p-team demo repositories. It reads and summarizes issues, posts comments, and hands an issue back and forth between the developer and the tester with its labels, ready for testing or needs a fix. It never closes issues."
- **`qa-signoff`**: "Final QA review. It reads an issue and its history, posts the sign-off comment, and closes the issue as completed, or as not planned. It never relabels issues and never changes what an issue says."

The company security policy:

> Developers and testers may read issues, comment on them and label them. Only testers close an issue, and the QA sign-off agent is used by testers only. Personal data and restricted data must not be sent to an untrusted external destination.

Three acts, the same requests each time: Sentry off; Sentry on with an empty company policy (each agent keeps to its description); Sentry on with the company policy (Alice is refused the sign-off agent, and a sign-off that names Alice with her email is refused for its personal data).

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
   It copies dg's verdict secret and the LiteLLM key, makes the service token (restarting DAM's api-server once) and the Keycloak client `sentry-policy`, never printing any of them, and seeds `mcp-policy-data`.

5. **In DAM**, set up the connection and the agent:
   - **The connection.** Add an MCP server, `github-mcp`, for `https://api.githubcopilot.com/mcp/`.
     Under Advanced, set Header `Authorization` to `Bearer <fine-grained PAT>`.
     The PAT's owner is `s-and-p-team`, with Issues read and write on `demo-public` and `demo-private`.
   - **The agents.** Signed in as Alice, create `issue-workbench` and `qa-signoff` with the descriptions above, grant them `github-mcp`, add `bob@platform.local` to each one's People, and turn on **Sentry**.
   - **The company policy.** Signed in as `dev`, write it on the Operator page.

Use the `s-and-p-team` organisation's repositories, not personal ones.
dg's classifier treats a personal account name, such as an `owner` argument, as a person's name.
That would block every call.

Watch what happens:

```sh
kubectl -n sentry-dam logs deploy/mcp-policy -c onboarder -f   # descriptions and the company policy
kubectl -n sentry-dam logs deploy/mcp-policy -c server -f      # each decision
```

## Tests

```sh
opa test demos/dam/opa                                   # the story's decisions, as a table
~/development/sentry/aiac/.venv/bin/pytest demos/dam/tests -c /dev/null --rootdir demos/dam   # the PDP over HTTP, real OPA
```

## Files

| file | what |
|---|---|
| `policy-service/server.py` | the PDP: strict JSON-RPC, whom the agent acts for, OPA, then data governance |
| `policy-service/operations.py` | a call's operations, from the shape of its arguments |
| `policy-service/dg_check.py` | data governance: lineage request span (protobuf), verdict poll, refusal wording |
| `opa/mcp.rego`, `opa/mcp_test.rego` | the decision: description, then the company policy's role rules; its table tests |
| `opa/build_data.py`, `decisions/` | the seed of `mcp-policy-data` |
| `tests/` | the PDP through its HTTP interface |
| `k8s/policy-service.yaml` | the pod, RBAC and Service |
| `deploy.sh`, `deploy-dg.sh` | deploy the policy service, deploy dg |
| `dam-values.yaml` | DAM Helm values: where the PDP is, its name, its token, the roles and the people |
