# Upgrade the combined main and risk schema

PR #338 preserves main's shipped `0020_entity_namespace` ancestry and joins the
risk-table branch at `0023_trace_risk_seq`. Revision `0027_merge_main_and_risk`
merges the two branches; `0028_correction_replay` adds independent label and risk
correction queues. The combined graph has one head: `0028_correction_replay`.

Run these commands from the repository root with `DATABASE_URL` set to the target
database. Stop the receiver and all processor writers, including any concurrent
migration init containers, for the upgrade. Take the deployment's normal database
backup before changing its schema. Restart writers using the combined application
version only after the migration succeeds.

| Existing schema | Upgrade procedure |
| --- | --- |
| Fresh database | Run the normal migration CLI below. |
| Main at `0022_content_kind_on_legs` or `0026_causal_leg_order` | Run the normal CLI; it installs the missing risk branch. |
| Risk at `0024_label_lineage` | Run the normal CLI; it installs main's causal-order migration and joins the heads. |
| Historical risk at shared `0020_entity_namespace` or `0022_content_kind_on_legs` | Run the schema-checked adoption procedure below, then the normal CLI. |

The same shared revision stamp can describe different historical schemas. A main
database at `0020` or `0022` has no risk-table branch; a historical risk database at
those revisions already executed `0016` through `0019`. Adding only a merge
revision would not distinguish them.

For an existing database, inspect its stamp before choosing the procedure:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM public.alembic_version ORDER BY version_num'
```

The normal upgrade is:

```bash
.venv/bin/python -m data_governance.db.migrate
```

For **historical risk `0020` or `0022` only**, first adopt the branch that is already
present:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f tools/adopt_historical_risk_history.sql
.venv/bin/python -m data_governance.db.migrate
```

The adoption script checks the shipped risk columns, types, nullability, defaults,
keys, indexes, sequence ownership and INSERT notification trigger before adding
the `0019_interaction_risk_seq` stamp. It runs in a transaction and changes only
version bookkeeping. A missing or incompatible risk schema, a main-only schema,
or another revision is rejected without changing the stamp. Re-running adoption
at its supported two-head intermediate stamp is safe; once the normal upgrade
finishes, use the normal CLI for repeat invocations.

If adoption fails, inspect and repair the specific schema discrepancy before
retrying. Do not stamp `head` to bypass it: that would hide unapplied DDL from
Alembic. No table truncation, cursor reset or evidence deletion is required.

After a successful upgrade, this should return exactly `0028_correction_replay`:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM public.alembic_version ORDER BY version_num'
```

The migration preserves spans, payload content, alerts and immutable risk record
history. Main's `0026` corrects existing inferred-tool occurrence times and queues
data-lineage replay. `0028` independently queues existing label traces,
interactions with legs, and traces with risk history. Restarting the data-lineage,
label-lineage, leg-ready and trace-risk-trigger processors drains these durable
backfills without changing their ingestion cursors. Recomputed decisions and
rollups append versions when their current evidence or result changes.

With `RISK_INHERITED_LABELS=off`, leg-ready still recomputes structural corrections
to parents, endpoints, trace membership and legs. Label-only requests remain
pending for a later run with the flag on; fulfilling a structural request keeps
any pending label obligation. With the flag on, risk waits for classifications,
label rows matching the current trace and payload, and completion of that trace's
label correction replay. Label-lineage runs and retains its own progress in both
modes.

The correction queues are `data_lineage_dirty_traces`,
`label_lineage_dirty_traces`, `risk_recompute_requests`, and
`trace_risk_recompute_requests`. Monitor processor logs and queue counts during
replay. A label-only risk request with `base_recompute = false` and
`labels_pending = true` is expected to remain while inherited labels are off.
Each queue acknowledges only the generation it processed, so a correction arriving
during replay remains durable for the next pass.

The upgrade tests in `tests/db/test_main_risk_merge_upgrade.py` execute the
historical branches' DDL, upgrade fresh/main `0022`/main `0026`/risk `0024` databases,
and exercise historical-risk adoption and rejection. They check preserved evidence
and required tables, triggers and sequence ownership as well as the final stamp.
