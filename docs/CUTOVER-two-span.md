# Cutover: selecting the sidecar interactions algorithm

The two-span sidecar derivation runs **inside** the P-interactions processor as
`INTERACTIONS_ALGORITHM=sidecar` (ADR-0028), a peer of `streaming` and `graph`
(ADR-0026). All algorithms write the same derived tables and share the one
`interactions` cursor, so switching algorithms is a *selection change plus a
data reset* — never an addition.

> **Exactly ONE algorithm runs at a time.** The processor Deployment
> (`deploy/k8s/70-interactions.yaml`) runs a single pod whose
> `INTERACTIONS_ALGORITHM` env selects the derivation; the rollout strategy
> (maxSurge:0) prevents two concurrent writers over the shared cursor.

The cutover to `sidecar` includes a **schema migration**: migration
`0009_interaction_legs` (ADR-0025) splits `interactions` into an identity row
plus `interaction_legs`, and TRUNCATEs `interactions` / `interaction_spans` as
it lands. The migrate init container applies it on rollout. The remaining
derived state and the cursor are reset by hand (below), and the algorithm
re-derives everything from the immutable `spans` table.

## Why a reset is needed

The algorithms derive the same tables by **different rules** (streaming region
repair / graph inference / sidecar wire facts), and the sidecar algorithm
assigns interaction ids as `uuid5(NS, "{trace_id}/{exchange_id}")`. Rows left
behind by another algorithm are not guaranteed to match key-for-key (graph-era
entity natural keys in particular would be stranded). The sidecar derivation
is a pure, idempotent reconcile of each trace from `spans`, so once the
derived tables are empty and the cursor is at 0 it rebuilds the correct state
in full.

## Procedure

1. **Build and roll the image from this branch.** The image's compiled
   migration head is 0009; the processor's startup schema-version check
   refuses to run against a non-migrated DB (exit 3), which is the safety net
   if the init container was skipped.

2. **Scale the processor down** so no writer is active during the reset:

   ```sh
   kubectl -n data-governance scale deploy/data-governance-interactions --replicas=0
   ```

3. **Truncate the derived tables** not already truncated by migration 0009
   (all fully re-derivable from `spans`; truncating `entities` avoids
   stranding another algorithm's natural keys):

   ```sql
   TRUNCATE entities, interactions, interaction_legs, interaction_spans,
            entity_spans, interaction_payloads;
   ```

4. **Reset the cursors** — the shared `interactions` cursor, plus the dead
   `sidecar_interactions` row if this DB predates the algorithm takeover:

   ```sql
   DELETE FROM processor_state
    WHERE processor_name IN ('interactions', 'sidecar_interactions');
   ```

5. **Select the algorithm and scale back up.** `70-interactions.yaml` pins
   `INTERACTIONS_ALGORITHM: "sidecar"`; apply it and bring the pod back:

   ```sh
   kubectl -n data-governance apply -f deploy/k8s/70-interactions.yaml
   kubectl -n data-governance scale deploy/data-governance-interactions --replicas=1
   ```

6. **Let it re-derive, then verify.** With the cursor at 0 the processor
   drains `spans` from the beginning and reconciles every trace. Check the
   log line `interactions processor using 'sidecar' algorithm`, then row
   counts > 0 in `interactions` / `interaction_legs`, and a known trace's
   numbers in the DG UI.

## What stays

- **`spans`** — the immutable source of truth; never touched by the cutover.
- **`payload_classifications`** — written once, content-addressed by payload
  content hash (P-classification's output). Orthogonal to which algorithm
  produced the payloads; **not** truncated: the same content hashes re-appear
  as the sidecar algorithm re-derives payloads, so existing classifications
  remain valid.

## Rollback

Switch `INTERACTIONS_ALGORITHM` back (`streaming` or `graph`) and repeat steps
2–5 — the reset is symmetric and every algorithm re-derives from `spans`.
Migration 0009 stays either way (both other algorithms write the legs schema
through `state.flush`).
