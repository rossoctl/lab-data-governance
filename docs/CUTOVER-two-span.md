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
   migration head is 0011 (0009 legs shape + 0010 entity-ready notify +
   0011 drop of `interaction_legs.original_seq`); the processor's startup schema-version check
   refuses to run against a non-migrated DB (exit 3), which is the safety net
   if the init container was skipped.

2. **Scale the processor down and WAIT FOR THE POD TO BE GONE.** `kubectl
   scale` returns as soon as the replica count is recorded, *not* when the pod
   has terminated — and a still-running processor re-writes its
   `processor_state` row at whatever seq it has reached. Delete the cursor in
   that window and the row is silently recreated mid-reset, so the restarted
   processor resumes from that seq instead of 0 and **every span below it is
   never derived**. Observed live on 2026-07-29: the first cutover attempt left
   7 complete weather traces (133 interactions) underived, with a sharp
   derivation floor at the seq the old pod had reached. Always wait:

   ```sh
   kubectl -n data-governance scale deploy/data-governance-interactions --replicas=0
   kubectl -n data-governance wait --for=delete \
     pod -l app.kubernetes.io/name=data-governance-interactions --timeout=90s
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

7. **Assert nothing was skipped.** Row counts and a spot-checked trace do not
   catch a cursor that started above 0 — the traces it skipped are simply
   absent, and every trace you happen to look at is one that survived. This
   query must return **0**; anything else means the drain did not start from
   the beginning (see step 2), and the fix is to repeat steps 2, 4 and 5 —
   truncation is *not* required, since the derivation is an idempotent
   per-trace reconcile:

   ```sql
   WITH anchors AS (
     SELECT DISTINCT trace_id FROM spans WHERE attributes->>'lineage.role' = 'request'
   )
   SELECT count(*) FROM anchors a
    WHERE NOT EXISTS (SELECT 1 FROM interactions i WHERE i.trace_id = a.trace_id);
   ```

   A useful corollary check: `min(seq)` over spans belonging to derived traces
   should sit at the bottom of the table, not part-way up it.

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
