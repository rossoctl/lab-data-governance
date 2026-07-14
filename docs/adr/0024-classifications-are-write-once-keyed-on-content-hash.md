# Classifications are write-once, keyed on content_hash, with no finalization

A **Classification** is stored one row per **Payload** in
`payload_classifications`, keyed on `content_hash`, and is **write-once**: it is
inserted when the payload's row arrives in the stream and never mutated
afterward. There is no `seq`-bump / finalization / in-place-mutation model —
unlike the sibling `interactions` and `entities` tables, which mutate in place
as late spans arrive (ADR-0007, ADR-0012). Each row carries a monotonic integer
`model_version` (starts at 1) as a future hook; automatic re-classification on a
version change is **not** implemented — a model upgrade is handled
operationally.

## Why

- **The payload it classifies is immutable, so the classification can be too.**
  `P-interactions` writes `interaction_payloads` with `ON CONFLICT
  (content_hash) DO NOTHING`: a payload row is content-addressed and written
  exactly once, never updated. Its bytes are frozen at insert, so the verdict
  over those bytes has no eventual-consistency story to track. This makes
  `P-classification` dramatically simpler than `P-interactions`: no lineage
  rehydration, no territory delete+reinsert, no mutation `seq` semantics — a
  straight map of payload row → classify → insert (`ON CONFLICT DO NOTHING`) →
  advance cursor.
- **Content addressing gives dedup for free.** One classification per
  `content_hash` means a body referenced by many interactions or traces is
  classified exactly once.
- **No finalization analogue is needed.** Spans carry both `seq` and
  `arrival_seq` and the interactions driver runs a finalization tripwire
  because spans finalize (ADR-0004). Payloads never do, so
  `interaction_payloads` gets a single `seq` column (its cursorable stream) and
  the shared driver's finalization tripwire stays an interactions-only concern.

## Considered alternatives

- **Per-row `model_version` + `config_version` with automatic
  re-classification of stale rows.** Rejected for the first increment: it needs
  either a stale-row scan or a version-aware cursor, and the
  reclassify-on-upgrade case is rare and operational, not a per-payload
  streaming concern. The `model_version` column is added now as a cheap hook
  (monotonic integer, so generations are comparable) so this is a later
  extension, not a schema break.

## Consequences

- A model/config upgrade re-classifies everything via an operational runbook —
  truncate `payload_classifications`, reset the `P-classification`
  `processor_state` cursor to 0, let the processor re-drain — mirroring the
  existing span-DB-wipe procedure. There is no incremental re-classification.
- The nullable `classification` field on `GET /api/payloads/{hash}` means
  *exactly* "not yet processed": every payload is classified uniformly (no
  per-kind skipping in this increment), and a payload with no sensitive text
  gets a real `PUBLIC` / zero-findings verdict, never a null. A future
  exclude-filter that skips selected kinds would change this and is deferred.
